"""
PDFPaperTranslator Web Server — 基于 Flask 的本地 Web 翻译界面

启动方式:
    python web_server.py
    python web_server.py --port 8080
    python web_server.py --no-browser  # 不自动打开浏览器

启动后在浏览器中上传 PDF 文件进行翻译，
支持多文件队列翻译，实时查看进度，翻译完成后下载译文。
"""

import argparse
import json
import os
import queue
import shutil
import sys
import tempfile
import threading
import time
import uuid
import webbrowser
from typing import Optional

from flask import Flask, Response, jsonify, render_template, request, send_file

# 直接执行 web_server.py 时需要父目录在 sys.path 中
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PARENT_DIR = os.path.dirname(SCRIPT_DIR)
if PARENT_DIR not in sys.path:
    sys.path.insert(0, PARENT_DIR)

from PDFPaperTranslator import _constants as const
from PDFPaperTranslator import config as cfg
from PDFPaperTranslator.translation.api_client import DebugLogger
from PDFPaperTranslator.translation.term_dict import merge_term_dicts

# ---- 常量 ----
UPLOAD_DIR = os.path.join(SCRIPT_DIR, "uploads")
MAX_FILE_SIZE = 100 * 1024 * 1024  # 100 MB

# ---- Flask 应用 ----
app = Flask(__name__, template_folder=os.path.join(SCRIPT_DIR, "templates"))


def _clear_uploads():
    """清理上传目录"""
    if os.path.exists(UPLOAD_DIR):
        shutil.rmtree(UPLOAD_DIR)
    os.makedirs(UPLOAD_DIR)


def _make_output_path(input_filename: str) -> str:
    """生成译文输出路径: {译文}原文件名前15字.pdf，重名时追加序号"""
    base = os.path.splitext(input_filename)[0].strip()
    if len(base) > 15:
        base = base[:15]
    path = os.path.join(UPLOAD_DIR, f"{{译文}}{base}.pdf")
    if not os.path.exists(path):
        return path
    counter = 1
    while True:
        path = os.path.join(UPLOAD_DIR, f"{{译文}}{base}({counter}).pdf")
        if not os.path.exists(path):
            return path
        counter += 1


# ============================================================
# 翻译队列管理器
# ============================================================

class TranslationQueue:
    """
    多文件 PDF 翻译队列，逐个翻译每个文件。
    每个批次包含多个任务，按 FIFO 顺序处理。

    线程模型：
    - 一个后台 daemon 线程 (_worker) 处理所有批次的作业
    - 多个 Flask 请求线程（threaded=True）读取进度/下载
    - _lock 保护 _pending、_jobs、_batch_queues、_batch_terms、_batch_debug_loggers
    - _batch_queues.pop() 在 SSE 生成器中持有 _lock（与 worker 的 list(items()) 互斥）
    - 文件写入（debug log）在 _lock 外进行，不阻塞其他操作
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._jobs: dict = {}          # job_id → job dict
        self._pending: list = []       # pending job_ids (FIFO)
        self._current_job: Optional[str] = None
        self._worker_running = False
        self._batch_queues: dict = {}  # batch_id → queue.Queue (for SSE)
        self._batch_terms: dict = {}   # batch_id → shared term dict
        self._batch_debug_loggers: dict = {}  # batch_id → DebugLogger

    def create_batch(self, files_data: list, api_key: str, model: str,
                     source_lang: str, target_lang: str,
                     initial_terms: dict = None) -> str:
        """创建批次，将所有 PDF 加入翻译队列"""
        batch_id = uuid.uuid4().hex[:8]
        bq = queue.Queue()
        self._batch_queues[batch_id] = bq

        # 始终初始化术语词典（即使为空，后续翻译中也会累积）
        self._batch_terms[batch_id] = dict(initial_terms) if initial_terms else {}

        # 为该批次创建调试日志记录器
        batch_logger = DebugLogger()
        self._batch_debug_loggers[batch_id] = batch_logger

        job_list = []
        for fd in files_data:
            job_id = uuid.uuid4().hex[:8]
            job = {
                "job_id": job_id,
                "batch_id": batch_id,
                "filename": fd["filename"],
                "input_path": fd["input_path"],
                "output_path": fd["output_path"],
                "output_filename": fd["output_filename"],
                "api_key": api_key,
                "model": model,
                "source_lang": source_lang,
                "target_lang": target_lang,
                "status": "pending",
                "error": None,
                "pages_to_skip": fd.get("pages_to_skip", []),
                "page_annotations": fd.get("page_annotations", {}),
            }
            self._jobs[job_id] = job
            job_list.append(job)

        with self._lock:
            for j in job_list:
                self._pending.append(j["job_id"])

        bq.put({"type": "batch_start", "batch_id": batch_id,
                "total": len(job_list),
                "jobs": [self._job_summary(j) for j in job_list]})

        self._ensure_worker()
        return batch_id

    def _job_summary(self, job: dict) -> dict:
        return {
            "job_id": job["job_id"],
            "filename": job["filename"],
            "output_filename": job["output_filename"],
            "status": job["status"],
            "error": job["error"],
        }

    def get_batch_queue(self, batch_id: str):
        return self._batch_queues.get(batch_id)

    def find_job_by_output(self, output_filename: str) -> dict | None:
        for job in self._jobs.values():
            if job.get("output_filename") == output_filename:
                return job
        return None

    def _ensure_worker(self):
        with self._lock:
            if not self._worker_running:
                self._worker_running = True
                t = threading.Thread(target=self._worker, daemon=True)
                t.start()

    def _worker(self):
        """后台线程：逐个处理队列中的 PDF 翻译任务"""
        while True:
            with self._lock:
                if not self._pending:
                    self._worker_running = False
                    self._current_job = None
                    break
                job_id = self._pending.pop(0)
                self._current_job = job_id

            with self._lock:
                job = self._jobs.get(job_id)
            if job is None:
                self._current_job = None
                continue

            job["status"] = "translating"
            with self._lock:
                bq = self._batch_queues.get(job["batch_id"])
            if bq:
                bq.put({"type": "job_start", "job_id": job_id,
                        "filename": job["filename"]})

            def _on_progress(msg: str):
                if bq:
                    bq.put({"type": "job_progress", "job_id": job_id,
                            "msg": str(msg)})

            try:
                _on_progress(f"开始翻译: {job['filename']}")

                # 获取该批次的共享术语词典
                batch_id = job["batch_id"]
                with self._lock:
                    shared_terms = self._batch_terms.get(batch_id, {})
                if shared_terms:
                    _on_progress(f"继承已有术语词典 ({len(shared_terms)} 词)")

                # 执行 PDF 翻译流水线
                batch_logger = self._batch_debug_loggers.get(batch_id)
                result = _run_pdf_translation(
                    input_path=job["input_path"],
                    output_path=job["output_path"],
                    api_key=job["api_key"],
                    model=job["model"],
                    source_lang=job["source_lang"],
                    target_lang=job["target_lang"],
                    progress_callback=_on_progress,
                    initial_term_dict=shared_terms if shared_terms else None,
                    debug_logger=batch_logger,
                    pages_to_skip=set(job.get("pages_to_skip", [])),
                    page_annotations=job.get("page_annotations", {}),
                )

                # 合并新术语到共享词典
                if result and result.get("term_dict"):
                    with self._lock:
                        shared_terms.update(result["term_dict"])
                        self._batch_terms[batch_id] = shared_terms
                    new_count = len(result["term_dict"])
                    if new_count > 0:
                        _on_progress(f"本文件新发现 {new_count} 个术语，"
                                     f"共享词典累计 {len(shared_terms)} 词")

                job["status"] = "done"
                if bq:
                    bq.put({"type": "job_done", "job_id": job_id,
                            "filename": job["filename"],
                            "output_filename": job["output_filename"]})
            except Exception as e:
                job["status"] = "error"
                job["error"] = str(e)
                if bq:
                    bq.put({"type": "job_error", "job_id": job_id,
                            "filename": job["filename"],
                            "error": str(e)})
            finally:
                with self._lock:
                    self._current_job = None

        # 所有任务处理完毕。先快照批队列表（持锁），再逐个处理（可放锁）。
        # 注意：此时 _worker_running 尚未重置，_ensure_worker() 可能已在 create_batch
        # 中启动新 worker。旧 worker 完成清理后自然退出，新 worker 接管新批次。
        with self._lock:
            batches = list(self._batch_queues.items())
        for batch_id, bq in batches:
            with self._lock:
                terms = self._batch_terms.get(batch_id, {})
                debug_logger = self._batch_debug_loggers.get(batch_id)
            # 写入调试日志文件（始终写入，即使为空也生成文件供下载验证）
            log_path = os.path.join(UPLOAD_DIR, f"debug_{batch_id}.log")
            if debug_logger is not None:
                try:
                    debug_logger.write_to_file(log_path)
                    has_debug_log = True
                except Exception as e:
                    print(f"[警告] 写入调试日志失败: {e}")
                    has_debug_log = False
            else:
                try:
                    with open(log_path, "w", encoding="utf-8") as f:
                        f.write("")
                    has_debug_log = True
                except Exception:
                    has_debug_log = False

            # 人工标注模式的页面标签元数据
            for job_id in list(self._jobs.keys()):
                job = self._jobs.get(job_id, {})
                if job.get("batch_id") == batch_id:
                    page_ann = job.get("page_annotations", {})
                    if page_ann:
                        with open(log_path, "a", encoding="utf-8") as f:
                            f.write("\n")
                            for pn_str in sorted(page_ann.keys(), key=int):
                                pn = int(pn_str)
                                ann = page_ann[pn_str]
                                mode = ann.get("mode", "auto")
                                entry = {
                                    "type": "page_annotation",
                                    "page": pn + 1,  # 1-based for readability
                                    "mode": mode,
                                    "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                                }
                                if mode == "manual":
                                    regions = ann.get("regions", [])
                                    entry["regions"] = regions
                                    entry["region_count"] = len(regions)
                                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
                        break  # 只处理本批次的第一个作业（单文件）
            bq.put({"type": "batch_done", "batch_id": batch_id,
                    "term_count": len(terms),
                    "done_count": sum(1 for j in self._jobs.values()
                                      if j.get("batch_id") == batch_id and j.get("status") == "done"),
                    "error_count": sum(1 for j in self._jobs.values()
                                       if j.get("batch_id") == batch_id and j.get("status") == "error"),
                    "has_debug_log": has_debug_log})


def _run_pdf_translation(input_path: str, output_path: str,
                         api_key: str, model: str,
                         source_lang: str, target_lang: str,
                         progress_callback=None,
                         initial_term_dict: dict = None,
                         debug_logger=None,
                         pages_to_skip: set = None,
                         page_annotations: dict = None) -> dict:
    """执行完整的 PDF 翻译流水线（委托共享模块）"""
    from PDFPaperTranslator.pipeline import run_translation_pipeline

    return run_translation_pipeline(
        pdf_path=input_path,
        output_path=output_path,
        api_key=api_key,
        model=model,
        source_lang=source_lang,
        target_lang=target_lang,
        progress_callback=progress_callback,
        initial_term_dict=initial_term_dict,
        debug_logger=debug_logger,
        pages_to_skip=pages_to_skip,
        page_annotations=page_annotations,
    )


# 全局队列实例
translation_queue = TranslationQueue()


# ============================================================
# 路由：页面
# ============================================================

@app.route("/")
def index():
    """渲染主页面"""
    return render_template("index.html")


# ============================================================
# 路由：配置 API
# ============================================================

@app.route("/api/config", methods=["GET"])
def get_config():
    """获取已保存的配置（API Key 脱敏）"""
    config = cfg.load_config()
    api_key = config.get("api_key") or ""
    klen = len(api_key)
    if klen > 8:
        masked = api_key[:4] + "*" * (klen - 8) + api_key[-4:]
    elif klen >= 4:
        masked = api_key[:2] + "*" * (klen - 4) + api_key[-2:]
    elif klen > 0:
        masked = api_key[0] + "*" * (klen - 1)
    else:
        masked = ""

    model = config.get("model", const.MODEL_DEFAULT)
    model_name = dict(
        (v[0], v[1]) for v in const.MODEL_OPTIONS.values()
    ).get(model, model)

    return jsonify({
        "api_key_masked": masked,
        "has_api_key": bool(api_key),
        "model": model,
        "model_name": model_name,
        "model_options": [
            {"id": v[0], "name": v[1]}
            for v in const.MODEL_OPTIONS.values()
        ],
    })


@app.route("/api/config", methods=["POST"])
def save_config():
    """保存 API Key 和模型选择"""
    data = request.get_json(silent=True) or {}
    api_key = (data.get("api_key") or "").strip()
    model = (data.get("model") or "").strip()

    if not api_key and not model:
        return jsonify({"ok": False, "error": "没有需要保存的配置"}), 400

    if not api_key:
        saved = cfg.load_config()
        api_key = saved.get("api_key") or ""

    valid_models = [v[0] for v in const.MODEL_OPTIONS.values()]
    if model and model not in valid_models:
        return jsonify({"ok": False, "error": f"无效的模型: {model}"}), 400

    if not model:
        model = const.MODEL_DEFAULT

    cfg.save_config(api_key, model)
    return jsonify({"ok": True})


# ============================================================
# 路由：翻译（多文件队列）
# ============================================================

@app.route("/api/translate", methods=["POST"])
def start_translation():
    """接收一个或多个 PDF 文件，加入翻译队列，返回 batch_id"""
    config = cfg.load_config()
    api_key = config.get("api_key")
    if not api_key:
        return jsonify({"ok": False, "error": "请先在设置中配置 DeepSeek API Key"}), 400

    model = config.get("model", const.MODEL_DEFAULT)
    source_lang = request.form.get("source_lang", const.DEFAULT_SOURCE_LANG)
    target_lang = request.form.get("target_lang", const.DEFAULT_TARGET_LANG)

    # 收集所有上传的文件
    uploaded = request.files.getlist("files")
    if not uploaded:
        single = request.files.get("file")
        if single and single.filename:
            uploaded = [single]
        else:
            return jsonify({"ok": False, "error": "未上传文件"}), 400

    # 解析跳过页面设置: {"filename": [1,3,5]} (1-based → 0-based)
    pages_to_skip_raw = request.form.get('pages_to_skip', '{}')
    try:
        pages_to_skip_map = json.loads(pages_to_skip_raw) if pages_to_skip_raw else {}
        # 1-based → 0-based
        pages_to_skip_map = {k: [p - 1 for p in v] for k, v in pages_to_skip_map.items()}
    except (json.JSONDecodeError, TypeError, ValueError):
        pages_to_skip_map = {}

    files_data = []
    errors = []

    for f in uploaded:
        if not f.filename:
            continue

        ext = os.path.splitext(f.filename)[1].lower()
        if ext != ".pdf":
            errors.append(f"{f.filename}: 不支持的格式 {ext}，仅支持 PDF")
            continue

        # 检查文件大小
        f.seek(0, os.SEEK_END)
        size = f.tell()
        f.seek(0)
        if size > MAX_FILE_SIZE:
            errors.append(f"{f.filename}: 文件过大 ({size / 1024 / 1024:.1f} MB)")
            continue

        safe_name = f.filename.replace("\\", "/").split("/")[-1]
        input_path = os.path.join(UPLOAD_DIR, f"{uuid.uuid4().hex[:8]}_{safe_name}")
        f.save(input_path)

        output_path = _make_output_path(safe_name)

        skip_pages = pages_to_skip_map.get(safe_name, [])
        files_data.append({
            "filename": safe_name,
            "input_path": input_path,
            "output_path": output_path,
            "output_filename": os.path.basename(output_path),
            "pages_to_skip": skip_pages,
        })

    if not files_data:
        return jsonify({"ok": False, "error": "没有有效的 PDF 文件可翻译" + (
            "; " + "; ".join(errors) if errors else "")}), 400

    # 读取可选的术语词典导入（支持多文件自动合并）
    initial_terms = {}
    termdict_info = None  # 合并统计信息
    termdict_files = request.files.getlist("termdict")
    valid_termdicts = [
        (f.filename, f.read()) for f in termdict_files
        if f and f.filename and f.filename.endswith(".json")
    ]
    if valid_termdicts:
        from PDFPaperTranslator.translation.term_dict import merge_term_dicts
        merge_result = merge_term_dicts(valid_termdicts)
        if merge_result.get("ok"):
            initial_terms = merge_result["terms"]
            termdict_info = {
                "files": len(valid_termdicts),
                "parsed": merge_result["stats"]["files_parsed"],
                "failed": merge_result["stats"]["files_failed"],
                "raw_terms": merge_result["stats"]["total_raw_terms"],
                "unique_terms": merge_result["stats"]["unique_terms"],
                "conflicts": merge_result["stats"]["conflict_count"],
                "conflict_details": merge_result.get("conflicts", []),
            }
        else:
            termdict_info = {"error": merge_result.get("error", "未知错误")}

    batch_id = translation_queue.create_batch(
        files_data=files_data,
        api_key=api_key,
        model=model,
        source_lang=source_lang,
        target_lang=target_lang,
        initial_terms=initial_terms,
    )

    all_warnings = list(errors)
    if termdict_info and termdict_info.get("error"):
        all_warnings.append(f"术语词典: {termdict_info['error']}")

    return jsonify({
        "ok": True,
        "batch_id": batch_id,
        "total": len(files_data),
        "warnings": all_warnings if all_warnings else None,
        "termdict_info": termdict_info,
    })


# ============================================================
# 路由：SSE 进度推送
# ============================================================

@app.route("/api/progress/<batch_id>")
def progress_stream(batch_id):
    """Server-Sent Events 端点，推送整个批次的翻译进度"""
    bq = translation_queue.get_batch_queue(batch_id)
    if bq is None:
        return Response(
            f"data: {json.dumps({'type': 'error', 'msg': '批次不存在'})}\n\n",
            mimetype="text/event-stream",
        )

    def generate():
        while True:
            try:
                event = bq.get(timeout=30)
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                if event["type"] in ("batch_done",):
                    time.sleep(2)
                    break
            except queue.Empty:
                yield f"data: {json.dumps({'type': 'heartbeat'})}\n\n"

        with translation_queue._lock:
            translation_queue._batch_queues.pop(batch_id, None)

    return Response(generate(), mimetype="text/event-stream")


# ============================================================
# 路由：下载
# ============================================================

@app.route("/api/download/<filename>")
def download_file(filename):
    """下载翻译完成的 PDF 文件"""
    safe_name = filename.replace("\\", "/").split("/")[-1]
    file_path = os.path.join(UPLOAD_DIR, safe_name)

    if not os.path.exists(file_path):
        return jsonify({"ok": False, "error": "文件不存在或已被清理"}), 404

    job = translation_queue.find_job_by_output(safe_name)
    input_path = job.get("input_path") if job else None

    def _cleanup():
        time.sleep(1)
        try:
            if input_path and os.path.exists(input_path):
                os.remove(input_path)
        except OSError:
            pass
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
        except OSError:
            pass

    response = send_file(
        file_path,
        as_attachment=True,
        download_name=safe_name,
        mimetype="application/pdf",
    )
    response.call_on_close(_cleanup)
    return response


# ============================================================
# 路由：术语词典下载
# ============================================================

@app.route("/api/termdict/<batch_id>")
def download_termdict(batch_id):
    """下载批次的共享术语词典（JSON 格式）"""
    terms = translation_queue._batch_terms.get(batch_id)
    if terms is None:
        return jsonify({"ok": False, "error": "批次不存在或尚未完成"}), 404

    import io
    from datetime import datetime

    meta = dict(const.TERMDICT_META)
    meta["created"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    data = {"_meta": meta, "terms": terms}
    json_str = json.dumps(data, ensure_ascii=False, indent=2)
    return send_file(
        io.BytesIO(json_str.encode("utf-8")),
        mimetype="application/json",
        as_attachment=True,
        download_name="翻译术语.json",
    )


# ============================================================
# 路由：批量下载 ZIP
# ============================================================

@app.route("/api/download_batch/<batch_id>")
def download_batch_zip(batch_id):
    """将批次中所有已完成的译文打包为 ZIP 下载"""
    import io
    import zipfile
    from datetime import datetime

    with translation_queue._lock:
        batch_jobs = [j for j in translation_queue._jobs.values()
                      if j.get("batch_id") == batch_id and j.get("status") == "done"]

    if not batch_jobs:
        return jsonify({"ok": False, "error": "该批次没有已完成的翻译"}), 404

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for job in batch_jobs:
            output_path = job.get("output_path")
            if output_path and os.path.exists(output_path):
                zf.write(output_path, job.get("output_filename"))

    buf.seek(0)

    now = datetime.now()
    zip_name = (f"【PDF译文】"
                f"{now.year}年{now.month:02d}月{now.day:02d}日"
                f"{now.hour:02d}.{now.minute:02d}.zip")

    return send_file(
        buf,
        mimetype="application/zip",
        as_attachment=True,
        download_name=zip_name,
    )


# ============================================================
# 路由：调试日志下载
# ============================================================

@app.route("/api/debug/<batch_id>")
def download_debug_log(batch_id):
    """下载批次的 API 调试日志（JSON 行格式，每行一次请求）"""
    log_path = os.path.join(UPLOAD_DIR, f"debug_{batch_id}.log")
    if not os.path.exists(log_path):
        return jsonify({"ok": False, "error": "调试日志不存在（批次可能尚未完成或未启用）"}), 404

    def _cleanup():
        time.sleep(2)
        try:
            if os.path.exists(log_path):
                os.remove(log_path)
        except OSError:
            pass

    response = send_file(
        log_path,
        as_attachment=True,
        download_name=f"debug_{batch_id}.log",
        mimetype="text/plain; charset=utf-8",
    )
    response.call_on_close(_cleanup)
    return response


# ============================================================
# 路由：人工标注模式
# ============================================================

@app.route("/annotate")
def annotate_page():
    """渲染人工标注页面"""
    return render_template("annotate.html")


@app.route("/api/page_count", methods=["POST"])
def page_count():
    """接收单文件上传，返回页数（用于人工标注模式初始化）"""
    f = request.files.get("file")
    if not f or not f.filename:
        return jsonify({"ok": False, "error": "未上传文件"}), 400
    safe_name = f.filename.replace("\\", "/").split("/")[-1]
    input_path = os.path.join(UPLOAD_DIR, f"{uuid.uuid4().hex[:8]}_{safe_name}")
    f.save(input_path)
    try:
        import fitz
        doc = fitz.open(input_path)
        pages = doc.page_count
        doc.close()
        return jsonify({"ok": True, "filename": os.path.basename(input_path), "pages": pages})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/page_preview/<filename>/<int:page_num>")
def page_preview(filename: str, page_num: int):
    """返回 PDF 单页的 PNG 预览图（用于人工标注的 Canvas 绘制）"""
    import fitz
    safe_name = filename.replace("\\", "/").split("/")[-1]
    file_path = os.path.join(UPLOAD_DIR, safe_name)
    if not os.path.exists(file_path):
        return jsonify({"ok": False, "error": "文件不存在"}), 404

    try:
        doc = fitz.open(file_path)
        if page_num < 0 or page_num >= doc.page_count:
            doc.close()
            return jsonify({"ok": False, "error": "页码超出范围"}), 400
        page = doc[page_num]
        pix = page.get_pixmap(matrix=fitz.Matrix(1, 1))
        from io import BytesIO
        buf = BytesIO(pix.tobytes("png"))
        doc.close()
        return Response(buf.getvalue(), mimetype="image/png")
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/annotated_translate", methods=["POST"])
def annotated_translate():
    """接收单文件 + 标注 JSON，启动翻译作业"""
    config = cfg.load_config()
    api_key = config.get("api_key")
    if not api_key:
        return jsonify({"ok": False, "error": "请先配置 API Key"}), 400

    model = config.get("model", const.MODEL_DEFAULT)
    source_lang = request.form.get("source_lang", const.DEFAULT_SOURCE_LANG)
    target_lang = request.form.get("target_lang", const.DEFAULT_TARGET_LANG)

    f = request.files.get("file")
    if not f or not f.filename:
        return jsonify({"ok": False, "error": "未上传文件"}), 400

    ext = os.path.splitext(f.filename)[1].lower()
    if ext != ".pdf":
        return jsonify({"ok": False, "error": "仅支持 PDF"}), 400

    safe_name = f.filename.replace("\\", "/").split("/")[-1]
    input_path = os.path.join(UPLOAD_DIR, f"{uuid.uuid4().hex[:8]}_{safe_name}")
    f.save(input_path)
    output_path = _make_output_path(safe_name)

    # 解析标注数据
    page_annotations = {}
    annotations_raw = request.form.get("page_annotations", "{}")
    try:
        page_annotations = json.loads(annotations_raw) if annotations_raw else {}
    except json.JSONDecodeError:
        pass

    # 解析术语词典
    initial_terms = {}
    termdict_file = request.files.get("termdict")
    if termdict_file and termdict_file.filename and termdict_file.filename.endswith(".json"):
        try:
            raw = termdict_file.read()
            loaded = json.loads(raw.decode("utf-8"))
            initial_terms = loaded.get("terms", loaded) if isinstance(loaded, dict) else {}
            if not isinstance(initial_terms, dict):
                initial_terms = {}
                print(f"[警告] 术语词典格式无效（非 JSON 对象），已忽略")
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            print(f"[警告] 术语词典解析失败: {e}，已忽略")

    files_data = [{
        "filename": safe_name,
        "input_path": input_path,
        "output_path": output_path,
        "output_filename": os.path.basename(output_path),
        "page_annotations": page_annotations,
    }]

    batch_id = translation_queue.create_batch(
        files_data=files_data, api_key=api_key, model=model,
        source_lang=source_lang, target_lang=target_lang,
        initial_terms=initial_terms,
    )

    return jsonify({"ok": True, "batch_id": batch_id, "total": 1})


# ============================================================
# 启动
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="PDFPaperTranslator Web Server")
    parser.add_argument("--port", "-p", type=int, default=5000,
                        help="服务器端口 (默认: 5000)")
    parser.add_argument("--host", type=str, default="127.0.0.1",
                        help="绑定地址 (默认: 127.0.0.1)")
    parser.add_argument("--no-browser", action="store_true",
                        help="不自动打开浏览器")
    args = parser.parse_args()

    _clear_uploads()

    url = f"http://{args.host}:{args.port}"

    print("=" * 50)
    print("  PDFPaperTranslator — Web 翻译界面")
    print("=" * 50)
    print(f"  服务器地址: {url}")
    print(f"  支持多文件队列翻译 PDF 学术论文")
    print(f"  按 Ctrl+C 停止服务器")
    print("=" * 50)

    if not args.no_browser:
        def _open_browser():
            time.sleep(1.5)
            webbrowser.open(url)
        threading.Thread(target=_open_browser, daemon=True).start()

    try:
        app.run(host=args.host, port=args.port, debug=False, threaded=True)
    except KeyboardInterrupt:
        print("\n[信息] 服务器已停止")


if __name__ == "__main__":
    main()

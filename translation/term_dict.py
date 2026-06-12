"""
术语词典管理 — 增量累积术语词典，跨批次保持一致。
支持多词典导入和智能合并。
"""

import json
import threading
from typing import Optional


class TermDictionary:
    """术语词典管理器，跨批次维持术语一致性（线程安全）。"""

    def __init__(self, initial_terms: Optional[dict[str, str]] = None):
        self._terms: dict[str, str] = dict(initial_terms) if initial_terms else {}
        self._lock = threading.Lock()

    @property
    def terms(self) -> dict[str, str]:
        with self._lock:
            return dict(self._terms)

    def add(self, src: str, dst: str):
        """添加或更新一个术语。"""
        with self._lock:
            self._terms[src] = dst

    def add_batch(self, new_terms: dict[str, str]) -> dict[str, str]:
        """批量添加新术语（不覆盖已有术语）。返回实际新增的术语 {src: dst}。线程安全。"""
        added = {}
        with self._lock:
            for src, dst in new_terms.items():
                if src not in self._terms:
                    self._terms[src] = dst
                    added[src] = dst
        return added

    def merge(self, new_terms: dict[str, str]) -> dict:
        """
        合并新术语，返回冲突报告。
        同名术语：已有优先保留，新译法记录为冲突。
        """
        conflicts = {}
        for src, dst in new_terms.items():
            if src in self._terms:
                if self._terms[src] != dst:
                    conflicts[src] = {"kept": self._terms[src], "discarded": dst}
            else:
                self._terms[src] = dst
        return conflicts

    def get(self, src: str) -> Optional[str]:
        """查找术语的翻译。"""
        return self._terms.get(src)

    def __len__(self) -> int:
        return len(self._terms)

    def __bool__(self) -> bool:
        return bool(self._terms)

    def to_dict(self) -> dict[str, str]:
        return dict(self._terms)


# ---- 多文件合并工具 ----

def merge_term_dicts(file_list: list[tuple[str, bytes]]) -> dict:
    """
    合并多份术语词典文件，返回合并结果和统计信息。

    Args:
        file_list: [(filename, file_bytes), ...]

    Returns:
        {"terms": {...}, "stats": {...}, "conflicts": [...], "ok": True}
        或 {"ok": False, "error": "..."}
    """
    merged = TermDictionary()
    stats = {
        "files_parsed": 0,
        "files_failed": 0,
        "total_raw_terms": 0,
        "unique_terms": 0,
        "conflict_count": 0,
        "errors": [],
    }
    all_conflicts = []

    for fname, fbytes in file_list:
        try:
            raw = fbytes.decode("utf-8")
            loaded = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            stats["files_failed"] += 1
            stats["errors"].append(f"{fname}: 无法解析 ({e})")
            continue

        if not isinstance(loaded, dict):
            stats["files_failed"] += 1
            stats["errors"].append(f"{fname}: 格式无效（非 JSON 对象）")
            continue

        # 识别来源：支持 PDFPaperTranslator 和 AINovelTranslator
        meta = loaded.get("_meta")
        terms = loaded.get("terms", {})
        if not isinstance(terms, dict) or not terms:
            # 可能是扁平的 {src: dst} 格式（无 _meta 包装）
            if isinstance(loaded, dict) and all(
                isinstance(k, str) and isinstance(v, str) for k, v in loaded.items()
            ):
                terms = loaded
            else:
                stats["files_failed"] += 1
                stats["errors"].append(f"{fname}: 未找到有效术语条目")
                continue

        stats["files_parsed"] += 1
        stats["total_raw_terms"] += len(terms)

        # 合并：检测冲突
        conflicts = merged.merge(terms)
        for src, info in conflicts.items():
            all_conflicts.append({
                "source": src,
                "kept": info["kept"],
                "discarded": info["discarded"],
                "from_file": fname,
            })
        stats["conflict_count"] += len(conflicts)

    stats["unique_terms"] = len(merged)

    if stats["files_parsed"] == 0:
        return {"ok": False, "error": "没有成功解析任何术语词典文件"}

    return {
        "ok": True,
        "terms": merged.to_dict(),
        "stats": stats,
        "conflicts": all_conflicts[:50],  # 最多返回50个冲突详情
    }
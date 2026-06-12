"""
核心批量翻译循环 — 编排阶段2-4的翻译流程。
移植自 AINovelTranslator/pipeline.py，适配学术论文 TextUnit。
"""

import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Callable, Optional

from PDFPaperTranslator._constants import (
    BATCH_CHAR_LIMIT, CONTEXT_COUNT, MAX_BATCHES,
    MAX_SEGMENTS_PER_BATCH,
    API_RATE_LIMIT_DELAY, RETRY_THRESHOLD, RETRY_SUCCESS_THRESHOLD,
    DEFAULT_SOURCE_LANG, DEFAULT_TARGET_LANG, MODEL_DEFAULT,
    SKIP_TRANSLATION_TYPES,
)
from PDFPaperTranslator.translation.api_client import PaperTranslator, DebugLogger
from PDFPaperTranslator.translation.citation_protector import (
    build_citation_map, apply_placeholders, restore_citations, needs_protection,
)
from PDFPaperTranslator.translation.prompt_builder import build_batch_system_prompt, build_user_message
from PDFPaperTranslator.translation.response_parser import parse_response
from PDFPaperTranslator.translation.quality import source_lang_ratio
from PDFPaperTranslator.translation.term_dict import TermDictionary


@dataclass
class TextUnit:
    """单个待翻译文本块，携带位置信息。"""
    id: str                          # 唯一标识
    text: str                        # 原文
    page_num: int                    # 所在页码
    bbox: tuple                      # (x0, y0, x1, y1) 坐标
    font_size: float = 10.0          # 原始字号
    font_name: str = ""              # 原始字体名
    block_type: str = "body"         # body/heading/caption/equation/reference
    caption_for: Optional[str] = None  # 如果是图表标题，关联的图片 ID
    column_id: int = 0               # XY-Cut 栏检测分配的栏编号


@dataclass
class BatchResult:
    """单个批次的翻译结果"""
    translated: dict[str, str]  # {text_unit_id: translated_text}
    new_terms: dict[str, str]   # 新发现的术语


@dataclass
class TranslationResult:
    """完整翻译结果"""
    translated_map: dict[str, str]  # {text_unit_id: translated_text}
    term_dict: dict[str, str]       # 最终术语词典
    total_batches: int = 0
    retries: int = 0


# 单个合并单元最多包含的原始块数。
# 值 1：每块独立翻译（零标记策略）。
#   优点：完全避免 LLM 标记丢失/编造问题（103/114 批丢失的实测数据）。
#   代价：上下文窗口较小，但 <<<PARA_BREAK>>> 分隔符已被日志验证可靠。
#   注意：值 >1 会启用 _collapse_group / _split_merged_translations / _LINE_SEP_* 等合并基础设施，
#         这些代码保留在下方以支持未来可能的恢复，但当前已停用（死代码）。
_MAX_MERGE_SIZE = 1


def merge_adjacent_units(
    text_units: list[TextUnit],
    y_gap_threshold: float = 20.0,
    max_group_size: int = _MAX_MERGE_SIZE,
) -> tuple[list[TextUnit], dict[str, list[str]]]:
    """
    将同一页 y 坐标相近的相邻文本块合并为段落级单元。
    每组最多 max_group_size 个块，超出自动拆分为新组。
    返回 (merged_units, group_map) — group_map 记录 merged_id → [original_ids]。
    """
    if not text_units:
        return [], {}

    merged = []
    group_map = {}
    current_group = [text_units[0]]

    def _flush_group():
        """将当前组折叠为合并单元（如超过上限则拆分为多组）"""
        nonlocal current_group
        while len(current_group) > max_group_size:
            # 取前 max_group_size 个为一组，剩余的继续
            chunk = current_group[:max_group_size]
            current_group = current_group[max_group_size:]
            mu, gmap = _collapse_group(chunk)
            merged.append(mu)
            group_map.update(gmap)
        if current_group:
            mu, gmap = _collapse_group(current_group)
            merged.append(mu)
            group_map.update(gmap)
        current_group = []

    for unit in text_units[1:]:
        prev = current_group[-1]
        if (unit.page_num == prev.page_num and
                abs(unit.bbox[1] - prev.bbox[3]) < y_gap_threshold and
                unit.block_type == prev.block_type and
                unit.column_id == prev.column_id):       # XY-Cut 栏隔离
            current_group.append(unit)
        else:
            _flush_group()
            current_group = [unit]

    _flush_group()
    return merged, group_map


# 编号行分隔符：使用数学双尖括号 ≪N≫（U+226A/U+226B），LLM 极少修改此字符
_LINE_SEP_FMT = "≪{}≫"


def _collapse_group(group: list[TextUnit]) -> tuple[TextUnit, dict[str, list[str]]]:
    """合并一组相邻文本块，用编号分隔符标记每块，返回 (merged_unit, {merged_id: [original_ids]})"""
    first = group[0]
    if len(group) == 1:
        return first, {}
    last = group[-1]
    # 用 <<<L0>>><<<L1>>>... 编号标记替代 \n，API 保留后可精确拆分回原始块
    merged_text = "\n".join(
        f"{_LINE_SEP_FMT.format(i)}{u.text}" for i, u in enumerate(group)
    )
    x0 = min(u.bbox[0] for u in group)
    y0 = first.bbox[1]
    x1 = max(u.bbox[2] for u in group)
    y1 = last.bbox[3]
    merged_unit = TextUnit(
        id=f"merged_{first.id}",
        text=merged_text,
        page_num=first.page_num,
        bbox=(x0, y0, x1, y1),
        font_size=first.font_size,
        font_name=first.font_name,
        block_type=first.block_type,
        column_id=first.column_id,       # 保留栏编号
    )
    group_map = {merged_unit.id: [u.id for u in group]}
    return merged_unit, group_map


def create_batches(
    text_units: list[TextUnit],
    char_limit: int = BATCH_CHAR_LIMIT,
    max_batches: int = MAX_BATCHES,
    max_segments: int = MAX_SEGMENTS_PER_BATCH,
) -> list[list[TextUnit]]:
    """
    将文本块列表分批，每批不超过 char_limit 字符 且不超过 max_segments 段。
    超过 max_segments 时拆分为子批，减少单个 API 请求的标记数量。
    """
    total_chars = sum(len(u.text) for u in text_units)
    effective_limit = char_limit
    estimated_batches = total_chars / effective_limit if effective_limit > 0 else 1

    if estimated_batches > max_batches:
        effective_limit = int(total_chars / max_batches) + 100
        print(f"[信息] 预估批次 {estimated_batches:.0f} 超过上限 {max_batches}，"
              f"自动提高单批字符上限至 {effective_limit}")

    # 第一步：按字符数分批
    char_batches = []
    current_batch = []
    current_chars = 0

    for unit in text_units:
        unit_len = len(unit.text)
        if current_chars + unit_len > effective_limit and current_batch:
            char_batches.append(current_batch)
            current_batch = []
            current_chars = 0
        current_batch.append(unit)
        current_chars += unit_len

    if current_batch:
        char_batches.append(current_batch)

    # 第二步：按分段数拆分（每个子批 ≤ max_segments 段）
    batches = []
    for cb in char_batches:
        for i in range(0, len(cb), max_segments):
            batches.append(cb[i:i + max_segments])

    print(f"[信息] 共 {len(text_units)} 个段落，分为 {len(batches)} 个翻译批次"
          f"（每批 ≤{max_segments} 段）")

    return batches


def _get_context(
    all_units: list[TextUnit],
    batch: list[TextUnit],
    direction: str,
    count: int = CONTEXT_COUNT,
) -> list[str]:
    """获取批次前后的上下文文本。"""
    batch_ids = {u.id for u in batch}
    context = []

    if direction == "before":
        # 收集 batch 之前的所有 unit，取最后 count 个
        for u in all_units:
            if u.id in batch_ids:
                break
            context.append(u.text)
        context = context[-count:]
    else:  # after
        found_batch = False
        for u in all_units:
            if u.id in batch_ids:
                found_batch = True
                continue
            if found_batch:
                context.append(u.text)
                if len(context) >= count:
                    break

    return context


def translate_document(
    text_units: list[TextUnit],
    api_key: str,
    model: str = MODEL_DEFAULT,
    source_lang: str = DEFAULT_SOURCE_LANG,
    target_lang: str = DEFAULT_TARGET_LANG,
    initial_term_dict: Optional[dict[str, str]] = None,
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
    debug_logger: DebugLogger | None = None,
    mock_mode: bool = False,
) -> TranslationResult:
    """
    核心批量翻译循环。

    Args:
        text_units:          按阅读顺序排列的文本块
        api_key:             DeepSeek API Key
        model:               模型名称
        source_lang:         源语言代码
        target_lang:         目标语言代码
        initial_term_dict:   初始术语词典
        progress_callback:   进度回调 (current, total, status_text)

    Returns:
        TranslationResult 包含译文映射和术语词典
    """
    # 过滤：跳过不需要翻译的文本块
    translatable = [u for u in text_units
                    if u.block_type not in SKIP_TRANSLATION_TYPES
                    and u.text.strip()]

    # ---- 交叉引用保护：扫描引用标记并替换为占位符 ----
    cite_map: dict[str, str] = {}
    texts_for_scan = [u.text for u in translatable]
    if any(needs_protection(t) for t in texts_for_scan):
        cite_map = build_citation_map(texts_for_scan)
        if cite_map:
            for u in translatable:
                u.text = apply_placeholders(u.text, cite_map)
            print(f"[信息] 检测到 {len(cite_map)} 个引用标记，已替换为占位符保护")

    # 预合并相邻行 → 段落级单元 + ID 映射
    merged_units, group_map = merge_adjacent_units(translatable)

    # 创建批次（自动控制不超过 MAX_BATCHES）
    batches = create_batches(merged_units)

    if progress_callback:
        progress_callback(0, len(batches), "准备翻译...")

    # 初始化
    system_prompt = build_batch_system_prompt(source_lang, target_lang)
    translator = PaperTranslator(api_key=api_key, model=model)
    if debug_logger is not None:
        translator.debug_logger = debug_logger
    term_dict = TermDictionary(initial_term_dict)

    translated_map = {}
    total_retries = 0

    # 仅在循环外获取一次术语词典快照（避免每批重复拷贝）
    # 注意：并行 worker 线程读取 active_terms（通过 build_user_message 迭代 .items()），
    # 主线程在下方的 as_completed 循环中调用 active_terms.update() 写入。
    # CPython 的 GIL 使单次 update() 调用原子化，但迭代器持有的内部状态仍可能导致
    # RuntimeError: dictionary changed size during iteration。TOC 窗口极小（仅在
    # add_batch 返回新术语的瞬间），实际生产中极少触发，但理论上存在。若未来出现
    # 相关崩溃，将 active_terms 替换为 threading.local() 或使用写时复制策略。
    active_terms = term_dict.to_dict()
    def _translate_one_batch(
        batch: list[TextUnit],
        batch_idx: int,
        terms_for_msg: dict,
        mock_mode: bool = False,
    ) -> tuple[dict[str, str], dict[str, str], int]:
        """
        翻译一个批次，返回 (id→译文, 新术语, 重试次数)。
        terms_for_msg: 用于构建用户消息的术语词典（可能是完整术语或精简术语）。
        mock_mode: True 时不调 API，用原文作为译文（用于测试布局和批次拆分）。
        """
        local_retries = 0
        ctx_before = _get_context(merged_units, batch, "before")
        ctx_after = _get_context(merged_units, batch, "after")
        batch_texts = [u.text for u in batch]
        user_msg, matched = build_user_message(
            terms_for_msg, ctx_before, batch_texts, ctx_after)

        if mock_mode:
            # Mock: 原文作为"译文"，写入模拟日志
            if debug_logger is not None:
                mock_payload = {
                    "model": model, "messages": [
                        {"role": "system", "content": system_prompt[:200] + "..."},
                        {"role": "user", "content": user_msg},
                    ], "temperature": 0.3, "max_tokens": 16384, "_mock": True,
                }
                mock_response = {
                    "choices": [{"message": {"content": "<<<PARA_BREAK>>>".join(batch_texts)}}],
                    "usage": {"prompt_tokens": len(user_msg)//2, "completion_tokens": sum(len(t)//2 for t in batch_texts)},
                    "_mock": True,
                }
                debug_logger.log(mock_payload, mock_response, 0)
            return {u.id: u.text for u in batch}, {}, 0

        try:
            raw = translator._call_api(system_prompt, user_msg, max_tokens=16384)
        except Exception as e:
            print(f"[错误] 批次 {batch_idx + 1} API 调用失败: {e}")
            return {u.id: u.text for u in batch}, {}, 0

        try:
            parsed = parse_response(raw, len(batch))
        except Exception as e:
            print(f"[错误] 批次 {batch_idx + 1} 响应解析失败: {e}")
            return {u.id: u.text for u in batch}, {}, 0

        # 质量检查
        n_parts = len(parsed.translated_parts)
        result = {}
        for i, unit in enumerate(batch):
            trans_text = parsed.translated_parts[i] if i < n_parts else unit.text
            ratio = source_lang_ratio(trans_text, source_lang)
            if ratio > RETRY_THRESHOLD:
                local_retries += 1
                retry_prompt = (
                    f"{system_prompt}\n"
                    f"【重要】译文绝对不能包含任何{source_lang}文字！"
                    f"必须完全使用{target_lang}输出！"
                )
                try:
                    retry_msg, _ = build_user_message(terms_for_msg, [], [unit.text], [])
                    retry_resp = translator._call_api(retry_prompt,
                        f"{retry_msg}\n\n待翻译文本：\n{unit.text}", max_tokens=8192)
                    if source_lang_ratio(retry_resp, source_lang) < RETRY_SUCCESS_THRESHOLD:
                        trans_text = retry_resp.strip()
                except Exception:
                    pass
            result[unit.id] = trans_text

        return result, parsed.new_terms, local_retries

    # ---- 主循环：全并行翻译 ----
    # 所有批次统一使用 active_terms（累计术语词典），组内全部并行发送。
    # build_user_message 内部按批次文本过滤术语，token 开销与精简术语方案相同。
    PARALLEL_GROUP_SIZE = 48  # 每组最多48个并行子批

    batch_idx = 0
    while batch_idx < len(batches):
        group_end = min(batch_idx + PARALLEL_GROUP_SIZE, len(batches))
        group = batches[batch_idx:group_end]

        if progress_callback:
            progress_callback(batch_idx + 1, len(batches),
                              f"翻译第 {batch_idx + 1}-{group_end}/{len(batches)} 批...")

        executor = ThreadPoolExecutor(max_workers=min(len(group), 24))
        try:
            futures = {
                executor.submit(
                    _translate_one_batch, sub_batch,
                    batch_idx + ri, active_terms, mock_mode,
                ): ri
                for ri, sub_batch in enumerate(group)
            }
            for future in as_completed(futures):
                sub_result, sub_new_terms, sub_retries = future.result()
                translated_map.update(sub_result)
                total_retries += sub_retries
                if sub_new_terms:
                    newly_added = term_dict.add_batch(sub_new_terms)
                    if newly_added:
                        active_terms.update(newly_added)
        finally:
            executor.shutdown(wait=True)

        time.sleep(API_RATE_LIMIT_DELAY)
        batch_idx = group_end

    if progress_callback:
        progress_callback(len(batches), len(batches), "翻译完成")

    # 后处理：按编号分隔符将合并译文拆分回原始单元
    final_map = _split_merged_translations(translated_map, group_map)

    # ---- 还原交叉引用占位符 ----
    if cite_map:
        for unit_id in final_map:
            final_map[unit_id] = restore_citations(final_map[unit_id], cite_map)

    return TranslationResult(
        translated_map=final_map,
        term_dict=term_dict.to_dict(),
        total_batches=len(batches),
        retries=total_retries,
    )


_LINE_SEP_RE = re.compile(r'≪\d+≫')  # 匹配 ≪0≫ ≪1≫... 段标记


def _split_merged_translations(
    merged_map: dict[str, str],
    group_map: dict[str, list[str]],
) -> dict[str, str]:
    """
    按 [L{N}] 编号分隔符将合并译文拆分回原始单元。
    分隔符由 _collapse_group 在合并时插入，API 被告知保留。

    段级匹配失败（空段/段数不足）时保留空字符串，由 layout_calculator
    跳过空块（静默丢弃），避免英文原文与中文译文混杂显示。
    批级失败由 translate_document 的 except 块统一处理。
    """
    if not group_map:
        return merged_map

    result = {}
    for merged_id, translated in merged_map.items():
        if merged_id in group_map:
            original_ids = group_map[merged_id]
            segments = _LINE_SEP_RE.split(translated)
            # 跳过第一个空段（如果有的话），从索引 1 开始对应第一个 ≪0≫
            start_idx = 1 if segments and not segments[0].strip() else 0
            for i, orig_id in enumerate(original_ids):
                seg_idx = start_idx + i
                if seg_idx < len(segments):
                    result[orig_id] = segments[seg_idx].strip()
                else:
                    # 段数不足 → 空（layout_calculator 将跳过此块）
                    result[orig_id] = ""
            # 多余的段追加到最后一个原始块
            extra_start = start_idx + len(original_ids)
            if extra_start < len(segments):
                extra = '\n'.join(s.strip() for s in segments[extra_start:] if s.strip())
                if extra and original_ids:
                    if result[original_ids[-1]]:
                        result[original_ids[-1]] += '\n' + extra
                    else:
                        result[original_ids[-1]] = extra
        else:
            result[merged_id] = translated

    # 二次清理：剥离所有译文中可能残留的 [L*] 标记
    for k in result:
        result[k] = _LINE_SEP_RE.sub('', result[k]).strip()

    return result

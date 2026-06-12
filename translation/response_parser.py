"""
Stage 4: 响应解析 — 拆分译文、提取新术语、清理混合输出。
移植自 AINovelTranslator/response_parser.py。
"""

import re
from dataclasses import dataclass

from .._constants import DICT_MARKER, LINE_BREAK_MARKER
from .quality import is_rare_term


@dataclass
class ParsedResponse:
    """解析后的 API 响应"""
    translated_parts: list[str]   # 各文本块译文
    new_terms: dict[str, str]     # 新发现的术语 {src: dst}


# ---- 混合输出清理 ----

def clean_mixed_output(text: str) -> str:
    """
    清理译文中可能残留的原文与译文混合内容。
    处理常见的不良输出模式：
      - "原文：...\\n译文：..." 双语对照格式
      - "（原文：...）"  括号内附原文
      - "原文：..."  /  "译文：..."  标签前缀
    """
    text = text.strip()

    # 1. 移除 "原文：...译文：..." 配对
    text = re.sub(
        r'原文[：:]\s*.*?\n\s*译文[：:]\s*', '', text, flags=re.DOTALL,
    )
    text = re.sub(r'原文[：:]\s*.*?译文[：:]\s*', '', text)

    # 2. 移除行首的 "译文：" 标签
    text = re.sub(r'^译文[：:]\s*', '', text, flags=re.MULTILINE)

    # 3. 移除行首的 "原文：xxx" 整行
    text = re.sub(r'^原文[：:]\s*.*$', '', text, flags=re.MULTILINE)

    # 4. 移除括号内附原文
    text = re.sub(r'[（(]原文[：:].*?[）)]', '', text)

    # 5. 清理多余空行
    text = re.sub(r'\n{3,}', '\n\n', text)

    return text.strip()


# ---- 换行标记还原 ----

_LINE_BREAK_RE = re.compile(re.escape(LINE_BREAK_MARKER) + r"{1,3}")


def restore_line_breaks(text: str) -> str:
    """将 LINE_BREAK_MARKER 的各种变体还原为 \\n"""
    return _LINE_BREAK_RE.sub("\n", text)


# ---- 术语词典残留剥离 ----

# 术语词典标记（单一定义，parse_response 和 _strip_term_dict_section 共享）
# 按长度降序排列：长标记优先匹配，避免 "术语词典" 短串误匹配 "术语词典：" 前缀
_DICT_MARKERS = [DICT_MARKER, "术语词典：", "[术语词典]", "〖术语词典〗", "术语词典"]
# 内联无新术语标记（API 有时在段落中插入这些短语而不带完整术语区块）
_INLINE_NO_TERMS = re.compile(
    r'[（(]无新(?:增)?术语[）)]|'
    r'[（(]本次无新发现术语[）)]|'
    r'[（(]无[）)]|'
    r'---\s*$'
)


def _strip_term_dict_section(text: str) -> str:
    """从单个译文片段中剥离可能残留的术语词典。"""
    for marker in _DICT_MARKERS:
        idx = text.find(marker)
        if idx != -1:
            return text[:idx].strip()
    return text


# ---- 响应解析 ----

def parse_response(
    raw_response: str,
    expected_count: int,
) -> ParsedResponse:
    """
    解析 DeepSeek API 的原始响应。

    Args:
        raw_response:   模型原始输出文本
        expected_count: 预期的文本块数量

    Returns:
        ParsedResponse 包含译文列表和新术语词典
    """
    # 分离译文与术语词典
    trans_text = raw_response
    dict_section = ""

    for marker in _DICT_MARKERS:
        if marker in raw_response:
            split_idx = raw_response.index(marker)
            trans_text = raw_response[:split_idx].strip()
            dict_section = raw_response[split_idx + len(marker):].strip()
            break

    # 提取新术语
    new_terms = {}
    if dict_section:
        for line in dict_section.split("\n"):
            line = line.strip()
            if not line or line.startswith("（") or line.startswith("("):
                continue
            # 尝试各种分隔符
            separator = None
            for sep in [" → ", "→", " -> ", "->", "\t"]:
                if sep in line:
                    separator = sep
                    break
            if separator:
                parts = line.split(separator, 1)
                src = parts[0].strip()
                dst = parts[1].strip()
                if not src or not dst:
                    continue
                if len(src) > 80 or len(dst) > 80:
                    continue
                if is_rare_term(src):
                    new_terms[src] = dst

    # 拆分译文，并对每个片段二次剥离可能残留的术语词典
    para_delim = "<<<PARA_BREAK>>>"
    translated_parts = trans_text.split(para_delim)
    translated_parts = [
        _INLINE_NO_TERMS.sub('', _strip_term_dict_section(restore_line_breaks(clean_mixed_output(p)))).strip()
        for p in translated_parts
    ]

    # 容错：段落数不匹配时的恢复策略
    if len(translated_parts) < expected_count and len(translated_parts) > 0:
        # LLM 合并了部分段落 → 将已有译文按比例分配
        print(f"[信息] 译文段落数 ({len(translated_parts)}) < 预期 ({expected_count})，按比例分配")
        # 将现有译文拼接后按预期段数均分（简单的字符数均分）
        merged = "".join(translated_parts)
        chunk_size = max(1, len(merged) // expected_count)
        recovered = []
        pos = 0
        for i in range(expected_count):
            end = pos + chunk_size if i < expected_count - 1 else len(merged)
            recovered.append(merged[pos:end].strip())
            pos = end
        translated_parts = recovered
    elif len(translated_parts) < expected_count:
        # 完全空响应
        translated_parts.extend([""] * (expected_count - len(translated_parts)))
    elif len(translated_parts) > expected_count:
        # LLM 产生了多余段落 → 追加到最后一个段，不丢弃
        excess = "".join(translated_parts[expected_count:])
        if excess.strip():
            translated_parts[expected_count - 1] += "\n" + excess
        print(f"[信息] 译文段落数 ({len(translated_parts)}) > 预期 ({expected_count})，多余内容追加到末段")
        translated_parts = translated_parts[:expected_count]

    return ParsedResponse(
        translated_parts=translated_parts,
        new_terms=new_terms,
    )

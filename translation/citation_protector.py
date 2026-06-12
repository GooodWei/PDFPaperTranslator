"""
Citation Protector — 交叉引用占位符保护模块。

在翻译前将引用标记替换为全局唯一 Unicode 占位符 ⟨CITE_N⟩，
翻译后还原。LLM 极少修改 Unicode 尖括号内的内容，从而保证
引用格式在翻译后保持完整。

用法:
    from PDFPaperTranslator.translation.citation_protector import (
        build_citation_map, apply_placeholders,
        restore_citations, needs_protection,
    )

    texts = ["See [1,2].", "Ref (Smith, 2020)."]
    cite_map = build_citation_map(texts)
    protected = [apply_placeholders(t, cite_map) for t in texts]
    # ... 翻译 ...
    restored = [restore_citations(t, cite_map) for t in translated]
"""

import re
from typing import Optional

from PDFPaperTranslator._constants import CITATION_PLACEHOLDER_FMT, CITATION_PATTERNS


# 模块加载时预编译正则，复用性能
_CITATION_RES: list[re.Pattern] = [re.compile(p) for p in CITATION_PATTERNS]


def find_all_citations(text: str) -> list[str]:
    """
    返回文本中所有引用匹配的完整文本列表。

    每个元素是一个完整匹配（如 "[1,2]"、"(Smith, 2020)"）。
    按匹配位置排序，重复的引用项不合并（由调用方去重）。
    """
    matches: list[tuple[int, str]] = []
    for regex in _CITATION_RES:
        for m in regex.finditer(text):
            matches.append((m.start(), m.group(0)))
    # 按位置排序
    matches.sort(key=lambda x: x[0])
    return [m[1] for m in matches]


def build_citation_map(texts: list[str]) -> dict[str, str]:
    """
    扫描所有待翻译文本，构建全局引用映射。

    返回 {原始引用文本: 占位符}。
    全局唯一编号，相同引用文本使用同一占位符，
    保证跨合并单元的引用映射一致。

    Args:
        texts: 所有待翻译文本列表（不含已过滤的 reference 块）

    Returns:
        {citation_text: placeholder} 映射，无引用时返回空 dict
    """
    seen: set[str] = set()
    cite_map: dict[str, str] = {}
    counter = 0

    for text in texts:
        for citation in find_all_citations(text):
            if citation not in seen:
                seen.add(citation)
                placeholder = CITATION_PLACEHOLDER_FMT.format(n=counter)
                cite_map[citation] = placeholder
                counter += 1

    return cite_map


def apply_placeholders(text: str, cite_map: dict[str, str]) -> str:
    """
    将文本中的引用标记替换为占位符。

    按引用长度降序替换，避免短引用破坏长引用。
    例如 "[1,2]" 先于 "[1]" 替换，防止 "[1]" 匹配 "[1,2]" 中的 "[1]" 部分。

    Args:
        text: 原始文本
        cite_map: build_citation_map() 返回的映射

    Returns:
        替换后的文本
    """
    if not cite_map:
        return text
    # 长度降序：长引用优先替换
    for citation in sorted(cite_map, key=len, reverse=True):
        placeholder = cite_map[citation]
        text = text.replace(citation, placeholder)
    return text


def restore_citations(text: str, cite_map: dict[str, str]) -> str:
    """
    将翻译后文本中的占位符还原为原始引用。

    如果某个占位符缺失（LLM 意外删除），保留占位符原文不做替换。

    Args:
        text: 翻译后的文本（含占位符）
        cite_map: build_citation_map() 返回的映射

    Returns:
        还原后的文本
    """
    if not cite_map:
        return text
    for citation, placeholder in cite_map.items():
        text = text.replace(placeholder, citation)
    return text


def needs_protection(text: str) -> bool:
    """
    快速预检：文本是否包含任何引用模式。

    用于跳过不必要的全文扫描。无引用时整个保护流程零开销。

    Args:
        text: 待检查的文本

    Returns:
        True 如果文本可能包含引用标记
    """
    for regex in _CITATION_RES:
        if regex.search(text):
            return True
    return False

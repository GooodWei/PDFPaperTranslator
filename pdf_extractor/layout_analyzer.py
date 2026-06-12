"""
版式分析：文本块分类（正文/标题/图表标题等）。
"""

import re

from PDFPaperTranslator._constants import (
    REFERENCE_HEADER_PATTERN,
    REFERENCE_ENTRY_BRACKET_PATTERN, REFERENCE_ENTRY_CONTENT_PATTERN,
)
from PDFPaperTranslator.pdf_extractor.text_extractor import TextBlock


def classify_block(block: TextBlock) -> str:
    """
    对文本块进行分类：正文/标题/图表标题/公式/参考文献。

    判断依据：字号、粗体、文本前缀（Figure/Table/Fig.）、字符构成。
    """
    text = block.text.strip()

    # 图表标题检测
    caption_patterns = [
        r'^(?:Fig(?:ure)?\.?\s*\d+)',      # Fig. 1, Figure 1
        r'^(?:Table\.?\s*\d+)',             # Table 1
        r'^(?:Table\.?\s*[IVX]+)',          # Table I (Roman numerals)
        r'^(?:图\d+)',                       # 图1
        r'^(?:表\d+)',                       # 表1
        r'^(?:Scheme\.?\s*\d+)',            # Scheme 1
        r'^(?:Algorithm\.?\s*\d+)',         # Algorithm 1
    ]
    for pattern in caption_patterns:
        if re.match(pattern, text, re.IGNORECASE):
            return "caption"

    # 公式检测：非字母字符比例高
    alpha_count = sum(1 for c in text if c.isalpha())
    if len(text) > 10 and alpha_count / len(text) < 0.3:
        return "equation"

    # 标题检测：字号较大 / 粗体 / 全大写短文本
    if block.font_size >= 13 or (block.font_size >= 11 and block.is_bold):
        return "heading"
    return "body"


def is_reference_header(text: str) -> bool:
    """
    判断文本是否为参考文献区段标题。

    匹配 "References"、"Bibliography"、"参考文献" 等，
    支持编号形式如 "5. References"。
    """
    return bool(re.match(REFERENCE_HEADER_PATTERN, text.strip(), re.IGNORECASE))


def is_likely_reference_entry(text: str) -> bool:
    """
    判断单个文本块是否像一条参考文献条目。

    用于回退检测（当论文没有明确的 "References" 标题时）：
      - 以 [N] 或 [N,N] 开头（编号引用）
      - 包含 "et al."、"Journal of" 等学术引用特征
    """
    t = text.strip()
    if not t:
        return False
    if re.match(REFERENCE_ENTRY_BRACKET_PATTERN, t, re.IGNORECASE):
        return True
    if re.search(REFERENCE_ENTRY_CONTENT_PATTERN, t, re.IGNORECASE):
        return True
    return False

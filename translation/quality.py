"""
术语过滤与翻译质量控制。
移植自 AINovelTranslator/term_filter.py，扩展支持英文检测和学术术语。
"""

import re


def is_rare_term(term: str) -> bool:
    """
    判断术语是否值得记录到词典。
    对于英文术语：过滤常见单词，保留专业术语。

    返回 True 表示值得保留，False 表示应过滤掉。
    """
    # 太短的不可能是专业术语
    if len(term) <= 1:
        return False

    # 英文常见学术通用词汇黑名单
    common_academic_words = {
        'the', 'a', 'an', 'is', 'are', 'was', 'were', 'be', 'been',
        'this', 'that', 'these', 'those', 'it', 'its', 'they', 'them',
        'we', 'our', 'i', 'you', 'he', 'she', 'his', 'her', 'my', 'your',
        'and', 'or', 'but', 'not', 'if', 'so', 'as', 'at', 'by', 'in',
        'on', 'of', 'to', 'for', 'with', 'from', 'about', 'into',
        'can', 'will', 'would', 'could', 'should', 'may', 'might', 'has', 'have',
        'when', 'where', 'which', 'who', 'what', 'how', 'all', 'each', 'both',
        'more', 'most', 'some', 'any', 'other', 'such', 'only', 'also',
        'very', 'just', 'then', 'now', 'than', 'too', 'also',
        'method', 'result', 'experiment', 'study', 'paper', 'research',
        'figure', 'table', 'section', 'chapter', 'equation', 'data',
        'analysis', 'model', 'system', 'process', 'approach',
        'proposed', 'based', 'used', 'shown', 'found', 'obtained',
        'using', 'first', 'second', 'third', 'two', 'three', 'one',
        'new', 'different', 'high', 'low', 'large', 'small',
        'however', 'therefore', 'because', 'since', 'although',
        'respectively', 'significant', 'important', 'specific',
    }
    if term.lower() in common_academic_words:
        return False

    # 纯数字+单位 → 不是术语
    if re.fullmatch(r'[\d.\s%°℃±×μgmlnmolL/\-]+', term):
        return False

    return True


def source_lang_ratio(text: str, source_lang: str) -> float:
    """检测文本中源语言字符的占比，用于判断翻译是否成功。"""
    if not text:
        return 0.0

    text_len = len(text)

    if source_lang == "en":
        # 英语：拉丁字母 [a-zA-Z]
        count = sum(1 for c in text if 'a' <= c <= 'z' or 'A' <= c <= 'Z')
    elif source_lang == "ja":
        count = sum(1 for c in text if '぀' <= c <= 'ゟ' or '゠' <= c <= 'ヿ')
    elif source_lang == "ko":
        count = sum(1 for c in text if '가' <= c <= '힣' or 'ᄀ' <= c <= 'ᇿ')
    elif source_lang == "zh":
        count = sum(1 for c in text if '一' <= c <= '鿿')
    else:
        return 0.0

    return count / text_len

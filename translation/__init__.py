"""
翻译引擎子包 — API 客户端、提示词构建、响应解析、质量控制、批量翻译。
移植自 AINovelTranslator，适配学术论文翻译。
"""

from PDFPaperTranslator.translation.api_client import PaperTranslator
from PDFPaperTranslator.translation.batch_engine import translate_document, TextUnit
from PDFPaperTranslator.translation.prompt_builder import build_batch_system_prompt, build_user_message
from PDFPaperTranslator.translation.response_parser import parse_response
from PDFPaperTranslator.translation.quality import source_lang_ratio

__all__ = [
    "PaperTranslator",
    "translate_document",
    "TextUnit",
    "build_batch_system_prompt",
    "build_user_message",
    "parse_response",
    "source_lang_ratio",
]

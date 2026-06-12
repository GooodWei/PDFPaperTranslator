"""
PDFPaperTranslator — 自动将国外 PDF 学术文献翻译为中文。

核心功能：
  - 提取 PDF 文本块（含坐标）和图片
  - 使用 DeepSeek API 批量翻译
  - 重建 PDF，保留原始图片和相对位置

用法:
  python -m PDFPaperTranslator --pdf paper.pdf
  python -m PDFPaperTranslator --pdf paper.pdf --output translated.pdf

参考项目: AINovelTranslator (I:/AINovelTranslator/AINovelTranslator)
"""

__version__ = "1.0.0"
__author__ = "PDFPaperTranslator"

from .cli import main
from ._constants import (
    DEFAULT_SOURCE_LANG,
    DEFAULT_TARGET_LANG,
    MODEL_DEFAULT,
    MODEL_OPTIONS,
    TERMDICT_META,
)

__all__ = [
    "main",
    "DEFAULT_SOURCE_LANG",
    "DEFAULT_TARGET_LANG",
    "MODEL_DEFAULT",
    "MODEL_OPTIONS",
    "TERMDICT_META",
]

"""
PDF 提取子包 — 从 PDF 提取文本块（含坐标）、图片、分析版式。
"""

from .document import open_pdf, DocInfo
from .text_extractor import extract_text_blocks
from .image_extractor import extract_images
from .block_grouper import group_all_pages
from .table_extractor import detect_tables_on_page, DetectedTable

__all__ = [
    "open_pdf",
    "DocInfo",
    "extract_text_blocks",
    "extract_images",
    "group_all_pages",
    "detect_tables_on_page",
    "DetectedTable",
]

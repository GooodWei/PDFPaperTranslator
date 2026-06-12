"""
PDF 文档打开与基本信息获取。
"""

from dataclasses import dataclass

import fitz  # PyMuPDF


@dataclass
class DocInfo:
    """PDF 文档基本信息和元数据"""
    file_path: str
    total_pages: int
    metadata: dict = None

    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}


def open_pdf(pdf_path: str) -> tuple[fitz.Document, DocInfo]:
    """
    打开 PDF 文件并返回文档对象和信息。

    Args:
        pdf_path: PDF 文件路径

    Returns:
        (fitz.Document, DocInfo) 元组
    """
    doc = fitz.open(pdf_path)
    info = DocInfo(
        file_path=pdf_path,
        total_pages=len(doc),
        metadata=dict(doc.metadata),
    )
    return doc, info

"""
从 PDF 页面中提取文本块及其位置信息（bbox）。
使用 PyMuPDF 的 "dict" 模式获取每个 text span 的精确坐标。
"""

from dataclasses import dataclass
from typing import Optional

import fitz  # PyMuPDF

from .._constants import MIN_BLOCK_WIDTH, MIN_BLOCK_HEIGHT


@dataclass
class TextBlock:
    """单个文本块，携带位置和格式信息"""
    text: str                        # 文本内容
    page_num: int                    # 页码（0-based）
    bbox: tuple[float, float, float, float]  # (x0, y0, x1, y1) 单位：pt
    font_name: str = ""              # 原始字体名
    font_size: float = 10.0          # 字号（pt）
    font_flags: int = 0              # 字体样式标志（PyMuPDF 格式）
    block_type: str = "body"         # 文本块类型
    is_bold: bool = False
    is_italic: bool = False
    column_id: int = 0              # XY-Cut 栏检测分配的栏编号（0=单栏/左栏, 1=右栏, …）
    block_id: str = ""              # 稳定标识（p{page_num}_b{counter}），替换脆弱的 id(block)

    @property
    def width(self) -> float:
        return self.bbox[2] - self.bbox[0]

    @property
    def height(self) -> float:
        return self.bbox[3] - self.bbox[1]

    @property
    def center_x(self) -> float:
        return (self.bbox[0] + self.bbox[2]) / 2

    @property
    def center_y(self) -> float:
        return (self.bbox[1] + self.bbox[3]) / 2


def extract_text_blocks(
    page: fitz.Page,
    page_num: int,
    min_width: float = MIN_BLOCK_WIDTH,
    min_height: float = MIN_BLOCK_HEIGHT,
) -> list[TextBlock]:
    """
    从单个 PDF 页面提取所有文本块（带 bbox 坐标）。

    Args:
        page:       PyMuPDF Page 对象
        page_num:   页码（0-based）
        min_width:  过滤掉宽度小于此值的碎片（pt）
        min_height: 过滤掉高度小于此值的碎片（pt）

    Returns:
        TextBlock 列表
    """
    blocks = []
    text_dict = page.get_text("dict")

    # y 间距阈值：当同一行内两个 span 的 y 间距超过此倍数时，
    # 视为不同表格行，拆分到独立的 TextBlock
    Y_GAP_SPLIT_FACTOR = 3.0

    for block in text_dict.get("blocks", []):
        if block.get("type") != 0:  # 非文本块（图片等）
            continue

        for line in block.get("lines", []):
            # 收集 span 并检测需要拆分的位置
            spans_data = []
            for span in line.get("spans", []):
                span_text = span.get("text", "").strip()
                if not span_text:
                    continue
                spans_data.append({
                    "text": span_text,
                    "font": span.get("font", ""),
                    "size": span.get("size", 10.0),
                    "flags": span.get("flags", 0),
                    "bbox": span["bbox"],
                })

            if not spans_data:
                continue

            # 将 span 按 y 间距拆分为多个子组（拆分合并的表格列）
            span_groups = []
            current_group = [spans_data[0]]
            prev_y1 = spans_data[0]["bbox"][3]  # 上一个 span 的底部 y

            for sd in spans_data[1:]:
                curr_y0 = sd["bbox"][1]  # 当前 span 的顶部 y
                gap = curr_y0 - prev_y1
                font_size = sd["size"]

                if gap > font_size * Y_GAP_SPLIT_FACTOR:
                    # y 间距显著 → 新行（表格不同单元格），拆分
                    span_groups.append(current_group)
                    current_group = [sd]
                else:
                    # y 间距小 → 同一文本块内换行
                    current_group.append(sd)
                prev_y1 = sd["bbox"][3]

            span_groups.append(current_group)

            # 为每个 span 组创建 TextBlock
            for group in span_groups:
                first_sd = group[0]
                x0, y0, x1, y1 = first_sd["bbox"]

                texts = [first_sd["text"]]
                font_name = first_sd["font"]
                font_size = first_sd["size"]
                font_flags = first_sd["flags"]

                for sd in group[1:]:
                    texts.append(sd["text"])
                    sb = sd["bbox"]
                    x0 = min(x0, sb[0])
                    y0 = min(y0, sb[1])
                    x1 = max(x1, sb[2])
                    y1 = max(y1, sb[3])

                full_text = " ".join(texts)

                # 过滤碎片（表格单元格放宽宽度限制）
                w = x1 - x0
                h = y1 - y0
                effective_min_width = min_width
                if h > font_size * 2:
                    # 高而窄 → 可能是表格单元格，放宽宽度限制
                    effective_min_width = 3
                if w < effective_min_width or h < min_height:
                    continue

                blocks.append(TextBlock(
                    text=full_text,
                    page_num=page_num,
                    bbox=(x0, y0, x1, y1),
                    font_name=font_name,
                    font_size=font_size,
                    font_flags=font_flags,
                    is_bold=bool(font_flags & 2**3),
                    is_italic=bool(font_flags & 2**1),
                    block_id=f"p{page_num}_b{len(blocks)}",
                ))

    return blocks

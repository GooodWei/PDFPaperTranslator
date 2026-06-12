"""
表格提取器 — 通过标题检测表格区域，返回 bbox 用于裁剪为图片。
表格不再翻译，直接从原 PDF 裁剪为 PNG 原位保留。
"""

import re
from dataclasses import dataclass, field

from PDFPaperTranslator.pdf_extractor.text_extractor import TextBlock


@dataclass
class DetectedTable:
    """检测到的表格区域"""
    page_num: int
    bbox: tuple[float, float, float, float]  # 整个表格的边界 (x0,y0,x1,y1)
    caption: str = ""                         # 表格标题文本（如 "Table I"）
    caption_bbox: tuple = None                # 标题位置
    rows: int = 0                             # 保留字段（历史兼容）
    cols: int = 0                             # 保留字段（历史兼容）


# ---- 表格标题正则 ----
_CAPTION_PATTERNS = [
    r'^表\s*\d+',
    r'^Table\s*\d+',
    r'^Table\s*[IVX]+',
    r'^TABLE\s*\d+',
    r'^TABLE\s*[IVX]+',
]

# 标题下方的最大搜索距离（pt）
_MAX_TABLE_BELOW_CAPTION = 500

# 候选数据块的最小数量（至少 2 行 × 2 列）
_MIN_CANDIDATE_BLOCKS = 4


def detect_tables_on_page(
    text_blocks: list[TextBlock],
    page_num: int,
    page_width: float,
    page_height: float,
) -> list[DetectedTable]:
    """
    通过表格标题检测页面上的表格区域，返回裁剪用 bbox。

    算法：
      1. 扫描文本块，匹配 "Table X" / "Table I" / "表X" 等标题模式
      2. 收集标题下方附近的候选数据块
      3. 计算候选块的 union bbox 作为表格区域

    Args:
        text_blocks: 该页的文本块列表
        page_num:    页码（0-based）
        page_width:  页面宽度（pt）
        page_height: 页面高度（pt）

    Returns:
        DetectedTable 列表
    """
    tables = []

    # 1. 找标题块
    caption_blocks = []
    for block in text_blocks:
        text = block.text.strip()
        for pat in _CAPTION_PATTERNS:
            if re.match(pat, text, re.IGNORECASE):
                caption_blocks.append(block)
                break

    if not caption_blocks:
        return tables

    # 2. 为每个标题查找表格数据区域
    for cap in caption_blocks:
        cap_bottom = cap.bbox[3]
        cap_top = cap.bbox[1]
        cap_center_x = (cap.bbox[0] + cap.bbox[2]) / 2

        # 判断标题所在栏位（用于过滤跨栏候选块）
        if cap_center_x < page_width * 0.4:
            col_min_x, col_max_x = 0, page_width * 0.52
        elif cap_center_x > page_width * 0.6:
            col_min_x, col_max_x = page_width * 0.48, page_width
        else:
            col_min_x, col_max_x = 0, page_width  # 全宽表格

        # 计算同栏内容顶部（修复：cap_top 在表格跨多列时 PyMuPDF
        # 可能报告异常高的值，导致遗漏表格顶部的内容）
        col_blocks = [
            b for b in text_blocks
            if b.bbox[0] >= col_min_x - 2 and b.bbox[2] <= col_max_x + 2
            and b != cap and b.text.strip()
        ]
        col_content_top = min(b.bbox[1] for b in col_blocks) if col_blocks else 0

        # 搜索上界取 cap_top 和栏内容顶部的较小值
        search_top = min(cap_top - 5, col_content_top - 5)

        # 收集标题附近同栏的候选数据块
        candidates = [
            b for b in text_blocks
            if b.bbox[1] >= search_top                       # 从栏顶开始搜索
            and b.bbox[1] <= cap_bottom + _MAX_TABLE_BELOW_CAPTION
            and b.bbox[0] >= col_min_x - 2                  # 同栏水平范围
            and b.bbox[2] <= col_max_x + 2
            and b != cap                                   # 排除标题自身
            and b.text.strip()                             # 排除空文本
        ]

        if len(candidates) < _MIN_CANDIDATE_BLOCKS:
            continue

        # 过滤过长文本（正文段落不是表格数据）
        avg_len = sum(len(b.text) for b in candidates) / len(candidates)
        if avg_len > 60:
            continue

        # 3. 计算表格区域 union bbox
        all_x0 = min(b.bbox[0] for b in candidates)
        all_y0 = min(b.bbox[1] for b in candidates)
        all_x1 = max(b.bbox[2] for b in candidates)
        all_y1 = max(b.bbox[3] for b in candidates)

        # 4. 扩展 bbox 以包含搜索范围外的孤立窄块（表格单元格遗漏保护）
        for b in text_blocks:
            if (b.bbox[0] >= col_min_x - 2
                and b.bbox[2] <= col_max_x + 2
                and b.bbox[2] - b.bbox[0] < 30              # 窄块（表格列）
                and len(b.text.strip()) < 80                 # 短文本
                and b != cap
                and b.text.strip()):
                # 检查是否在表格 bbox 附近（50pt 内）
                near_table = (
                    abs(b.bbox[1] - all_y0) < 50 or
                    abs(b.bbox[3] - all_y1) < 50 or
                    (b.bbox[1] >= all_y0 - 10 and b.bbox[3] <= all_y1 + 10)
                )
                if near_table:
                    all_x0 = min(all_x0, b.bbox[0])
                    all_y0 = min(all_y0, b.bbox[1])
                    all_x1 = max(all_x1, b.bbox[2])
                    all_y1 = max(all_y1, b.bbox[3])

        tables.append(DetectedTable(
            page_num=page_num,
            bbox=(all_x0, all_y0, all_x1, all_y1),
            caption=cap.text,
            caption_bbox=cap.bbox,
        ))

    return tables

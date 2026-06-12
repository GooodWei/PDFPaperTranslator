"""
布局计算 — 根据原文位置和翻译后文本，计算每页元素的最终放置坐标。
这是整个项目最核心的难点模块。
"""

import os
from dataclasses import dataclass, field

from PDFPaperTranslator.pdf_extractor.text_extractor import TextBlock
from PDFPaperTranslator.pdf_extractor.image_extractor import ImageBlock
from PDFPaperTranslator._constants import (
    rects_overlap, wrap_text_to_width, MIN_FONT_SIZE, MAX_FONT_SIZE,
    HEADING_MIN_FONT_SIZE, HEADING_SHRINK_FLOOR,
    MAX_IMG_W_RATIO, MAX_IMG_H_RATIO, FONT_SHRINK_STEP, HEADING_OVERFLOW_RATIO,
)


@dataclass
class LayoutElement:
    """页面上的单个布局元素"""
    type: str               # "image" | "text" | "caption"
    bbox: tuple             # (x0, y0, x1, y1) pt
    content: str            # 图片路径 或 翻译文本
    font_size: float = 10.0
    font_flags: int = 0
    block_type: str = "body"


@dataclass
class PageLayout:
    """单页布局"""
    page_num: int
    page_width: float
    page_height: float
    elements: list[LayoutElement] = field(default_factory=list)


def calculate_page_layout(
    text_blocks: list[TextBlock],
    image_blocks: list[ImageBlock],
    translated_map: dict[str, str],
    page_width: float,
    page_height: float,
    font_name: str = "Helvetica",
    default_font_size: float = 10.0,
) -> PageLayout:
    """
    计算单页的布局。
    策略：先放置所有图片（固定位置），然后在剩余空间放置翻译文本。

    Args:
        text_blocks:     该页原文文本块
        image_blocks:    该页图片
        translated_map:  {block_id: translated_text} 翻译结果
        page_width:      页面宽度（pt）
        page_height:     页面高度（pt）
        default_font_size: 默认中文字号

    Returns:
        PageLayout 包含该页所有元素的放置信息
    """
    from reportlab.pdfbase import pdfmetrics

    elements = []
    image_regions = []       # 图片缩放后区域（文本避让应与渲染尺寸一致）

    # 插图自动缩放
    _MAX_IMG_W = page_width * MAX_IMG_W_RATIO
    _MAX_IMG_H = page_height * MAX_IMG_H_RATIO

    # 1. 先放置图片（缩放渲染，缩放后 bbox 避让文本）
    for img in image_blocks:
        ix0, iy0, ix1, iy1 = img.bbox
        iw, ih = ix1 - ix0, iy1 - iy0
        scale = min(_MAX_IMG_W / iw, _MAX_IMG_H / ih, 1.0) if iw > 0 and ih > 0 else 1.0
        if scale < 1.0:
            nw, nh = iw * scale, ih * scale
            cx, cy = (ix0 + ix1) / 2, (iy0 + iy1) / 2
            scaled_bbox = (cx - nw / 2, cy - nh / 2, cx + nw / 2, cy + nh / 2)
            print(f"[信息] 图片缩放: {os.path.basename(img.image_path)} {iw:.0f}×{ih:.0f} → {nw:.0f}×{nh:.0f} (×{scale:.2f})")
        else:
            scaled_bbox = img.bbox

        elements.append(LayoutElement(
            type="image",
            bbox=scaled_bbox,           # 缩放后渲染尺寸
            content=img.image_path,
        ))
        image_regions.append(scaled_bbox)  # 文本避让使用缩放后区域

    # 2. 放置翻译文本（段落内堆叠 + 避让图片）
    sorted_blocks = sorted(text_blocks, key=lambda b: (b.bbox[1], b.bbox[0]))

    _SHRINK_STEP = FONT_SHRINK_STEP
    _MIN_FONT_SIZE = MIN_FONT_SIZE

    col_bottom = {}              # 按栏跟踪上一文本块底部（防跨栏误推）

    for block in sorted_blocks:
        translated = translated_map.get(block.block_id, block.text)

        # 跳过空文本和不需要翻译的块
        if not translated.strip():
            continue

        # 选择字号（标题最小 12pt，正文不低于最小值）
        font_size = block.font_size if block.font_size > 0 else default_font_size
        font_size = min(font_size, float(MAX_FONT_SIZE))
        if block.block_type == "heading":
            font_size = max(font_size, float(HEADING_MIN_FONT_SIZE))
        else:
            font_size = max(font_size, _MIN_FONT_SIZE)

        # 测量翻译文本的尺寸
        x0, y0, x1, y1 = block.bbox

        # 极窄块宽度保护：仅扩展至同栏、同垂直区域的最宽块，防跨栏/跨段误扩
        raw_width = x1 - x0
        if raw_width < 80 and len(translated.strip()) > 5:
            # 约束：仅考虑 y 坐标在当前块 ±200pt 内的同栏块（避免标题匹配到远处的通栏作者行）
            _MAX_Y_DIST = 200.0
            same_col_x1 = max(
                (b.bbox[2] for b in sorted_blocks
                 if abs(b.bbox[0] - x0) < 50
                 and b.bbox[2] - b.bbox[0] > 20
                 and abs(b.bbox[1] - y0) < _MAX_Y_DIST),
                default=x1,
            )
            # 若同垂直区域找不到足够宽的块，回退到仅 x0+100（保守扩展，不跨栏）
            if same_col_x1 == x1:
                effective_x1 = max(x1, x0 + 100)
            else:
                effective_x1 = max(x1, same_col_x1, x0 + 100)
        else:
            effective_x1 = x1
        text_width = effective_x1 - x0
        line_height = font_size * 1.4

        # 计算需要的行数和高度
        lines = wrap_text_to_width(translated, text_width, font_size, font_name)
        text_height = len(lines) * line_height

        # 自动缩小字号
        # 标题：允许缩至 9pt（高于正文 6.5pt，保持一定突出度）
        # 仅在超出原始高度 50% 后触发缩小（避免小幅溢出就缩小字号）
        original_height = max(y1 - y0, 1.0)
        if block.block_type == "heading":
            while (text_height > original_height * HEADING_OVERFLOW_RATIO
                   and font_size > HEADING_SHRINK_FLOOR + 0.1):
                font_size -= _SHRINK_STEP
                font_size = max(font_size, HEADING_SHRINK_FLOOR)
                line_height = font_size * 1.4
                lines = wrap_text_to_width(translated, text_width, font_size, font_name)
                text_height = len(lines) * line_height
        else:
            while (text_height > original_height
                   and font_size > _MIN_FONT_SIZE + 0.1):
                font_size -= _SHRINK_STEP
                font_size = max(font_size, _MIN_FONT_SIZE)
                line_height = font_size * 1.4
                lines = wrap_text_to_width(translated, text_width, font_size, font_name)
                text_height = len(lines) * line_height

        # 按栏堆叠：仅同栏块重叠时才下推（防跨栏误推）
        col_key = round(x0 / 50) * 50
        prev_bottom = col_bottom.get(col_key, 0.0)
        if y0 < prev_bottom:
            start_y = prev_bottom + 2
        else:
            start_y = y0

        # 避让图片区域
        final_y = start_y
        for ox0, oy0, ox1, oy1 in image_regions:
            if rects_overlap((x0, final_y, effective_x1, final_y + text_height),
                              (ox0, oy0, ox1, oy1)):
                if final_y < oy0:
                    # 文本起始点在图片上方 → 向上约束，不推下
                    final_y = min(final_y, oy0 - text_height - 2)
                else:
                    # 文本起始点在图片范围内或下方 → 推到图片底部
                    final_y = max(final_y, oy1 + 2)

        col_bottom[col_key] = final_y + text_height

        elements.append(LayoutElement(
            type="text",
            bbox=(x0, final_y, effective_x1, final_y + text_height),
            content=translated,
            font_size=font_size,
            font_flags=block.font_flags,
            block_type=block.block_type,
        ))

    layout = PageLayout(
        page_num=text_blocks[0].page_num if text_blocks else 0,
        page_width=page_width,
        page_height=page_height,
        elements=elements,
    )

    # 重叠解决：迭代下推重叠的文本元素，避免最终 PDF 中的文本堆叠
    resolved = resolve_text_overlaps(layout)
    if resolved > 0:
        print(f"[信息] 第 {layout.page_num + 1} 页解决 {resolved} 处文本重叠")

    return layout


def _overlap_area(r1: tuple, r2: tuple) -> float:
    """计算两个矩形 (x0,y0,x1,y1) 的重叠面积（pt²），不重叠返回 0。"""
    ox0 = max(r1[0], r2[0])
    oy0 = max(r1[1], r2[1])
    ox1 = min(r1[2], r2[2])
    oy1 = min(r1[3], r2[3])
    if ox1 <= ox0 or oy1 <= oy0:
        return 0.0
    return float((ox1 - ox0) * (oy1 - oy0))


def _same_column(x0_a: float, x0_b: float, tolerance: float = 50.0) -> bool:
    """判断两个元素是否属于同一栏（x0 差距 < tolerance）。"""
    return abs(x0_a - x0_b) < tolerance


def resolve_text_overlaps(page_layout: PageLayout, max_iterations: int = 4) -> int:
    """
    迭代解决 PageLayout 中文本元素之间的重叠。

    每轮：检测 → 下推重叠元素 → 重新排序再检测，最多 max_iterations 轮。
    收敛（本轮无新冲突）时提前退出。

    下推策略：
      扫描所有 text<->text 重叠对（重叠面积 ≥ 2pt²），
      将 y 坐标更高的元素下推至紧贴上方元素底部 + 2pt。
      本轮不做级联——由外层迭代的下一轮重新排序后自然捕获被推元素的新冲突。

    页面保护：不推到 page_height - 10pt 以下。

    返回解决的冲突数。
    """
    page_h = page_layout.page_height
    elems = page_layout.elements
    resolved_count = 0

    for iteration in range(max_iterations):
        # 每轮重新按 y 排序（被推元素位置变化后顺序自然更新）
        sorted_indices = sorted(
            [i for i, e in enumerate(elems) if e.type == "text"],
            key=lambda i: (elems[i].bbox[1], elems[i].bbox[0])
        )

        conflicts_this_round = 0

        for si in range(len(sorted_indices)):
            i = sorted_indices[si]
            elem_i = elems[i]

            for sj in range(si + 1, len(sorted_indices)):
                j = sorted_indices[sj]
                elem_j = elems[j]

                if not rects_overlap(elem_i.bbox, elem_j.bbox):
                    continue

                area = _overlap_area(elem_i.bbox, elem_j.bbox)
                if area < 2.0:
                    continue

                # 确定下方元素，下推它
                if elem_i.bbox[1] <= elem_j.bbox[1]:
                    upper, lower = elem_i, elem_j
                else:
                    upper, lower = elem_j, elem_i

                # 跨栏重叠不下推：不同栏的元素即使 bbox 相交也不应互相排挤
                # （例如通栏标题与右栏正文的重叠不应把右栏推走）
                if not _same_column(upper.bbox[0], lower.bbox[0]):
                    continue

                push_distance = upper.bbox[3] - lower.bbox[1] + 2
                old_bbox = lower.bbox
                elem_h = old_bbox[3] - old_bbox[1]
                new_y0 = old_bbox[1] + push_distance
                new_y1 = old_bbox[3] + push_distance

                # 页面边界保护
                if new_y1 > page_h - 10:
                    new_y1 = page_h - 10
                    new_y0 = max(new_y1 - elem_h, old_bbox[1])

                lower.bbox = (old_bbox[0], new_y0, old_bbox[2], new_y1)
                conflicts_this_round += 1
                resolved_count += 1

        if conflicts_this_round == 0:
            break  # 收敛

    return resolved_count


def detect_overlaps(page_layout: PageLayout) -> list[dict]:
    """
    检测 PageLayout 中所有重叠的元素对，返回重叠报告。

    覆盖所有类型组合：text<->text、text<->image、image<->image。
    同时检测元素是否超出页面边界。

    在 PDF 组装阶段调用，用于诊断布局问题。
    """
    overlaps = []
    elems = page_layout.elements
    page_h = page_layout.page_height
    page_w = page_layout.page_width

    for i in range(len(elems)):
        for j in range(i + 1, len(elems)):
            if not rects_overlap(elems[i].bbox, elems[j].bbox):
                continue
            area = _overlap_area(elems[i].bbox, elems[j].bbox)
            if area < 2.0:  # 与 resolve 保持一致，忽略 <2pt² 的微小接触
                continue
            ti, tj = elems[i].type, elems[j].type
            overlaps.append({
                "page": page_layout.page_num,
                "elem_i": i, "elem_j": j,
                "type": f"{ti}<->{tj}",
                "area": round(area, 1),
                "bbox_i": tuple(round(v) for v in elems[i].bbox),
                "bbox_j": tuple(round(v) for v in elems[j].bbox),
                "preview_i": str(elems[i].content)[:80],
                "preview_j": str(elems[j].content)[:80],
            })

    # 页面边界检测：文本元素是否超出页面底部（容忍 5pt）
    for i, elem in enumerate(elems):
        if elem.type == "text" and elem.bbox[3] > page_h + 5:
            overflow_amt = elem.bbox[3] - page_h
            overlaps.append({
                "page": page_layout.page_num,
                "elem_i": i, "elem_j": -1,
                "type": "overflow↓",
                "area": round(overflow_amt, 1),
                "bbox_i": tuple(round(v) for v in elem.bbox),
                "bbox_j": (0, round(page_h), round(page_w), round(page_h)),
                "preview_i": str(elem.content)[:80],
                "preview_j": "--- page bottom ---",
            })

    return overlaps

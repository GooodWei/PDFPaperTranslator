"""
页构建器 — 将 LayoutElement 渲染到 reportlab Canvas 上。
"""

from .layout_calculator import PageLayout, LayoutElement
from .font_manager import FontManager
from .._constants import wrap_text_to_width, PAGE_MARGIN_X, PAGE_MARGIN_Y


def build_page(
    c,  # reportlab Canvas
    page_layout: PageLayout,
    font_manager: FontManager,
    page_h: float,  # 页面高度（避免访问私有 _pagesize）
):
    """渲染单页内容到 Canvas（含页边距偏移）。"""
    _render_elements(c, page_layout, font_manager, page_h)
    c.showPage()


def build_page_overlay(
    c,
    page_layout: PageLayout,
    font_manager: FontManager,
    page_h: float,
    max_region_height: float = None,
):
    """在已有背景上叠加渲染文本（用于人工标注页，不调用 showPage）。
    max_region_height: 若提供，文本高度受此限制（超出时缩字号）。"""
    for elem in page_layout.elements:
        if elem.type == "image":
            _draw_image(c, elem, page_h)
        elif elem.type == "text":
            _draw_text(c, elem, font_manager, page_h, max_region_height)


def build_page_overlay_regions(
    c,
    region_texts: list[tuple[tuple, str]],  # [((x0,y0,x1,y1), "translated text"), ...]
    font_manager: FontManager,
    page_h: float,
    scale_x: float = 1.0,
    scale_y: float = 1.0,
    orig_page_h: float = None,
):
    """在已有背景上按区域统一排版翻译文本。
    每个区域内的文本统一字号、自然换行，超区域高度自动缩小。
    """
    for (rx0, ry0, rx1, ry1), text in region_texts:
        if not text.strip():
            continue
        _draw_text_region(c, text, rx0, ry0, rx1, ry1, font_manager, page_h,
                          scale_x, scale_y, orig_page_h)


def _draw_text_region(
    c, text: str,
    rx0, ry0, rx1, ry1,  # region bbox in PDF coords
    font_manager: FontManager,
    page_h: float,
    scale_x: float = 1.0,
    scale_y: float = 1.0,
    orig_page_h: float = None,
):
    """在指定 bbox 内统一排版文本：统一字号、自然换行、自动适配区域高度。"""
    _ph = orig_page_h if orig_page_h is not None else page_h
    # Canvas 坐标
    cx = rx0 * scale_x
    cy = (_ph - ry0) * scale_y  # region top in canvas
    cw = (rx1 - rx0) * scale_x
    ch = (ry1 - ry0) * scale_y

    # 初始字号 10pt
    font_size = 10.0
    min_fs = 5.0

    while font_size > min_fs + 0.1:
        font_name = font_manager.get_font(font_size, 0)
        c.setFont(font_name, font_size)
        c.setFillColor('black')
        lines = wrap_text_to_width(text, cw, font_size, font_name)
        line_h = font_size * 1.4
        total_h = len(lines) * line_h
        if total_h <= ch:
            break
        font_size -= 0.5

    font_size = max(font_size, min_fs)
    font_name = font_manager.get_font(font_size, 0)
    c.setFont(font_name, font_size)
    c.setFillColor('black')
    lines = wrap_text_to_width(text, cw, font_size, font_name)
    line_h = font_size * 1.4

    start_y = cy - font_size  # baseline of first line
    for line in lines:
        draw_y = start_y
        if draw_y < cy - ch:
            break  # exceeded region bottom
        c.drawString(cx, draw_y, line)
        start_y -= line_h


def build_page_overlay_single(
    c,
    elem: LayoutElement,
    font_manager: FontManager,
    page_h: float,
    max_region_height: float = None,
    scale_x: float = 1.0,
    scale_y: float = 1.0,
    orig_page_h: float = None,
):
    """在已有背景上叠加渲染单个文本元素，受区域高度约束。
    scale_x/y: 背景图片拉伸比例。orig_page_h: 原始页面高度（不含 margin）。"""
    if elem.type == "text":
        _draw_text(c, elem, font_manager, page_h, max_region_height, scale_x, scale_y, orig_page_h)
    elif elem.type == "image":
        _draw_image(c, elem, page_h, scale_x, scale_y)


def _render_elements(c, page_layout, font_manager, page_h):
    """渲染 LayoutElement 列表到 Canvas（不调用 showPage）。"""
    for elem in page_layout.elements:
        if elem.type == "image":
            _draw_image(c, elem, page_h)
        elif elem.type == "text":
            _draw_text(c, elem, font_manager, page_h)


def _draw_image(c, elem: LayoutElement, page_h: float,
                scale_x: float = 1.0, scale_y: float = 1.0):
    """绘制图片。auto 页有 margin 偏移，manual 覆盖页背景已含 margin。"""
    x0, y0, x1, y1 = elem.bbox
    _mx = PAGE_MARGIN_X if scale_x == 1.0 else 0
    _my = PAGE_MARGIN_Y if scale_y == 1.0 else 0
    try:
        c.drawImage(
            elem.content,
            x0 * scale_x + _mx, (page_h - y1) * scale_y - _my,
            width=(x1 - x0) * scale_x, height=(y1 - y0) * scale_y,
            preserveAspectRatio=True, mask='auto',
        )
    except Exception as e:
        print(f"[警告] 绘制图片失败 {elem.content}: {e}")


def _draw_text(c, elem: LayoutElement, font_manager: FontManager, page_h: float,
               max_region_height: float = None,
               scale_x: float = 1.0, scale_y: float = 1.0,
               orig_page_h: float = None):
    """绘制文本块。scale_x/y 用于背景缩放对齐（manual 页）。
    orig_page_h: 原始页面高度（不含 margin），用于 manual 页 y 坐标计算。
    """
    x0, y0, x1, y1 = elem.bbox
    font_size = elem.font_size
    block_type = getattr(elem, 'block_type', 'body')

    flags = getattr(elem, 'font_flags', 0)
    if block_type == "heading":
        flags |= 8
    font_name = font_manager.get_font(font_size, flags)
    c.setFont(font_name, font_size)
    c.setFillColor('black')  # 重置（_redact_regions 可能把填充色设为白色）

    # 坐标偏移：auto 页有 margin，manual 覆盖页背景已包含 margin
    _mx = PAGE_MARGIN_X if scale_x == 1.0 else 0
    _my = PAGE_MARGIN_Y if scale_y == 1.0 else 0

    text_width = (x1 - x0) * scale_x if scale_x != 1.0 else (x1 - x0)
    lines = wrap_text_to_width(elem.content, text_width, font_size, font_name)

    # 区域高度约束（manual 页）
    if max_region_height is not None:
        avail_h = max_region_height * scale_y
        min_fs = 5.0
        while len(lines) * font_size * 1.4 > avail_h and font_size > min_fs + 0.1:
            font_size -= 0.5
            font_size = max(font_size, min_fs)
            font_name = font_manager.get_font(font_size, flags)
            c.setFont(font_name, font_size)
            lines = wrap_text_to_width(elem.content, text_width, font_size, font_name)

    line_height = font_size * 1.4
    # manual 页用原始页面高度计算 y，避免 margin 偏移
    _ph = orig_page_h if orig_page_h is not None else page_h
    start_y = (_ph - y0) * scale_y - font_size - _my

    drawn = 0
    for i, line in enumerate(lines):
        draw_y = start_y - i * line_height
        if draw_y < 0:
            clipped = len(lines) - drawn
            print(f"[警告] 第 {getattr(elem, 'block_type', '?')} 块 {clipped}/{len(lines)} 行超出页面底部被截断"
                  f" (y0={y0:.0f}, fs={font_size:.1f}, 内容: {str(elem.content)[:60]}...)")
            break
        c.drawString(x0 * scale_x + _mx, draw_y, line)
        drawn += 1

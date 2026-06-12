"""
PDF 写入器 — 组装所有页面，输出最终翻译 PDF。
支持表格渲染。
"""

from reportlab.pdfgen import canvas

from .._constants import PAGE_MARGIN_X, PAGE_MARGIN_Y
from .font_manager import FontManager
import fitz  # PyMuPDF

from .layout_calculator import calculate_page_layout, detect_overlaps
from .page_builder import build_page, build_page_overlay, build_page_overlay_single, build_page_overlay_regions


def _redact_regions(c, abs_regions: list, page_w: float, page_h: float,
                    extra_w: float, extra_h: float):
    """在 Canvas 上画白色填充矩形，擦除框选区域的原文。
    背景图片拉伸比例 = (page + margins) / page，坐标需同步缩放。
    """
    scale_x = (page_w + extra_w) / page_w
    scale_y = (page_h + extra_h) / page_h
    for rx0, ry0, rx1, ry1, _order in abs_regions:
        c.setFillColor('white')
        c.setStrokeColor('white')
        # reportlab: y 自底向上，PDF y 自顶向下
        rl_x = rx0 * scale_x
        rl_y = (page_h - ry1) * scale_y   # PDF y1 → canvas y
        rl_w = (rx1 - rx0) * scale_x
        rl_h = (ry1 - ry0) * scale_y
        c.rect(rl_x, rl_y, rl_w, rl_h, fill=1, stroke=0)


def _copy_original_page_bg(c, src_page, extra_w: float, extra_h: float):
    """将原始 PDF 页面渲染到 Canvas 上作为背景（不调用 showPage，由调用方控制）。"""
    import tempfile, os, time
    tmp = None
    try:
        pix = src_page.get_pixmap(matrix=fitz.Matrix(2, 2))
        tmp = tempfile.NamedTemporaryFile(suffix='.png', delete=False)
        tmp.close()
        pix.save(tmp.name)
        pix = None
        c.drawImage(tmp.name, 0, 0, width=src_page.rect.width + extra_w,
                    height=src_page.rect.height + extra_h)
    finally:
        if tmp:
            try:
                time.sleep(0.1)
                os.unlink(tmp.name)
            except OSError:
                pass


def _copy_original_page(c, src_page, extra_w: float, extra_h: float):
    """将原始 PDF 页面完整复制到 Canvas 上（含 showPage，用于跳过页）。"""
    _copy_original_page_bg(c, src_page, extra_w, extra_h)
    c.showPage()


def create_translated_pdf(
    all_text_blocks: list[list],
    all_image_blocks: list[list],
    page_dims: list[tuple[float, float]],
    translated_map: dict[str, str],
    output_path: str,
    font_manager: FontManager = None,
    skipped_page_data: dict = None,
) -> str:
    """
    创建翻译后的 PDF。
    表格已作为图片嵌入 all_image_blocks，无需单独处理。

    Args:
        all_text_blocks:  每页的原文文本块列表
        all_image_blocks: 每页的图片列表（含表格裁剪图）
        page_dims:        每页的 (width, height)
        translated_map:   译文映射 {block_id: translated_text}
        output_path:      输出 PDF 路径
        font_manager:     字体管理器

    Returns:
        输出 PDF 的路径
    """
    if font_manager is None:
        font_manager = FontManager()
        font_manager.find_and_register()

    num_pages = len(all_text_blocks)

    # 计算每页布局
    page_layouts = []
    for page_idx in range(num_pages):
        text_blocks = all_text_blocks[page_idx]
        image_blocks = all_image_blocks[page_idx] if page_idx < len(all_image_blocks) else []
        w, h = page_dims[page_idx]

        layout = calculate_page_layout(
            text_blocks=text_blocks,
            image_blocks=image_blocks,
            translated_map=translated_map,
            page_width=w,
            page_height=h,
            font_name=font_manager.font_name,
        )
        page_layouts.append(layout)

        # 重叠诊断报告（calculate_page_layout 已完成初步解决）
        # 仅检测 + 报告，不在此再次修复（避免双重调用破坏布局）
        remaining = detect_overlaps(layout)
        if remaining:
            text_overlaps = [o for o in remaining if o["type"] == "text<->text"]
            other_overlaps = [o for o in remaining if o["type"] != "text<->text"
                            and not o["type"].startswith("overflow")]
            overflows = [o for o in remaining if o["type"].startswith("overflow")]

            parts = []
            if text_overlaps:
                parts.append(f"{len(text_overlaps)} 处文本重叠")
            if other_overlaps:
                parts.append(f"{len(other_overlaps)} 处图文重叠")
            if overflows:
                parts.append(f"{len(overflows)} 处超出页面")

            print(f"[警告] 第 {page_idx + 1} 页存在布局问题: {', '.join(parts)}")
            for o in remaining[:5]:
                print(f"  {o['type']} ({o['area']}pt^2): {o['bbox_i']} <-> {o['bbox_j']}")
                if o['preview_j'] != "--- page bottom ---":
                    print(f"    [{o['preview_i'][:60]}]")
                    print(f"    [{o['preview_j'][:60]}]")

    # 创建 PDF（防御空文档；页边距加宽）
    if not page_dims or not page_layouts:
        raise ValueError("无法创建 PDF：没有页面数据（可能所有内容被过滤）")
    # 边距加倍（左右各一次、上下各一次）
    extra_w = PAGE_MARGIN_X * 2
    extra_h = PAGE_MARGIN_Y * 2
    first_page = page_dims[0]
    c = canvas.Canvas(output_path, pagesize=(first_page[0] + extra_w, first_page[1] + extra_h))

    # 如果存在跳过页数据，准备原始文档引用
    _skipped = skipped_page_data or {}

    for page_idx, layout in enumerate(page_layouts):
        if page_idx > 0:
            pw, ph = page_dims[page_idx]
            c.setPageSize((pw + extra_w, ph + extra_h))
        else:
            ph = first_page[1]

        skipped_entry = _skipped.get(page_idx)
        if skipped_entry is not None:
            if isinstance(skipped_entry, tuple):
                # 手动标注页：(page, abs_regions) — 擦除框选区 + 填充翻译
                src_page, abs_regions = skipped_entry
                # bg: 原始页背景（不 finalize 页面）
                _copy_original_page_bg(c, src_page, extra_w, extra_h)
                # 白色矩形擦除框选区域
                _redact_regions(c, abs_regions, src_page.rect.width, src_page.rect.height, extra_w, extra_h)
                # 按区域统一排版：pipeline 已将每个区域的块合并为一个 TextBlock，直接渲染
                pw_src = src_page.rect.width
                ph_src = src_page.rect.height
                sx = (pw_src + extra_w) / pw_src
                sy = (ph_src + extra_h) / ph_src
                canvas_h = ph_src + extra_h
                region_texts = []
                for elem in layout.elements:
                    if elem.type == "text":
                        # bbox 即为区域 bbox（pipeline 合并时设置），content 为区域内合并文本
                        region_texts.append((elem.bbox, str(elem.content)))
                build_page_overlay_regions(c, region_texts, font_manager, canvas_h, sx, sy, ph_src)
                c.showPage()
            else:
                # 跳过页：直接复制原始 PDF 页
                _copy_original_page(c, skipped_entry, extra_w, extra_h)
        else:
            build_page(c, layout, font_manager, ph + extra_h)

    c.save()
    print(f"[信息] 已生成翻译 PDF: {output_path}")
    return output_path

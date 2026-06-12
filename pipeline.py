"""
共享翻译流水线 — CLI 和 Web 共用的 PDF 翻译核心逻辑。
提取自 cli.py 和 web_server.py，消除重复代码。
"""

import os
import shutil
import tempfile

import fitz  # PyMuPDF

from PDFPaperTranslator._constants import SKIP_TRANSLATION_TYPES, rects_overlap
from PDFPaperTranslator.pdf_extractor.document import open_pdf
from PDFPaperTranslator.pdf_extractor.text_extractor import extract_text_blocks, TextBlock
from PDFPaperTranslator.pdf_extractor.image_extractor import extract_images, ImageBlock
from PDFPaperTranslator.pdf_extractor.table_extractor import detect_tables_on_page
from PDFPaperTranslator.pdf_extractor.block_grouper import group_all_pages
from PDFPaperTranslator.translation.batch_engine import translate_document, TextUnit
from PDFPaperTranslator.pdf_reconstructor.font_manager import FontManager
from PDFPaperTranslator.pdf_reconstructor.pdf_writer import create_translated_pdf


def run_translation_pipeline(
    pdf_path: str,
    output_path: str,
    api_key: str,
    model: str,
    source_lang: str,
    target_lang: str,
    progress_callback=None,
    initial_term_dict: dict = None,
    skip_translate: bool = False,
    dry_run: bool = False,
    debug_logger=None,
    mock_mode: bool = False,
    pages_to_skip: set = None,
    page_annotations: dict | None = None,
) -> dict | None:
    """
    执行完整的 PDF 翻译流水线：提取 → 分组 → 翻译 → 重建。

    Args:
        pdf_path: 输入 PDF 路径
        output_path: 输出 PDF 路径
        api_key: DeepSeek API Key
        model: 模型名称
        source_lang: 源语言代码
        target_lang: 目标语言代码
        progress_callback: 进度回调 (message: str)
        initial_term_dict: 初始术语词典
        skip_translate: 仅提取显示，不翻译
        dry_run: 提取但不调用 API
        debug_logger: API 调试日志记录器

    Returns:
        {"term_dict": {...}} 或 None（skip_translate/dry_run 时）
    """
    def _progress(msg: str):
        if progress_callback:
            progress_callback(msg)
        else:
            print(msg)

    _progress(f"打开 PDF: {pdf_path}")
    doc, doc_info = open_pdf(pdf_path)
    _progress(f"共 {doc_info.total_pages} 页")

    img_dir = tempfile.mkdtemp(prefix="pdf_img_")
    _progress(f"临时图片目录: {img_dir}")

    try:
        # 阶段1：提取 PDF 内容
        _progress("【阶段1】正在提取 PDF 内容...")
        all_text_blocks, all_image_blocks, all_tables, page_dims = [], [], [], []
        skip_set = pages_to_skip or set()
        skipped_page_data = {}  # {page_num: fitz.Page} 用于阶段4还原

        for page_num in range(doc_info.total_pages):
            page = doc[page_num]
            pw, ph = page.rect.width, page.rect.height

            # 确定该页模式
            page_ann = (page_annotations or {}).get(str(page_num), {})
            page_mode = page_ann.get("mode", "auto")

            if page_num in skip_set or page_mode == "skip":
                all_text_blocks.append([])
                all_image_blocks.append([])
                all_tables.append([])
                page_dims.append((pw, ph))
                skipped_page_data[page_num] = page
                _progress(f"  第 {page_num + 1} 页: 跳过")
                continue

            page_dims.append((pw, ph))

            if page_mode == "manual":
                # 人工标注：每个框选区域内合并文本块后翻译，保证段落连贯
                regions = page_ann.get("regions", [])
                all_text = extract_text_blocks(page, page_num)
                # 按区域收集文本块（按 y 排序）。abs_regions 用 order 做 key，支持非连续 order 值。
                region_blocks = {}
                abs_regions = {}
                for i, r in enumerate(regions):
                    rorder = r.get("order", i)
                    abs_regions[rorder] = (
                        r["x0"] * pw, r["y0"] * ph,
                        r["x1"] * pw, r["y1"] * ph, rorder)
                    region_blocks[rorder] = []
                for tb in all_text:
                    for ri, (rx0, ry0, rx1, ry1, rorder) in enumerate(abs_regions):
                        if rects_overlap(tb.bbox, (rx0, ry0, rx1, ry1)):
                            region_blocks[rorder].append(tb)
                            break
                # 每个区域：按 y 排序文本块，合并文本，创建单个 TextBlock
                merged_blocks = []
                for rorder in sorted(region_blocks.keys()):
                    blocks = sorted(region_blocks[rorder], key=lambda b: b.bbox[1])
                    if not blocks:
                        continue
                    rx0, ry0, rx1, ry1, _ = abs_regions[rorder]
                    joined_text = " ".join(b.text for b in blocks)
                    # 创建合成 TextBlock，bbox = region bbox
                    merged_blocks.append(TextBlock(
                        text=joined_text,
                        page_num=page_num,
                        bbox=(rx0, ry0, rx1, ry1),
                        font_size=blocks[0].font_size,
                        font_name=blocks[0].font_name,
                        block_type="body",
                        block_id=f"p{page_num}_region_{rorder}",
                        column_id=0,
                    ))
                all_text_blocks.append(merged_blocks)
                all_image_blocks.append([])
                all_tables.append([])
                skipped_page_data[page_num] = (page, list(abs_regions.values()))  # 转为列表兼容 pdf_writer
                total_blocks_in_regions = sum(len(v) for v in region_blocks.values())
                _progress(f"  第 {page_num + 1} 页: 人工标注 ({len(regions)} 区, {total_blocks_in_regions} 块 → {len(merged_blocks)} 段)")
                continue

            # auto 模式：全页提取
            text_blocks = extract_text_blocks(page, page_num)
            all_text_blocks.append(text_blocks)

            image_blocks = extract_images(page, doc, page_num, img_dir)
            tables = detect_tables_on_page(text_blocks, page_num, pw, ph)

            # 表格裁剪为图片
            for ti, table in enumerate(tables):
                margin = 3
                clip_rect = fitz.Rect(
                    max(0, table.bbox[0] - margin),
                    max(0, table.bbox[1] - margin),
                    min(pw, table.bbox[2] + margin),
                    min(ph, table.bbox[3] + margin),
                )
                try:
                    pix = page.get_pixmap(matrix=fitz.Matrix(4, 4), clip=clip_rect)
                    tbl_img_path = os.path.join(img_dir, f"table_p{page_num}_t{ti}.png")
                    pix.save(tbl_img_path)
                    pix = None
                    image_blocks.append(ImageBlock(
                        page_num=page_num, bbox=clip_rect,
                        image_path=tbl_img_path, xref=0, width=0, height=0,
                        caption=table.caption,
                    ))
                except Exception as e:
                    _progress(f"[警告] 表格裁剪失败 P{page_num+1}T{ti}: {e}")

            all_tables.append(tables)
            all_image_blocks.append(image_blocks)

            _progress(f"  第 {page_num + 1} 页: {len(text_blocks)} 个文本块, "
                      f"{len(image_blocks)} 张图片"
                      + (f", {len(tables)} 个表格" if tables else ""))

        # 阶段2：分组和分类
        _progress("【阶段2】正在分析版式...")
        all_text_blocks, all_image_blocks = group_all_pages(
            all_text_blocks, all_image_blocks, page_dims)

        # 收集表格 bbox 用于过滤重叠文本块
        table_bboxes = [(page_idx, table.bbox)
                        for page_idx, page_tables in enumerate(all_tables)
                        for table in page_tables]

        # 移除表格区域的文本块
        for page_idx in range(len(all_text_blocks)):
            filtered = []
            for b in all_text_blocks[page_idx]:
                overlaps_table = any(
                    tbi == page_idx and rects_overlap(b.bbox, tbbox)
                    for tbi, tbbox in table_bboxes
                )
                if not overlaps_table:
                    filtered.append(b)
            all_text_blocks[page_idx] = filtered

        total_blocks = sum(len(tb) for tb in all_text_blocks)
        total_images = sum(len(ib) for ib in all_image_blocks)
        total_tables = sum(len(t) for t in all_tables)
        _progress(f"提取完成: {doc_info.total_pages} 页, "
                  f"{total_blocks} 个文本块, {total_images} 张图片"
                  + (f", {total_tables} 个表格" if total_tables else ""))

        # 构建 TextUnit 列表
        text_units = []
        for page_idx, page_blocks in enumerate(all_text_blocks):
            for block in page_blocks:
                text_units.append(TextUnit(
                    id=block.block_id,
                    text=block.text,
                    page_num=block.page_num,
                    bbox=block.bbox,
                    font_size=block.font_size,
                    font_name=block.font_name,
                    block_type=block.block_type,
                    column_id=block.column_id,
                ))

        translatable = [u for u in text_units
                        if u.text.strip()
                        and u.block_type not in SKIP_TRANSLATION_TYPES]
        _progress(f"可翻译文本块: {len(translatable)}")

        if skip_translate:
            _progress("--skip-translate 模式，仅显示提取内容")
            for u in translatable[:20]:
                _progress(f"  [{u.block_type}] P{u.page_num + 1}: {u.text[:80]}...")
            if len(translatable) > 20:
                _progress(f"  ... 共 {len(translatable)} 个文本块")
            return None

        if dry_run:
            _progress("--dry-run 模式，不调用 API")
            _progress(f"将翻译 {len(translatable)} 个文本块")
            return None

        # 阶段3：翻译
        _progress("【阶段3】正在调用 DeepSeek API 翻译...")
        _progress(f"模型: {model}, {source_lang} → {target_lang}")

        def batch_progress(current, total, status):
            if progress_callback:
                if status.startswith("[术语]"):
                    progress_callback(status)
                else:
                    progress_callback(f"翻译进度: {current}/{total} 批 — {status}")

        result = translate_document(
            text_units=text_units,
            api_key=api_key,
            model=model,
            source_lang=source_lang,
            target_lang=target_lang,
            initial_term_dict=initial_term_dict,
            progress_callback=batch_progress,
            debug_logger=debug_logger,
            mock_mode=mock_mode,
        )

        _progress(f"翻译完成: {len(result.translated_map)} 块, "
                  f"{len(result.term_dict)} 术语, {result.retries} 次重试")

        # 阶段4&5：重建 PDF
        _progress("【阶段4】正在重建 PDF（保留图片位置）...")
        fm = FontManager()
        fm.find_and_register()

        create_translated_pdf(
            all_text_blocks=all_text_blocks,
            all_image_blocks=all_image_blocks,
            page_dims=page_dims,
            translated_map=result.translated_map,
            output_path=output_path,
            font_manager=fm,
            skipped_page_data=skipped_page_data if skipped_page_data else None,
        )

        _progress("【完成】译文 PDF 已生成")
        return {"term_dict": result.term_dict}

    except Exception as e:
        _progress(f"[错误] 翻译流水线异常: {e}")
        import traceback
        traceback.print_exc()
        raise

    finally:
        doc.close()
        try:
            shutil.rmtree(img_dir, ignore_errors=True)
        except Exception:
            pass

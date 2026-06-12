"""
文本块分组：分类文本块、关联图表标题与图片。
"""

import re

from PDFPaperTranslator._constants import (
    IMAGE_CAPTION_NEARBY, STOP_REFERENCE_SECTION_PATTERN,
    REFERENCE_POSITION_RATIO, REFERENCE_MIN_CONSECUTIVE,
    COL_GAP_RATIO, COL_WIDTH_RATIO,
)
from PDFPaperTranslator.pdf_extractor.layout_analyzer import (
    classify_block, is_reference_header, is_likely_reference_entry,
)


def _detect_columns_xycut(blocks: list, page_width: float) -> int:
    """
    使用 X 中心点聚类检测页面栏结构，为每个 block 分配 column_id。

    算法（参考 PyMuPDF/pdf_oxide ColumnAware 排序）：
      1. 收集所有有效文本块的 x 中心点，排序
      2. 寻找相邻中心点之间的最大间隙
      3. 若最大间隙 > page_width * 8%，视为栏间分隔
      4. 在间隙中点设置分割线，按 center_x 分配 column_id
      5. 递归：在子区域内继续检测（处理 3 栏）

    通用性优势：
      - 基于块中心点比投影法更鲁棒（不受行宽参差影响）
      - 自适应 1/2/3 栏和混合布局
      - 纯几何算法，不依赖语言特征

    Returns:
        检测到的栏数（1 表示单栏）。
    """
    if not blocks or page_width <= 0:
        for b in blocks:
            b.column_id = 0
        return 1

    # 过滤碎片块
    valid = [b for b in blocks if b.width > 10 and b.height > 3]
    if len(valid) < 3:
        for b in blocks:
            b.column_id = 0
        return 1

    # 步骤 1：收集 x 中心点并排序
    x_centers = sorted(
        (b.bbox[0] + b.bbox[2]) / 2 for b in valid
    )

    # 步骤 2：找到相邻中心点之间的最大间隙
    max_gap = 0.0
    gap_idx = 0
    for i in range(len(x_centers) - 1):
        gap = x_centers[i + 1] - x_centers[i]
        if gap > max_gap:
            max_gap = gap
            gap_idx = i

    # 步骤 3：间隙 > 页宽 8% 才视为栏间分隔
    min_col_gap = page_width * COL_GAP_RATIO
    if max_gap <= min_col_gap:
        for b in blocks:
            b.column_id = 0
        return 1

    # 步骤 4：在间隙中点设置分割线
    split_x = (x_centers[gap_idx] + x_centers[gap_idx + 1]) / 2

    # 步骤 5：为每个 block 分配 column_id
    for b in blocks:
        cx = (b.bbox[0] + b.bbox[2]) / 2
        b.column_id = 0 if cx < split_x else 1

    return 2


def group_blocks(
    text_blocks: list,
    image_blocks: list,
    page_width: float = 595.0,
) -> tuple:
    """
    对单页的文本块和图片进行后处理：分类文本块 + 关联图表标题与图片。
    """
    # 0. XY-Cut 栏检测（在所有分类之前，为每个块分配 column_id）
    _detect_columns_xycut(text_blocks, page_width)

    # 1. 分类文本块
    for block in text_blocks:
        block.block_type = classify_block(block)

    # 1.5 短文本行识别为标题（基于字体度量 + 位置特征，语言无关）
    # 计算该栏正文块的典型字号（中位数），用于相对字号判断
    _body_blocks = [b for b in text_blocks
                    if b.block_type == "body" and b.width > 20 and b.height > 5]
    _body_font_sizes = [b.font_size for b in _body_blocks] if _body_blocks else [10.0]
    _body_font_sizes.sort()
    _median_body_fs = _body_font_sizes[len(_body_font_sizes) // 2] if _body_font_sizes else 10.0
    _median_body_fs = max(_median_body_fs, 6.0)  # 下限保护

    for block in text_blocks:
        if block.block_type != "body":
            continue
        text = block.text.strip().rstrip('.,:;)]}）')
        if len(text) < 2 or len(text) > 60:
            continue
        # 纯数字文本跳过（如页码 "24"），但含数字+文字的标题保留（如 "3.1 Method"）
        if text.isdigit():
            continue
        if block.width > page_width * COL_WIDTH_RATIO:
            continue

        # 检测文本是否含拉丁字母（用于区分 CJK 与拉丁脚本）
        _has_ascii_alpha = any(c.isascii() and c.isalpha() for c in text)

        # 字体度量判断（语言无关）：
        # A. 字号 >= 正文中位字号的 1.3 倍（明显大于正文）
        is_larger_font = block.font_size >= _median_body_fs * 1.3
        # B. 粗体（PyMuPDF flags bit 3）且字号 >= 正文中位字号
        is_bold_heading = block.is_bold and block.font_size >= _median_body_fs
        # C. 居中文本（center_x 接近页面中心 35%-65% 区域）
        is_centered = (page_width * 0.35 < block.center_x < page_width * 0.65)

        # 全大写：仅对含拉丁字母的文本生效（CJK 无大小写，text.upper()==text 恒真 → 排除）
        is_all_upper = (_has_ascii_alpha and text == text.upper()
                        and len(text) >= 3)

        # 严格 Title Case：所有含字母的单词都以大写开头（如 "Chitosan Nanoparticles"）
        words = text.split()
        is_strict_title_case = (
            _has_ascii_alpha and len(words) >= 2
            and all(w[0].isupper() for w in words if w and w[0].isalpha())
            and not text.isupper()  # 排除全大写（已由 is_all_upper 处理）
        )

        # 综合判断：
        # 规则 1：全大写短文本（如 "POLYMERS", "INTRODUCTION"）→ 标题
        if is_all_upper:
            block.block_type = "heading"
        # 规则 2：字号显著大（>= 1.5x 正文）的短文本 → 标题（语言无关）
        elif block.font_size >= _median_body_fs * 1.5 and len(text) <= 40:
            block.block_type = "heading"
        # 规则 3：严格 Title Case + (粗体 或 字号较大 或 居中) → 标题
        elif is_strict_title_case and (is_bold_heading or is_larger_font or is_centered):
            block.block_type = "heading"
        # 规则 4：严格 Title Case + 宽度明显小于栏目典型宽度（标签式标题）
        elif is_strict_title_case and block.width < page_width * 0.25:
            block.block_type = "heading"

    # 2. 关联图表标题与图片
    for img in image_blocks:
        img_bottom = img.bbox[3]
        img_top = img.bbox[1]

        best_caption = None
        best_distance = IMAGE_CAPTION_NEARBY

        for tb in text_blocks:
            if tb.block_type != "caption":
                continue
            tb_center_y = (tb.bbox[1] + tb.bbox[3]) / 2

            if tb_center_y > img_bottom:
                dist = tb.bbox[1] - img_bottom
            elif tb_center_y < img_top:
                dist = img_top - tb.bbox[3]
            else:
                dist = abs(tb_center_y - (img_bottom + img_top) / 2)

            if dist < best_distance:
                best_distance = dist
                best_caption = tb

        if best_caption:
            img.caption = best_caption.text

    return text_blocks, image_blocks


def _mark_reference_blocks(
    all_text_blocks: list[list],
    page_dims: list,
) -> None:
    """
    状态机扫描：检测参考文献区段，将相关文本块标记为 "reference"。

    两种策略（按优先级）：
      策略 A（精确标题）：匹配 "References" 等区段标题，之后的全部
        "body"/"heading" 块标记为 "reference"，直到遇到停止标题
        （"Appendix"、"Acknowledgments" 等）。
      策略 B（位置+模式回退）：如果策略 A 未匹配到任何标题，在文档
        最后 30% 页面中检测连续参考文献条目模式，标记为 "reference"。

    只覆盖 block_type 为 "body" 或 "heading" 的块（不覆盖 caption、
    equation、table_cell）。
    """
    total_pages = len(all_text_blocks)
    in_reference = False
    found_header = False

    # ---- 策略 A：精确标题检测 ----
    for page_idx in range(total_pages):
        for block in all_text_blocks[page_idx]:
            if block.block_type not in ("body", "heading"):
                continue

            text = block.text.strip()

            # 检测参考文献区段开始
            if not in_reference and is_reference_header(text):
                in_reference = True
                found_header = True
                block.block_type = "reference"
                continue

            # 检测停止标题（Appendix 等）
            if in_reference and re.match(
                STOP_REFERENCE_SECTION_PATTERN, text, re.IGNORECASE
            ):
                in_reference = False
                continue

            if in_reference:
                block.block_type = "reference"

    if found_header:
        return  # 策略 A 成功，无需回退

    # ---- 策略 B：位置+模式回退 ----
    reference_start_page = max(0, int(total_pages * REFERENCE_POSITION_RATIO))

    for page_idx in range(reference_start_page, total_pages):
        candidate_run = 0
        run_start_idx = 0

        for block_idx, block in enumerate(all_text_blocks[page_idx]):
            if block.block_type not in ("body", "heading"):
                candidate_run = 0
                continue

            if is_likely_reference_entry(block.text):
                if candidate_run == 0:
                    run_start_idx = block_idx
                candidate_run += 1
            else:
                if candidate_run >= REFERENCE_MIN_CONSECUTIVE:
                    for i in range(run_start_idx, block_idx):
                        all_text_blocks[page_idx][i].block_type = "reference"
                candidate_run = 0

        # 页尾：处理末尾的连续参考文献条目
        if candidate_run >= REFERENCE_MIN_CONSECUTIVE:
            for i in range(run_start_idx, len(all_text_blocks[page_idx])):
                all_text_blocks[page_idx][i].block_type = "reference"


def group_all_pages(
    all_text_blocks: list[list],
    all_image_blocks: list[list],
    page_dims: list,
) -> tuple:
    """对所有页面执行分组处理。"""
    for page_idx in range(len(all_text_blocks)):
        pw = page_dims[page_idx][0] if page_idx < len(page_dims) else 595.0
        all_text_blocks[page_idx], all_image_blocks[page_idx] = group_blocks(
            all_text_blocks[page_idx],
            all_image_blocks[page_idx],
            page_width=pw,
        )

    # 后处理：参考文献区段检测（状态机扫描全部页面）
    _mark_reference_blocks(all_text_blocks, page_dims)

    return all_text_blocks, all_image_blocks

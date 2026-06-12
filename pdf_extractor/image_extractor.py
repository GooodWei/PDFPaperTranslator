"""
从 PDF 页面中提取嵌入的图片，并保存为 PNG 文件。
"""

import os
from dataclasses import dataclass

import fitz  # PyMuPDF


@dataclass
class ImageBlock:
    """PDF 中的嵌入图片信息"""
    page_num: int
    bbox: tuple[float, float, float, float]  # (x0, y0, x1, y1) pt
    image_path: str                           # 导出的图片文件路径
    xref: int                                 # PDF 内部 xref 编号
    width: int                                # 像素宽度
    height: int                               # 像素高度
    caption: str = ""                         # 关联的图表标题（后续填充）


def extract_images(
    page: fitz.Page,
    doc: fitz.Document,
    page_num: int,
    output_dir: str,
) -> list[ImageBlock]:
    """
    从单个 PDF 页面提取所有嵌入图片。

    Args:
        page:       PyMuPDF Page 对象
        doc:        PyMuPDF Document 对象（用于 xref 解析）
        page_num:   页码（0-based）
        output_dir: 图片输出目录

    Returns:
        ImageBlock 列表
    """
    image_blocks = []
    image_list = page.get_images(full=True)

    for img_idx, img_info in enumerate(image_list):
        xref = img_info[0]

        try:
            # 获取图片在页面上的位置
            rects = page.get_image_rects(xref)
            if not rects:
                continue

            # 创建 Pixmap
            pix = fitz.Pixmap(doc, xref)

            # 颜色空间转换：CMYK (n=4) 或 CMYK+Alpha (n=5) → RGB
            if pix.n >= 4:
                pix = fitz.Pixmap(fitz.csRGB, pix)

            # 保存为 PNG
            img_filename = f"page{page_num + 1}_img{img_idx + 1}.png"
            img_path = os.path.join(output_dir, img_filename)

            pix.save(img_path)

            w, h = pix.width, pix.height
            pix = None  # 释放内存

            # 使用第一个 rect 作为图片位置
            rect = rects[0]

            image_blocks.append(ImageBlock(
                page_num=page_num,
                bbox=(rect.x0, rect.y0, rect.x1, rect.y1),
                image_path=img_path,
                xref=xref,
                width=w,
                height=h,
            ))

        except Exception as e:
            print(f"[警告] 第 {page_num + 1} 页图片 {img_idx + 1} 提取失败: {e}")
            continue

    return image_blocks

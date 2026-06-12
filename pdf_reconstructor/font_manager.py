"""
字体管理 — 注册 CJK 字体用于 reportlab PDF 生成。
"""

import os
import platform
import sys

from PDFPaperTranslator._constants import FONTS_DIR, DEFAULT_FONT_NAME


class FontManager:
    """管理和注册 CJK 字体（含粗体变体）。"""

    def __init__(self):
        self.font_name = DEFAULT_FONT_NAME
        self.bold_font_name = DEFAULT_FONT_NAME  # 粗体回退到常规
        self.font_path = None
        self._registered = False

    def find_and_register(self) -> str:
        """
        查找可用 CJK 字体并注册到 reportlab，同时尝试注册粗体变体。
        优先级：项目 fonts/ 目录 > 系统字体 > 下载 Noto Sans SC

        Returns:
            注册的常规字体名称
        """
        if self._registered:
            return self.font_name

        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont

        # 尝试多个字体路径
        candidates = self._get_font_candidates()

        regular_registered = False
        for font_path, font_name in candidates:
            if os.path.exists(font_path):
                try:
                    pdfmetrics.registerFont(TTFont(font_name, font_path))
                    if not regular_registered:
                        self.font_path = font_path
                        self.font_name = font_name
                        regular_registered = True
                        print(f"[信息] 已注册 CJK 字体: {font_name} ({font_path})")
                    else:
                        # 第二个成功注册的是粗体候选
                        self.bold_font_name = font_name
                        print(f"[信息] 已注册 CJK 粗体: {font_name} ({font_path})")
                        break
                except Exception as e:
                    print(f"[警告] 注册字体 {font_path} 失败: {e}")
                    continue

        if regular_registered:
            self._registered = True
            return self.font_name

        # 如果所有候选都失败
        print("[警告] 未找到 CJK 字体，将使用 reportlab 默认字体（可能无法显示中文）")
        self._registered = True
        return self.font_name

    def get_font(self, font_size: float, font_flags: int = 0) -> str:
        """
        根据格式标志返回合适的字体名。
        font_flags bit 3 (值 8) = 粗体 → 返回粗体变体。
        """
        if font_flags & 8:  # is_bold
            return self.bold_font_name
        return self.font_name

    def _get_font_candidates(self) -> list[tuple[str, str]]:
        """获取候选字体列表 [(path, name), ...]"""
        candidates = []

        # 1. 项目 fonts/ 目录下的字体
        if os.path.exists(FONTS_DIR):
            for fname in os.listdir(FONTS_DIR):
                if fname.lower().endswith(('.ttf', '.otf')):
                    path = os.path.join(FONTS_DIR, fname)
                    name = os.path.splitext(fname)[0].replace(' ', '')
                    candidates.append((path, name))

        # 2. Windows 系统字体
        if platform.system() == "Windows":
            font_dir = r"C:\Windows\Fonts"
            system_fonts = [
                ("msyh.ttc", "MicrosoftYaHei"),       # 微软雅黑
                ("msyhbd.ttc", "MicrosoftYaHeiBold"),
                ("simsun.ttc", "SimSun"),              # 宋体
                ("simhei.ttf", "SimHei"),              # 黑体
                ("simkai.ttf", "KaiTi"),               # 楷体
                ("msyh.ttf", "MicrosoftYaHei"),
            ]
            for fname, fname_short in system_fonts:
                path = os.path.join(font_dir, fname)
                if os.path.exists(path):
                    candidates.append((path, fname_short))

        # 3. macOS 系统字体
        elif platform.system() == "Darwin":
            system_fonts = [
                ("/System/Library/Fonts/PingFang.ttc", "PingFang"),
                ("/System/Library/Fonts/STHeiti Light.ttc", "STHeiti"),
                ("/Library/Fonts/Arial Unicode.ttf", "ArialUnicode"),
            ]
            for path, name in system_fonts:
                candidates.append((path, name))

        # 4. Linux 系统字体
        else:
            system_fonts = [
                ("/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc", "NotoSansCJK"),
                ("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", "NotoSansCJK"),
                ("/usr/share/fonts/truetype/wqy/wqy-microhei.ttc", "WenQuanYi"),
                ("/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf", "DroidSans"),
            ]
            for path, name in system_fonts:
                candidates.append((path, name))

        return candidates

    def get_font_size_for_height(self, text: str, max_width: float,
                                  start_size: float = 12.0) -> float:
        """
        计算使文本适合给定宽度的字号。
        用于自动缩小长文本以适配列宽。
        """
        from reportlab.pdfbase import pdfmetrics

        min_size = 7.0
        font_size = start_size

        while font_size >= min_size:
            text_width = pdfmetrics.stringWidth(text, self.font_name, font_size)
            if text_width <= max_width:
                return font_size
            font_size -= 0.5

        return min_size

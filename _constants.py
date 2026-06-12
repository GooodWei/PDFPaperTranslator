"""
全局常量 — PDF 学术论文翻译工具共享配置。
移植自 AINovelTranslator/_constants.py，适配 EN→ZH 学术场景。
"""

import os

# ---- 语言默认值 ----
DEFAULT_SOURCE_LANG = "en"   # 默认源语言：英语
DEFAULT_TARGET_LANG = "zh"   # 默认目标语言：中文

# ---- DeepSeek API ----
DEEPSEEK_CHAT_URL = "https://api.deepseek.com/chat/completions"
MODEL_DEFAULT = "deepseek-v4-flash"
MODEL_OPTIONS = {
    "1": ("deepseek-v4-pro", "DeepSeek V4 Pro"),
    "2": ("deepseek-v4-flash", "DeepSeek V4 Flash"),
}

# ---- 文件路径 ----
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
APIKEY_FILE = os.path.join(SCRIPT_DIR, "apikey.txt")
CONFIG_FILE = os.path.join(SCRIPT_DIR, "config.json")
PROMPT_FILE = os.path.join(SCRIPT_DIR, "templates", "提示词.txt")
FONTS_DIR = os.path.join(SCRIPT_DIR, "fonts")

# ---- 批量翻译常量 ----
BATCH_CHAR_LIMIT = 8000     # 每批最大字符数（平衡批次数与重叠）
CONTEXT_COUNT = 2            # 上下文段落/文本块数
MAX_BATCHES = 25             # 最大批次数，超时自动提高 char_limit 压缩
MAX_SEGMENTS_PER_BATCH = 3   # 每批最多合并段数（每组最多 8 块 → 最多 24 个标记）
PARA_DELIM = "\n\n<<<PARA_BREAK>>>\n\n"   # 段落分隔符
LINE_BREAK_MARKER = "<<<LINE_BREAK>>>"     # 换行占位符（response_parser 用于规范化）
DICT_MARKER = "【术语词典】"                # 术语词典标记
API_RATE_LIMIT_DELAY = 0.2   # API 请求间隔（秒）
RETRY_THRESHOLD = 0.4        # 源语言占比超过此值触发重试（容忍代码混合文本中的英文变量名）
RETRY_SUCCESS_THRESHOLD = 0.2  # 重试后源语言占比低于此值视为成功

# ---- PDF 处理常量 ----
DEFAULT_FONT_NAME = "NotoSansSC"
MIN_FONT_SIZE = 5.0           # 最小可读字号（应对 5x+ 翻译膨胀极端情况）
MAX_FONT_SIZE = 14            # 最大字号
HEADING_MIN_FONT_SIZE = 12    # 标题最小字号
HEADING_SHRINK_FLOOR = 7.0    # 标题缩小下限（pt）
PAGE_MARGIN_X = 28            # 左右页边距（pt，约 1cm）
PAGE_MARGIN_Y = 36            # 上下页边距（pt，约 1.27cm）
LINE_SPACING_FACTOR = 1.4     # 行距倍数

# ---- 栏检测常量 ----
COL_GAP_RATIO = 0.08          # 栏间距最小比例（页宽百分比）
COL_WIDTH_RATIO = 0.45        # 宽度超过此比例视为正文行（非标题）

# ---- 图片缩放常量 ----
MAX_IMG_W_RATIO = 0.85        # 图片最大宽度比例
MAX_IMG_H_RATIO = 0.55        # 图片最大高度比例

# ---- 字号缩小常量 ----
FONT_SHRINK_STEP = 0.5        # 字号缩小步长（pt）
HEADING_OVERFLOW_RATIO = 1.5  # 标题溢出原始高度此倍数后触发缩小

# ---- 表格检测常量 ----
TABLE_SEARCH_BELOW_CAPTION = 500  # 标题下方表格搜索范围（pt）
TABLE_MIN_CANDIDATE_BLOCKS = 4    # 构成表格的最小候选块数
TABLE_CAPTION_CENTER_LEFT = 0.4   # 标题居中左阈值
TABLE_CAPTION_CENTER_RIGHT = 0.6  # 标题居中右阈值
TABLE_COL_MIN_LEFT = 0.48         # 栏左边界
TABLE_COL_MAX_RIGHT = 0.52        # 栏右边界
IMAGE_CAPTION_NEARBY = 25     # 图表标题与图片最大距离（pt）
MIN_BLOCK_WIDTH = 5           # 最小文本块宽度（pt），过滤碎片（需低于表格列宽）
MIN_BLOCK_HEIGHT = 5          # 最小文本块高度（pt）
# ---- 学术翻译专用 ----
# 不应翻译的段落分类（在 batch_engine 中使用）
SKIP_TRANSLATION_TYPES = {"equation", "code", "reference"}

# ---- 参考文献区段检测 ----
# 匹配参考文献区段标题（支持编号如 "5. References"）
REFERENCE_HEADER_PATTERN = (
    r'^(?:\d+[\.\s]+)?'
    r'(?:References?|REFERENCES?|'
    r'Bibliography|BIBLIOGRAPHY|'
    r'Works\s+Cited|WORKS\s+CITED|'
    r'参考文献|引用文献|參考文獻|'
    r'Literature\s+Cited|LITERATURE\s+CITED)'
    r'\s*$'
)

# 停止参考文献区段的标题（附录、致谢等之后的内容不应标记为参考文献）
STOP_REFERENCE_SECTION_PATTERN = (
    r'^(?:\d+[\.\s]+)?'
    r'(?:Appendix|Appendices|APPENDIX|APPENDICES|'
    r'Acknowledgments?|Acknowledgements?|'
    r'ACKNOWLEDGMENTS?|ACKNOWLEDGEMENTS?|'
    r'Supplement|Supplementary|SUPPLEMENT|SUPPLEMENTARY|'
    r'Declaration|Funding|Conflict\s+of\s+Interest|'
    r'Author\s+Contributions?|Data\s+Availability)'
    r'\s*$'
)

# 参考文献条目特征：以 [N] 或 [N,N] 开头
REFERENCE_ENTRY_BRACKET_PATTERN = (
    r'^\s*\[\d+(?:\s*[,，\-–—]\s*\d+)*\]'
)

# 参考文献条目内容特征：含 et al.、期刊名、DOI 等
REFERENCE_ENTRY_CONTENT_PATTERN = (
    r'(?:et\s+al\.|Journal\s+of|Proceedings\s+of|'
    r'Conference\s+on|Symposium\s+on|Workshop\s+on|'
    r'Trans\.|Transactions\s+on|'
    r'vol\.\s*\d+|pp\.\s*\d+|DOI\s*:|doi\s*:|'
    r'ISBN|arXiv|\(\d{4}\))'
)

# 参考文献区段通常在文档末尾的比例阈值
REFERENCE_POSITION_RATIO = 0.70

# 回退检测所需的最小连续参考文献条目数
REFERENCE_MIN_CONSECUTIVE = 3

# ---- 交叉引用保护 ----
# 占位符格式：Unicode 尖括号 ⟨CITE_N⟩（LLM 几乎不会修改）
CITATION_PLACEHOLDER_FMT = "⟨CITE_{n}⟩"

# 引用匹配正则（按优先级：方括号 > 括号作者-年份(et al.) > 括号作者-年份 > 叙述式）
CITATION_PATTERNS = [
    # [1], [1,2], [1-3], [1,3-5,7] — 方括号引用
    r'(?<![a-zA-Z0-9])\[\d+(?:\s*[-–—,，]\s*\d+)*\](?![a-zA-Z])',
    # (Smith et al., 2020), (Smith and Jones, 2020a)
    r'\([A-Z][a-zA-Z]+(?:\s+(?:and|&)\s+[A-Z][a-zA-Z]+)?\s+et\s+al\.?,\s*\d{4}[a-z]?\)',
    # (Smith, 2020) — 简单括号作者-年份
    r'\([A-Z][a-zA-Z]+,\s*\d{4}[a-z]?\)',
    # Smith (2020), Smith et al. (2020) — 叙述式引用
    r'\b[A-Z][a-zA-Z]+(?:\s+et\s+al\.?)?\s+\(\d{4}[a-z]?\)',
]

# ---- 术语词典导出格式标识 ----
TERMDICT_META = {"source": "PDFPaperTranslator", "version": 1}


# ---- 共享工具函数 ----

def rects_overlap(r1: tuple, r2: tuple) -> bool:
    """检查两个矩形是否重叠（(x0,y0,x1,y1) 格式）。"""
    return not (
        r1[2] <= r2[0] or r1[0] >= r2[2] or
        r1[3] <= r2[1] or r1[1] >= r2[3]
    )


def wrap_text_to_width(text: str, max_width: float, font_size: float,
                       font_name: str) -> list[str]:
    """按像素宽度将文本拆分为多行（用于 reportlab 文本布局）。"""
    from reportlab.pdfbase import pdfmetrics

    lines = []
    current_line = ""

    for char in text:
        if char == '\n':
            lines.append(current_line)
            current_line = ""
            continue

        test_line = current_line + char
        try:
            test_width = pdfmetrics.stringWidth(test_line, font_name, font_size)
        except KeyError:
            test_width = max_width + 1  # 字体不可用时强制换行

        if test_width > max_width:
            if current_line:
                lines.append(current_line)
                current_line = char
            else:
                lines.append(char)
                current_line = ""
        else:
            current_line += char

    if current_line:
        lines.append(current_line)

    return lines if lines else [text]

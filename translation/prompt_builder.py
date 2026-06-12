"""
Stage 2: 提示词构建 — 从 templates/提示词.txt 加载模板，生成系统提示词和用户消息。
移植自 AINovelTranslator/prompt_builder.py，适配学术论文翻译。
"""

import re

from .._constants import PARA_DELIM, PROMPT_FILE


# ---- 从文件加载提示词模板 ----

def _load_prompts() -> dict:
    """加载 templates/提示词.txt，解析出批量翻译和单段翻译等模板。"""
    try:
        with open(PROMPT_FILE, "r", encoding="utf-8") as f:
            content = f.read()
    except FileNotFoundError:
        # 如果模板文件不存在，使用内嵌默认模板
        content = _default_prompt_content()

    sections = re.split(r'^=== (.+) ===$', content, flags=re.MULTILINE)
    templates = {}
    for i in range(1, len(sections), 2):
        name = sections[i].strip()
        body = sections[i + 1].strip()
        templates[name] = body

    return templates


def _default_prompt_content() -> str:
    """内嵌默认学术翻译提示词（在提示词文件缺失时使用）"""
    return r'''=== 学术论文批量翻译系统提示词 ===
你是一位专业的学术论文翻译。请将以下 {source_lang} 学术论文文本翻译成 {target_lang}。

用户消息中【待翻译段落】包含多个文本块，由分隔符 "<<<PARA_BREAK>>>" 分割。
请逐个翻译每个文本块，并用完全相同的分隔符 "<<<PARA_BREAK>>>" 分隔各块译文。
多个待翻译段落由分隔符 "<<<PARA_BREAK>>>" 分割。请逐个翻译每个段落，并用完全相同的分隔符 "<<<PARA_BREAK>>>" 分隔各段译文。不要添加任何编号或标记。

翻译要求：
- 准确传达学术含义，使用规范的中文学术语言
- 专业术语翻译准确一致，使用【术语词典】中的标准译法
- 保持学术论文的客观、严谨语体风格
- 完整保留数学符号、变量名、数字、单位和公式（不翻译数学符号）
- 保留引用标记：[1], [2,3], (Author, Year) 等，保持其格式不变
- ⟨CITE_N⟩ 形式的占位符受系统保护，必须完全按原样输出（含尖括号和编号），中间不得添加空格或修改字符
- 保留表格引用和图片引用（如 "Table 1", "Figure 2", "见第3节" 等）
- 对于专有名词、算法名、系统名首次出现时，可保留英文原名并附加中文翻译
- 正确处理长难句，拆分为符合中文习惯的短句

特殊处理规则：
- 章节标题：翻译为中文并保留编号
- 论文摘要：完整翻译，保持学术风格
- 图表标题：翻译为中文，格式如 "图1：实验结果对比"
- 参考文献条目：保持原文不翻译
- LaTeX 命令、环境名：不翻译，原样保留

【术语词典】规则：
- 仅记录专业学术术语、算法名、系统名、数据集名
- 不记录通用学术词汇（如 "experiment", "result", "method"）
- 每个术语独占一行，格式：英文术语 → 中文译名
- 术语词典放在所有译文之后

!!! 绝对禁止：
- 禁止输出原文（{source_lang}）文字，除非是保留的专有名词/数学符号
- 禁止输出双语对照格式
- 禁止在译文中夹杂原文注释
- 禁止改动数学公式、⟨CITE_N⟩占位符、引用标记、数字、单位

=== 学术论文单段翻译系统提示词 ===
你是一位专业的学术论文翻译。请将以下 {source_lang} 学术论文文本翻译成 {target_lang}。

要求：
- 准确传达学术含义，使用规范的中文学术语言
- 完整保留数学符号、引用标记、数字和单位
- 对专业术语使用标准译法
- 保持学术论文的客观严谨风格

!!! 绝对禁止：
- 禁止输出原文文字
- 禁止双语对照格式
- 禁止改动数学公式、⟨CITE_N⟩占位符和引用标记
- 只输出纯译文
'''


_TEMPLATES = _load_prompts()


# ---- 公开 API ----

def build_batch_system_prompt(source_lang: str, target_lang: str) -> str:
    """构建批量翻译的系统提示词（含术语词典规则）。"""
    template = _TEMPLATES.get("学术论文批量翻译系统提示词", "")
    if not template:
        raise RuntimeError("未找到批量翻译提示词模板")
    return template.format(source_lang=source_lang, target_lang=target_lang)


def build_user_message(
    term_dict: dict,
    context_before: list,
    batch_texts: list,
    context_after: list,
) -> tuple[str, dict[str, str]]:
    """
    构建用户消息。

    结构：【待翻译段落】→ 参考信息（术语词典+上下文，尾部不影响正文解析）
    术语词典放在待翻译内容之后，确保 API 响应中【术语词典】标记出现在译文末尾，
    parse_response 可正确剥离术语部分。

    Returns:
        (user_message, relevant_terms) — relevant_terms 是批次文本中实际
        匹配到的术语子集 {src: dst}，用于前端日志展示。
    """
    parts = []
    relevant_terms = {}

    # === 待翻译内容（主体） ===
    parts.append("【待翻译段落】")
    parts.append(PARA_DELIM.join(batch_texts))

    # === 参考信息（尾部，不影响 parse_response 对正文的解析） ===
    ref_parts = []

    # 术语词典 — 只包含批次文本中实际出现的术语
    if term_dict:
        all_batch_text = " ".join(batch_texts + context_before + context_after).lower()
        relevant_terms = {
            k: v for k, v in term_dict.items()
            if k.lower() in all_batch_text
        }
        if relevant_terms:
            # 用精简格式提供术语参考（不加【术语词典】标记，避免 API 在每批响应中回显术语段浪费 ~15% 输出 token）
            term_str = "；".join(f"{k}→{v}" for k, v in relevant_terms.items())
            ref_parts.append(f"\n---\n参考译法: {term_str}")

    # 上下文（精简为每段最多 150 字符）
    ctx_texts = []
    if context_before:
        ctx_texts.append("上文: " + " | ".join(
            t[:150] for t in context_before))
    if context_after:
        ctx_texts.append("下文: " + " | ".join(
            t[:150] for t in context_after))
    if ctx_texts:
        ref_parts.append("【上下文】\n" + "\n".join(ctx_texts))

    if ref_parts:
        parts.append("".join(ref_parts))

    return "\n".join(parts), relevant_terms

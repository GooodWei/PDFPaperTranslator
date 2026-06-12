"""
CLI 入口 — 命令行界面和交互式菜单。
"""

import argparse
import os
import sys

from PDFPaperTranslator._constants import DEFAULT_SOURCE_LANG, DEFAULT_TARGET_LANG, MODEL_DEFAULT
from PDFPaperTranslator.config import resolve_config, load_config
from PDFPaperTranslator.pipeline import run_translation_pipeline


def main():
    parser = argparse.ArgumentParser(
        description="PDF 学术论文自动翻译工具 — 将英文 PDF 翻译为中文",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python -m PDFPaperTranslator --pdf paper.pdf
  python -m PDFPaperTranslator --pdf paper.pdf --output translated.pdf
  python -m PDFPaperTranslator --pdf paper.pdf --api-key sk-xxx --model deepseek-v4-pro
        """,
    )
    parser.add_argument("--pdf", dest="pdf_path", help="输入 PDF 文件路径")
    parser.add_argument("--output", dest="output_path", help="输出 PDF 文件路径（默认：输入文件名_translated.pdf）")
    parser.add_argument("--api-key", dest="api_key", help="DeepSeek API Key")
    parser.add_argument("--model", dest="model", help="DeepSeek 模型名称")
    parser.add_argument("--source-lang", dest="source_lang", default=DEFAULT_SOURCE_LANG,
                        help=f"源语言（默认：{DEFAULT_SOURCE_LANG}）")
    parser.add_argument("--target-lang", dest="target_lang", default=DEFAULT_TARGET_LANG,
                        help=f"目标语言（默认：{DEFAULT_TARGET_LANG}）")
    parser.add_argument("--skip-translate", action="store_true",
                        help="仅提取并显示 PDF 内容，不执行翻译")
    parser.add_argument("--dry-run", action="store_true",
                        help="提取 PDF 内容但不调用 API（用于测试）")

    args = parser.parse_args()

    # 解析配置
    if not args.pdf_path:
        # 交互模式
        _interactive_mode(args)
        return

    # 命令行模式
    pdf_path = args.pdf_path
    if not os.path.exists(pdf_path):
        print(f"[错误] PDF 文件不存在: {pdf_path}")
        sys.exit(1)

    # 获取 API Key 和模型（仅在需要翻译时）
    need_api = not args.dry_run and not args.skip_translate
    if need_api:
        api_key, model = resolve_config(args.api_key, args.model)
        if not api_key:
            print("[错误] 需要 API Key 才能进行翻译")
            sys.exit(1)
    else:
        api_key = None
        saved = load_config()
        model = args.model or saved.get("model", MODEL_DEFAULT)

    # 输出路径
    if args.output_path:
        output_path = args.output_path
    else:
        base = os.path.splitext(os.path.basename(pdf_path))[0]
        output_dir = os.path.dirname(pdf_path) or "."
        output_path = os.path.join(output_dir, f"{base}_translated.pdf")

    # 执行翻译流水线
    _run_pipeline(
        pdf_path=pdf_path,
        output_path=output_path,
        api_key=api_key,
        model=model,
        source_lang=args.source_lang,
        target_lang=args.target_lang,
        skip_translate=args.skip_translate,
        dry_run=args.dry_run,
    )


def _interactive_mode(args):
    """交互式菜单模式"""
    print("=" * 50)
    print("  PDF 学术论文自动翻译工具")
    print("=" * 50)
    print()

    pdf_path = input("请输入 PDF 文件路径: ").strip()
    if not pdf_path or not os.path.exists(pdf_path):
        print(f"[错误] PDF 文件不存在: {pdf_path}")
        return

    output_choice = input("输出路径（直接回车使用默认 _translated.pdf）: ").strip()
    if output_choice:
        output_path = output_choice
    else:
        base = os.path.splitext(os.path.basename(pdf_path))[0]
        output_dir = os.path.dirname(pdf_path) or "."
        output_path = os.path.join(output_dir, f"{base}_translated.pdf")

    # 获取配置
    api_key, model = resolve_config(args.api_key, args.model)
    if not api_key:
        print("[错误] 需要 API Key")
        return

    _run_pipeline(
        pdf_path=pdf_path,
        output_path=output_path,
        api_key=api_key,
        model=model,
        source_lang=DEFAULT_SOURCE_LANG,
        target_lang=DEFAULT_TARGET_LANG,
    )


def _run_pipeline(
    pdf_path: str,
    output_path: str,
    api_key: str,
    model: str,
    source_lang: str,
    target_lang: str,
    skip_translate: bool = False,
    dry_run: bool = False,
):
    """执行完整的翻译流水线（委托共享模块）"""
    # pipeline 内部已将 3 参进度包装为 1 参消息回调
    last_msg = [""]

    def cli_progress(msg: str):
        # 进度行用 \r 覆盖，非进度行正常换行
        if msg.startswith("翻译进度:"):
            print(f"\r  {msg}", end="", flush=True)
        else:
            if last_msg[0].startswith("翻译进度:"):
                print()  # 进度行结束后换行
            print(msg)
        last_msg[0] = msg

    try:
        result = run_translation_pipeline(
            pdf_path=pdf_path,
            output_path=output_path,
            api_key=api_key,
            model=model,
            source_lang=source_lang,
            target_lang=target_lang,
            progress_callback=cli_progress,
            skip_translate=skip_translate,
            dry_run=dry_run,
        )
        if result:
            print()  # 换行
            print(f"\n{'=' * 50}")
            print(f"  翻译完成！")
            print(f"  输出文件: {output_path}")
            print(f"  术语数:   {len(result['term_dict'])}")
            print(f"{'=' * 50}")

    except Exception as e:
        print(f"\n[错误] 翻译流水线异常: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()

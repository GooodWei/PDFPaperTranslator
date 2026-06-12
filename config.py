"""
配置管理 — API Key 和模型选择的加载/保存/解析。
移植自 AINovelTranslator/config.py。
"""

import json
import os
import sys

from PDFPaperTranslator._constants import APIKEY_FILE, CONFIG_FILE, MODEL_DEFAULT, MODEL_OPTIONS


def load_config() -> dict:
    """加载配置：API Key 从 apikey.txt，模型选择从 config.json"""
    config = {"api_key": None, "model": MODEL_DEFAULT}

    if os.path.exists(APIKEY_FILE):
        try:
            with open(APIKEY_FILE, "r", encoding="utf-8") as f:
                key = f.read().strip()
            if key:
                config["api_key"] = key
        except Exception:
            pass

    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                saved = json.load(f)
            if saved.get("model") in [v[0] for v in MODEL_OPTIONS.values()]:
                config["model"] = saved["model"]
        except Exception:
            pass

    return config


def save_config(api_key: str, model: str):
    """保存 API Key 和模型选择"""
    try:
        with open(APIKEY_FILE, "w", encoding="utf-8") as f:
            f.write(api_key)
    except Exception as e:
        print(f"[警告] 无法保存 API Key 到 {APIKEY_FILE}: {e}")

    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump({"model": model}, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[警告] 无法保存配置到 {CONFIG_FILE}: {e}")


def resolve_config(cli_key: str = None, cli_model: str = None) -> tuple:
    """
    按优先级获取 API Key 和模型:
      1. 命令行参数
      2. 已保存的配置文件
      3. 交互式输入
    返回 (api_key, model) 元组。
    """
    api_key = cli_key
    model = cli_model if cli_model else None
    need_prompt_key = not bool(api_key)
    need_prompt_model = not bool(model)

    if need_prompt_key or need_prompt_model:
        saved = load_config()
        if need_prompt_key and saved["api_key"]:
            api_key = saved["api_key"]
            need_prompt_key = False
            print(f"[信息] 使用已保存的 API Key (来自 {APIKEY_FILE})")
        if need_prompt_model:
            model = saved["model"]
            need_prompt_model = False
            model_name = dict((v[0], v[1]) for v in MODEL_OPTIONS.values()).get(model, model)
            print(f"[信息] 使用已保存的模型: {model_name}")

    if need_prompt_key:
        sys.stdout.write("请输入 DeepSeek API Key: ")
        sys.stdout.flush()
        api_key = input().strip()
        if not api_key:
            print("[错误] API Key 不能为空")
            return None, None

    if need_prompt_model:
        print()
        print("选择模型（直接回车默认 DeepSeek V4 Flash）:")
        for k, (mid, mname) in MODEL_OPTIONS.items():
            default_mark = " ← 默认" if k == "2" else ""
            print(f"  {k}. {mname}{default_mark}")
        sys.stdout.write("请选择 (1/2): ")
        sys.stdout.flush()
        choice = input().strip()
        if not choice:
            choice = "2"
        if choice in MODEL_OPTIONS:
            model = MODEL_OPTIONS[choice][0]
        else:
            print(f"[提示] 无效选择，使用默认: {MODEL_OPTIONS['2'][1]}")
            model = MODEL_OPTIONS["2"][0]

    if api_key and model:
        save_config(api_key, model)

    return api_key, model

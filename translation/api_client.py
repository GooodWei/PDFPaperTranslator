"""
Stage 3: DeepSeek API 客户端 — 学术论文翻译器。
移植自 AINovelTranslator/translator.py。
"""

import json
import time
import requests

from PDFPaperTranslator._constants import DEEPSEEK_CHAT_URL, MODEL_DEFAULT


class DebugLogger:
    """记录 API 调用的完整请求/响应内容和时间戳，用于调试。"""

    def __init__(self):
        self.entries: list[dict] = []

    def log(self, request_payload: dict, response_data: dict,
            elapsed_ms: float, error: str = None):
        """记录一次 API 交互。"""
        entry = {
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "elapsed_ms": round(elapsed_ms, 1),
        }
        entry["request"] = request_payload
        if error:
            entry["error"] = error
        else:
            entry["response"] = response_data
        self.entries.append(entry)

    def write_to_file(self, path: str):
        """将全部记录写入 JSON 行文件（每行一个 JSON 对象）。"""
        with open(path, "w", encoding="utf-8") as f:
            for entry in self.entries:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def __len__(self):
        return len(self.entries)


class PaperTranslator:
    """学术论文翻译器，基于 DeepSeek API"""

    def __init__(self, api_key: str, model: str = MODEL_DEFAULT):
        self.api_key = api_key
        self.model = model
        self.debug_logger: DebugLogger | None = None

    def _call_api(
        self,
        system_prompt: str,
        user_message: str,
        max_tokens: int = 16384,
        timeout: int = 120,
    ) -> str:
        """底层 API 调用，返回模型响应文本"""
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            "temperature": 0.3,
            "max_tokens": max_tokens,
        }

        t_start = time.time()
        resp = requests.post(DEEPSEEK_CHAT_URL, headers=headers, json=payload, timeout=timeout)
        elapsed = (time.time() - t_start) * 1000

        if resp.status_code != 200:
            error_detail = resp.text
            try:
                error_json = resp.json()
                error_detail = error_json.get("error", {}).get("message", resp.text)
            except ValueError:
                pass
            # 记录失败请求
            if self.debug_logger is not None:
                self.debug_logger.log(payload, {}, elapsed,
                                      error=f"HTTP {resp.status_code}: {error_detail}")
            raise RuntimeError(
                f"API 请求失败 (HTTP {resp.status_code}): {error_detail}"
            )

        data = resp.json()
        # 记录成功请求（隐藏 API Key）
        if self.debug_logger is not None:
            log_payload = json.loads(json.dumps(payload))
            log_payload["_masked"] = True  # 标记已脱敏
            self.debug_logger.log(log_payload, data, elapsed)

        choice = data["choices"][0]
        content = choice["message"]["content"].strip()
        if choice.get("finish_reason") == "length":
            print("[警告] API 响应被 max_tokens 截断，译文可能不完整")
        return content

    # translate() 方法已移除 — 批量翻译引擎直接使用 _call_api()

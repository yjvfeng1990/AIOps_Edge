import json
import re
import time

import requests


class LLMError(Exception):
    pass


class LLMClient:
    """OpenAI 兼容客户端：流式输出 + JSON 模式 + 瞬时错误重试。"""

    def __init__(self, llm_cfg):
        self.base_url = llm_cfg["base_url"].rstrip("/")
        self.api_key = llm_cfg["api_key"]
        self.model = llm_cfg["model"]
        self.temperature = float(llm_cfg.get("temperature", 0.2))
        self.max_tokens = int(llm_cfg.get("max_tokens", 16384))
        self.timeout = int(llm_cfg.get("timeout", 180))
        self.json_mode = bool(llm_cfg.get("json_mode", True))

    def _endpoint(self):
        if self.base_url.endswith("/chat/completions"):
            return self.base_url
        return self.base_url + "/chat/completions"

    def _payload_base(self, messages):
        return {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }

    def _headers(self):
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def _send(self, messages, use_json_mode):
        payload = self._payload_base(messages)
        if use_json_mode:
            payload["response_format"] = {"type": "json_object"}
        resp = requests.post(self._endpoint(), headers=self._headers(),
                             json=payload, timeout=self.timeout)
        if resp.status_code != 200:
            raise LLMError(f"HTTP {resp.status_code}: {resp.text[:500]}")
        try:
            data = resp.json()
            choice = data["choices"][0]
            content = choice["message"]["content"]
            if choice.get("finish_reason") == "length" or data.get("finish_reason") == "length":
                raise LLMError(
                    "输出被截断(finish_reason=length)：请调大 llm.max_tokens"
                    f"（当前 {self.max_tokens}）后重试"
                )
            return content
        except LLMError:
            raise
        except (ValueError, KeyError, IndexError, TypeError):
            raise LLMError(f"响应格式异常: {resp.text[:500]}")

    def _stream_chat(self, messages, use_json_mode, on_delta):
        payload = self._payload_base(messages)
        payload["stream"] = True
        if use_json_mode:
            payload["response_format"] = {"type": "json_object"}
        content_parts = []
        finish_reason = None
        with requests.post(self._endpoint(), headers=self._headers(),
                           json=payload, stream=True, timeout=self.timeout) as resp:
            if resp.status_code != 200:
                raise LLMError(f"HTTP {resp.status_code}: {resp.text[:300]}")
            resp.encoding = "utf-8"
            for line in resp.iter_lines(decode_unicode=True):
                if not line or not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    obj = json.loads(data)
                except ValueError:
                    continue
                choices = obj.get("choices") or [{}]
                delta = choices[0].get("delta") or {}
                fr = choices[0].get("finish_reason")
                if fr:
                    finish_reason = fr
                rc = delta.get("reasoning_content")
                if rc and on_delta:
                    on_delta("reasoning", rc)
                cc = delta.get("content")
                if cc:
                    content_parts.append(cc)
                    if on_delta:
                        on_delta("content", cc)
        if finish_reason == "length":
            raise LLMError(
                "输出被截断(finish_reason=length)：请调大 llm.max_tokens"
                f"（当前 {self.max_tokens}）后重试"
            )
        content = "".join(content_parts).strip()
        if not content:
            raise LLMError("流式响应内容为空")
        return content

    def _disable_json_if_rejected(self, err_msg):
        m = str(err_msg)
        if ("response_format" in m or "json_object" in m
                or "must contain the word" in m):
            self.json_mode = False
            return True
        return False

    def _post(self, messages, on_delta=None):
        use_json = self.json_mode
        if on_delta is not None or use_json:
            try:
                return self._stream_chat(messages, use_json, on_delta)
            except LLMError as e:
                if self._disable_json_if_rejected(str(e)):
                    return self._send(messages, False)
                if on_delta is None:
                    raise
                return self._send(messages, self.json_mode)
        return self._send(messages, False)

    def chat(self, system, user_content, retries=2, on_delta=None):
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user_content},
        ]
        return self.chat_messages(messages, retries=retries, on_delta=on_delta)

    def chat_messages(self, messages, retries=2, on_delta=None):
        last_err = None
        for attempt in range(retries + 1):
            try:
                return self._post(messages, on_delta)
            except LLMError as e:
                m = str(e)
                transient = ("HTTP 429" in m or re.search(r"HTTP 5\d\d", m)
                             or "Connection" in m or "Timeout" in m or "timed out" in m)
                if not transient:
                    raise
                last_err = e
            except (requests.ConnectionError, requests.Timeout) as e:
                last_err = LLMError(f"连接失败: {e}")
            time.sleep(min(2 * (attempt + 1), 6))
        raise last_err or LLMError("请求失败")


def _strip_strings(body):
    out = []
    in_str = False
    esc = False
    for ch in body:
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            out.append(ch)
        else:
            if ch == '"':
                in_str = True
            out.append(ch)
    return "".join(out), in_str


def _repair(body):
    body = re.sub(r",\s*([}\]])", r"\1", body)
    body = body.replace("\u201c", '"').replace("\u201d", '"')
    body = body.replace("\u2018", "'").replace("\u2019", "'")
    return body


def _balance(body):
    stripped, open_str = _strip_strings(body)
    stack = []
    pairs = {"}": "{", "]": "["}
    for ch in stripped:
        if ch in "{[":
            stack.append(ch)
        elif ch in "}]":
            if stack and stack[-1] == pairs[ch]:
                stack.pop()
    tail = '"'
    tail += "".join("}" if c == "{" else "]" for c in reversed(stack))
    if not open_str:
        tail = tail[1:]
    return body + tail


def extract_json(text):
    """从模型输出中提取并修复 JSON 对象。"""
    if not text or not text.strip():
        raise LLMError("模型返回空内容")
    t = text.strip()
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", t, re.S)
    if fence:
        t = fence.group(1).strip()
    start, end = t.find("{"), t.rfind("}")
    if start == -1 or end <= start:
        raise LLMError(f"未在模型输出中找到 JSON: {t[:200]}")
    candidates = [t[start:end + 1]]
    candidates.append(_repair(candidates[0]))
    balanced = _balance(t[start:])
    candidates.append(_repair(balanced))
    last_err = None
    for cand in candidates:
        try:
            return json.loads(cand)
        except json.JSONDecodeError as e:
            last_err = e
    raise LLMError(f"JSON 解析失败({last_err}): {candidates[0][:300]}")

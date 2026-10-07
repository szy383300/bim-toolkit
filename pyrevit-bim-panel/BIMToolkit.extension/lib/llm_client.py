# -*- coding: utf-8 -*-
"""DeepSeek (OpenAI-compatible) chat client.

Dual-compatible with:
  - IronPython 2.7  (Revit / pyRevit 按钮)
  - CPython 3       (AutoCAD COM 控制器)

IMPORTANT: NO f-strings anywhere. IronPython 2.7 does not support them.
Use .format() / % / string concatenation only.
"""
import os
import json

try:
    import urllib2 as _urllib   # IronPython 2.7
    _PY3 = False
except ImportError:
    import urllib.request as _urllib  # CPython 3
    _PY3 = True


def load_config():
    """Return dict: endpoint / model / temperature / max_tokens / api_key."""
    cfg = {
        "endpoint": "https://api.deepseek.com/v1/chat/completions",
        "model": "deepseek-chat",
        "temperature": 0.2,
        "max_tokens": 2000,
        "api_key": "",
    }
    # 1) config file next to this module
    try:
        here = os.path.dirname(os.path.abspath(__file__))
        cfg_path = os.path.join(here, "deepseek_config.json")
        if os.path.exists(cfg_path):
            with open(cfg_path, "r") as f:
                data = json.load(f)
            for k in list(cfg.keys()):
                if k in data and data[k] is not None:
                    cfg[k] = data[k]
    except Exception:
        pass
    # 2) environment variable (highest priority, avoids storing secret in file)
    env_key = os.environ.get("DEEPSEEK_API_KEY")
    if env_key:
        cfg["api_key"] = env_key
    return cfg


def _post(url, headers, body, timeout=None):
    data = json.dumps(body)
    # 无论 IronPython 2.7 还是 CPython 3，urllib 都要求 bytes
    if isinstance(data, str):
        data = data.encode("utf-8")
    req = _urllib.Request(url, data=data, headers=headers)
    if timeout is None:
        timeout = 120
    try:
        resp = _urllib.urlopen(req, timeout=timeout)
    except TypeError:
        # older IronPython urlopen may not accept timeout
        resp = _urllib.urlopen(req)
    raw = resp.read()
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    return raw


def chat(system_prompt, user_prompt, model=None, temperature=None,
         max_tokens=None, timeout=None):
    """Send a chat request; return assistant message text (str).

    timeout: 秒，None 用默认 120。Revit 按钮里务必显式传小一点 ——
             这是在 UI 线程上同步阻塞的，120 秒足以让 Revit 看起来像卡死。
    """
    cfg = load_config()
    api_key = cfg["api_key"]
    if not api_key:
        raise RuntimeError(
            "未配置 DeepSeek API Key。请设置环境变量 DEEPSEEK_API_KEY，"
            "或在 lib/deepseek_config.json 中填写 api_key 字段。"
        )
    headers = {
        "Content-Type": "application/json",
        "Authorization": "Bearer " + api_key,
    }
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
    body = {
        "model": model or cfg["model"],
        "messages": messages,
        "temperature": (cfg["temperature"] if temperature is None
                        else temperature),
        "max_tokens": (cfg["max_tokens"] if max_tokens is None
                       else max_tokens),
        "stream": False,
    }
    raw = _post(cfg["endpoint"], headers, body, timeout=timeout)
    data = json.loads(raw)
    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        raise RuntimeError(
            "DeepSeek 返回格式异常:\n" + json.dumps(data, ensure_ascii=False)[:1200]
        )


def extract_code(text, lang=None):
    """Extract the first fenced code block from LLM text.

    Returns (code_string, language) or (None, None).
    `lang` (e.g. 'lisp', 'python') filters the fence tag but if no match
    is found the first code block is returned regardless.
    """
    if not text:
        return None, None
    marker = "```"
    start = text.find(marker)
    if start < 0:
        # 无围栏：系统提示已要求“只输出代码”，把整段当作代码返回
        return text.strip(), ""
    rest = text[start + len(marker):]
    nl = rest.find("\n")
    lang_line = rest[:nl].strip().lower() if nl >= 0 else ""
    body_start = (nl + 1) if nl >= 0 else 0
    body = rest[body_start:]
    end = body.find(marker)
    code = body if end < 0 else body[:end]
    code = code.strip("\n")
    if lang:
        if lang_line and lang.lower() in lang_line:
            return code, lang_line
        # requested language not found in fence -> still return first block
    return code, lang_line

# -*- coding: utf-8 -*-
"""
AI 对话后端（OpenAI 兼容 /v1/chat/completions）。

只用标准库 urllib，不引入新依赖；请求在后台线程执行，通过 Qt 信号回主线程。
适用于任何 OpenAI 兼容端点：DeepSeek、OpenAI、Ollama、vLLM、OpenRouter 等。

配置存放在独立文件 chat.json（与 config.json 同目录），避免把 API key
写进会频繁读写的主配置：

    {
      "enabled": true,
      "base_url": "https://api.deepseek.com/v1",
      "model": "deepseek-chat",
      "api_key": "sk-...",
      "system_prompt": "...",
      "max_history": 12,
      "timeout": 30
    }
"""

from __future__ import annotations

import json
import logging
import os
import threading
import urllib.error
import urllib.request
from pathlib import Path

from PySide6.QtCore import QObject, Signal

logger = logging.getLogger(__name__)

CHAT_FILENAME = 'chat.json'

DEFAULT_BASE_URL = 'https://api.deepseek.com/v1'
DEFAULT_MODEL = 'deepseek-chat'
DEFAULT_SYSTEM_PROMPT = (
    '你是一只住在用户桌面上的桌宠，名字叫 DeepSeek 娘。'
    '性格活泼、话少但暖心。回答要口语化，控制在 60 字以内，不要用 Markdown。'
)
DEFAULT_MAX_HISTORY = 12
DEFAULT_TIMEOUT = 30

# 环境变量兜底，方便一键安装的用户不改文件也能用
ENV_API_KEY = 'DSH_PET_CHAT_API_KEY'
ENV_BASE_URL = 'DSH_PET_CHAT_BASE_URL'
ENV_MODEL = 'DSH_PET_CHAT_MODEL'


class ChatSettings:
    """chat.json 读写；API key 支持环境变量覆盖。"""

    def __init__(self, config_dir: Path | str) -> None:
        self.dir = Path(config_dir)
        self.path = self.dir / CHAT_FILENAME
        self.data: dict = {
            'enabled': True,
            'base_url': DEFAULT_BASE_URL,
            'model': DEFAULT_MODEL,
            'api_key': '',
            'system_prompt': DEFAULT_SYSTEM_PROMPT,
            'max_history': DEFAULT_MAX_HISTORY,
            'timeout': DEFAULT_TIMEOUT,
        }
        self._load()

    def _load(self) -> None:
        try:
            raw = json.loads(self.path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return
        if isinstance(raw, dict):
            for key in self.data:
                if key in raw and raw[key] is not None:
                    self.data[key] = raw[key]

    def save(self) -> None:
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                json.dumps(self.data, ensure_ascii=False, indent=2),
                encoding='utf-8',
            )
            # 含 API key，仅本人可读
            os.chmod(self.path, 0o600)
        except OSError:
            logger.warning('chat 配置写入失败: %s', self.path)

    def get(self, key: str, default=None):
        return self.data.get(key, default)

    def set(self, key: str, value) -> None:
        self.data[key] = value

    # ---------------------------------------------------------- 生效值
    def api_key(self) -> str:
        return os.environ.get(ENV_API_KEY) or str(self.data.get('api_key') or '')

    def base_url(self) -> str:
        url = os.environ.get(ENV_BASE_URL) or str(self.data.get('base_url') or '')
        return url.rstrip('/')

    def model(self) -> str:
        return os.environ.get(ENV_MODEL) or str(self.data.get('model') or DEFAULT_MODEL)

    def enabled(self) -> bool:
        return bool(self.data.get('enabled', True))

    def is_configured(self) -> bool:
        """本地端点（Ollama 等）通常不需要 key，只要有 base_url 就算配好。"""
        if not self.base_url():
            return False
        return bool(self.api_key()) or self.is_local_endpoint()

    def is_local_endpoint(self) -> bool:
        url = self.base_url()
        return ('://localhost' in url) or ('://127.0.0.1' in url) or ('://0.0.0.0' in url)

    def endpoint(self) -> str:
        base = self.base_url()
        if base.endswith('/chat/completions'):
            return base
        return f'{base}/chat/completions'


class ChatClient(QObject):
    """后台线程调用 OpenAI 兼容接口，通过信号回主线程。"""

    replied = Signal(str)          # 成功：回复文本
    failed = Signal(str)           # 失败：错误说明
    busyChanged = Signal(bool)     # 请求进行中状态

    def __init__(self, settings: ChatSettings, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.settings = settings
        self._history: list[dict] = []
        self._busy = False
        self._lock = threading.Lock()
        self._generation = 0

    # ---------------------------------------------------------- 状态
    def is_busy(self) -> bool:
        return self._busy

    def history(self) -> list[dict]:
        return list(self._history)

    def clear_history(self) -> None:
        self._history = []

    def cancel(self) -> None:
        """放弃当前在途请求的结果（线程仍会跑完，但结果被丢弃）。"""
        with self._lock:
            self._generation += 1
        self._set_busy(False)

    def _set_busy(self, busy: bool) -> None:
        if busy != self._busy:
            self._busy = busy
            self.busyChanged.emit(busy)

    # ---------------------------------------------------------- 发送
    def send(self, text: str) -> bool:
        """发起一次对话；参数缺失或正在请求时返回 False。"""
        text = (text or '').strip()
        if not text:
            return False
        if self._busy:
            return False
        if not self.settings.is_configured():
            self.failed.emit('还没有配置 AI 接口，请先在「AI 对话 → 设置」里填写。')
            return False

        with self._lock:
            self._generation += 1
            generation = self._generation

        messages = self._build_messages(text)
        self._set_busy(True)
        thread = threading.Thread(
            target=self._worker,
            args=(generation, text, messages),
            daemon=True,
        )
        thread.start()
        return True

    def _build_messages(self, text: str) -> list[dict]:
        system_prompt = str(self.settings.get('system_prompt') or '').strip()
        messages: list[dict] = []
        if system_prompt:
            messages.append({'role': 'system', 'content': system_prompt})
        try:
            max_history = int(self.settings.get('max_history', DEFAULT_MAX_HISTORY))
        except (TypeError, ValueError):
            max_history = DEFAULT_MAX_HISTORY
        if max_history > 0:
            messages.extend(self._history[-max_history:])
        messages.append({'role': 'user', 'content': text})
        return messages

    def _worker(self, generation: int, question: str, messages: list[dict]) -> None:
        try:
            reply = self._request(messages)
        except Exception as exc:  # 网络/解析全部兜住，不让线程炸掉
            with self._lock:
                stale = generation != self._generation
            if stale:
                return
            self._set_busy(False)
            self.failed.emit(_friendly_error(exc))
            return

        with self._lock:
            stale = generation != self._generation
        if stale:
            return
        self._history.append({'role': 'user', 'content': question})
        self._history.append({'role': 'assistant', 'content': reply})
        self._set_busy(False)
        self.replied.emit(reply)

    def _request(self, messages: list[dict]) -> str:
        payload = json.dumps(
            {
                'model': self.settings.model(),
                'messages': messages,
                'stream': False,
            },
            ensure_ascii=False,
        ).encode('utf-8')

        headers = {
            'Content-Type': 'application/json',
            'Accept': 'application/json',
        }
        key = self.settings.api_key()
        if key:
            headers['Authorization'] = f'Bearer {key}'

        try:
            timeout = float(self.settings.get('timeout', DEFAULT_TIMEOUT))
        except (TypeError, ValueError):
            timeout = DEFAULT_TIMEOUT

        request = urllib.request.Request(
            self.settings.endpoint(), data=payload, headers=headers, method='POST'
        )
        opener = self._opener()
        with opener.open(request, timeout=timeout) as response:
            body = response.read().decode('utf-8', errors='replace')
        return _extract_reply(body)

    def _opener(self) -> urllib.request.OpenerDirector:
        """本地端点绕过系统代理。

        urllib 默认读取 http_proxy/https_proxy；很多用户的 no_proxy 只写了
        localhost，导致本地 Ollama/vLLM 的请求被代理拦成 502。
        """
        if self.settings.is_local_endpoint():
            return urllib.request.build_opener(urllib.request.ProxyHandler({}))
        return urllib.request.build_opener()


def _extract_reply(body: str) -> str:
    """从 OpenAI 兼容响应里取回复文本，兼容 Ollama 的 message 结构。"""
    data = json.loads(body)
    if not isinstance(data, dict):
        raise ValueError('响应格式不是 JSON 对象')
    if 'error' in data:
        err = data['error']
        message = err.get('message') if isinstance(err, dict) else str(err)
        raise RuntimeError(message or '接口返回错误')

    choices = data.get('choices')
    if isinstance(choices, list) and choices:
        first = choices[0]
        if isinstance(first, dict):
            message = first.get('message')
            if isinstance(message, dict):
                content = message.get('content')
                if isinstance(content, str) and content.strip():
                    return content.strip()
            text = first.get('text')
            if isinstance(text, str) and text.strip():
                return text.strip()

    # Ollama /api/chat 风格
    message = data.get('message')
    if isinstance(message, dict):
        content = message.get('content')
        if isinstance(content, str) and content.strip():
            return content.strip()

    raise ValueError('响应里没有找到回复内容')


def _friendly_error(exc: Exception) -> str:
    if isinstance(exc, urllib.error.HTTPError):
        detail = ''
        try:
            raw = exc.read().decode('utf-8', errors='replace')
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                err = parsed.get('error')
                if isinstance(err, dict):
                    detail = str(err.get('message') or '')
                elif isinstance(err, str):
                    detail = err
        except Exception:
            detail = ''
        if exc.code == 401:
            return 'API key 无效或已过期（401）。'
        if exc.code == 404:
            return '接口地址不存在（404），检查 base_url 与 model。'
        if exc.code == 429:
            return '请求过于频繁或额度不足（429）。'
        return f'接口返回 HTTP {exc.code}{("：" + detail) if detail else ""}'
    if isinstance(exc, urllib.error.URLError):
        return f'网络无法连接：{exc.reason}'
    if isinstance(exc, TimeoutError):
        return '请求超时，稍后再试。'
    return f'请求失败：{exc}'

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
import ssl
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
DEFAULT_TEMPERATURE = 0.8
DEFAULT_MAX_TOKENS = 512
TEST_TIMEOUT = 10

# 环境变量兜底，方便一键安装的用户不改文件也能用
ENV_API_KEY = 'DSH_PET_CHAT_API_KEY'
ENV_BASE_URL = 'DSH_PET_CHAT_BASE_URL'
ENV_MODEL = 'DSH_PET_CHAT_MODEL'

CERT_HINT = '可在「AI 对话 → 设置」中勾选「跳过 SSL 证书验证」后重试。'


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
            'temperature': DEFAULT_TEMPERATURE,
            'max_tokens': DEFAULT_MAX_TOKENS,
            'verify_ssl': True,
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

    def verify_ssl(self) -> bool:
        return bool(self.data.get('verify_ssl', True))

    def temperature(self) -> float:
        try:
            return max(0.0, min(2.0, float(self.data.get('temperature', DEFAULT_TEMPERATURE))))
        except (TypeError, ValueError):
            return DEFAULT_TEMPERATURE

    def max_tokens(self) -> int:
        try:
            value = int(self.data.get('max_tokens', DEFAULT_MAX_TOKENS))
        except (TypeError, ValueError):
            return DEFAULT_MAX_TOKENS
        return max(0, min(32768, value))

    def timeout(self) -> float:
        try:
            return max(1.0, float(self.data.get('timeout', DEFAULT_TIMEOUT)))
        except (TypeError, ValueError):
            return float(DEFAULT_TIMEOUT)


def build_ssl_context(verify: bool) -> ssl.SSLContext | None:
    """verify=False 时返回不校验证书的上下文（本地网关/自签名/代理拦截）。"""
    if verify:
        return None
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    return context


class ChatClient(QObject):
    """后台线程调用 OpenAI 兼容接口，通过信号回主线程。"""

    replied = Signal(str)          # 成功：回复文本
    failed = Signal(str)           # 失败：错误说明
    busyChanged = Signal(bool)     # 请求进行中状态
    tested = Signal(bool, str)     # 连通性测试结果

    def __init__(self, settings: ChatSettings, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.settings = settings
        self._history: list[dict] = []
        self._busy = False
        self._testing = False
        self._lock = threading.Lock()
        self._generation = 0
        self._character_id = ''
        self._character_prompt = ''
        self._test_slot = None

    # ---------------------------------------------------------- 角色
    def set_character(self, character_id: str, manifest_prompt: str = '') -> None:
        """切换角色：清空历史并记录角色人设，避免旧角色消息串进新角色。"""
        character_id = str(character_id or '')
        if character_id == self._character_id:
            self._character_prompt = str(manifest_prompt or '')
            return
        self._character_id = character_id
        self._character_prompt = str(manifest_prompt or '')
        self.cancel()
        self._history = []

    def character_id(self) -> str:
        return self._character_id

    def effective_system_prompt(self) -> str:
        """优先级：用户自定义 > 角色 manifest > 内置默认。

        用户显式清空（设为空串）时不回退默认，尊重「不带人设」的选择。
        """
        raw = self.settings.get('system_prompt')
        user_prompt = '' if raw is None else str(raw).strip()
        if user_prompt and user_prompt != DEFAULT_SYSTEM_PROMPT:
            return user_prompt
        if not user_prompt:
            # 显式清空：角色人设仍可生效，但不套回内置默认
            return self._character_prompt.strip()
        if self._character_prompt.strip():
            return self._character_prompt.strip()
        return user_prompt

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
        system_prompt = self.effective_system_prompt().strip()
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

    def _request(self, messages: list[dict], max_tokens: int | None = None,
                 timeout: float | None = None) -> str:
        body_data: dict = {
            'model': self.settings.model(),
            'messages': messages,
            'stream': False,
            'temperature': self.settings.temperature(),
        }
        limit = self.settings.max_tokens() if max_tokens is None else max_tokens
        if limit > 0:
            body_data['max_tokens'] = limit
        payload = json.dumps(body_data, ensure_ascii=False).encode('utf-8')

        headers = {
            'Content-Type': 'application/json',
            'Accept': 'application/json',
        }
        key = self.settings.api_key()
        if key:
            headers['Authorization'] = f'Bearer {key}'

        request = urllib.request.Request(
            self.settings.endpoint(), data=payload, headers=headers, method='POST'
        )
        opener = self._opener()
        effective_timeout = self.settings.timeout() if timeout is None else timeout
        with opener.open(request, timeout=effective_timeout) as response:
            body = response.read().decode('utf-8', errors='replace')
        return _extract_reply(body)

    def _opener(self) -> urllib.request.OpenerDirector:
        """构造 opener：本地端点绕过系统代理；可选跳过证书校验。

        urllib 默认读取 http_proxy/https_proxy；很多用户的 no_proxy 只写了
        localhost，导致本地 Ollama/vLLM 的请求被代理拦成 502。
        """
        handlers: list = []
        if self.settings.is_local_endpoint():
            handlers.append(urllib.request.ProxyHandler({}))
        context = build_ssl_context(self.settings.verify_ssl())
        if context is not None:
            handlers.append(urllib.request.HTTPSHandler(context=context))
        return urllib.request.build_opener(*handlers)

    # ---------------------------------------------------------- 连通性测试
    def test_connection(self, on_done) -> bool:
        """真实发一条极小请求验证连通性（含 TLS 校验）。

        用 daemon 线程 + 回调，不用 QThread：反复点击「测试连接」时
        QThread 对象可能在运行中被销毁而崩溃。
        """
        if not self.settings.base_url():
            on_done(False, '请先填写接口地址。')
            return False
        if self._testing:
            return False
        self._testing = True

        def worker() -> None:
            try:
                reply = self._request(
                    [{'role': 'user', 'content': 'ping'}],
                    max_tokens=1,
                    timeout=TEST_TIMEOUT,
                )
            except Exception as exc:
                self._testing = False
                self.tested.emit(False, _friendly_error(exc))
                return
            self._testing = False
            preview = reply[:40] if reply else ''
            self.tested.emit(True, f'连接正常。模型回复：{preview}' if preview else '连接正常。')

        # 每次测试只保留最新回调：旧的 lambda 断开后不会再收到结果
        if self._test_slot is not None:
            try:
                self.tested.disconnect(self._test_slot)
            except (RuntimeError, TypeError):
                pass
        self._test_slot = lambda ok, message: on_done(ok, message)
        self.tested.connect(self._test_slot)
        threading.Thread(target=worker, daemon=True).start()
        return True


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
        if exc.code in (401, 403):
            return f'API key 无效或无权限（{exc.code}）。'
        if exc.code == 402:
            return '账户余额不足（402），请到服务商平台充值。'
        if exc.code == 404:
            return '接口地址不存在（404），检查 base_url 与 model。'
        if exc.code == 429:
            return '请求过于频繁或额度不足（429），稍后再试。'
        if 500 <= exc.code < 600:
            return f'服务端故障（{exc.code}），稍后重试{("：" + detail) if detail else ""}'
        return f'接口返回 HTTP {exc.code}{("：" + detail) if detail else ""}'
    if isinstance(exc, ssl.SSLCertVerificationError):
        return f'证书校验失败：{_ssl_detail(exc)}。{CERT_HINT}'
    if isinstance(exc, ssl.SSLError):
        return f'SSL 错误：{_ssl_detail(exc)}。{CERT_HINT}'
    if isinstance(exc, urllib.error.URLError):
        reason = exc.reason
        if isinstance(reason, ssl.SSLCertVerificationError):
            return f'证书校验失败：{_ssl_detail(reason)}。{CERT_HINT}'
        if isinstance(reason, ssl.SSLError):
            return f'SSL 错误：{_ssl_detail(reason)}。{CERT_HINT}'
        if isinstance(reason, TimeoutError):
            return '请求超时，稍后再试。'
        return f'网络无法连接：{reason}'
    if isinstance(exc, TimeoutError):
        return '请求超时，稍后再试。'
    return f'请求失败：{exc}'


def _ssl_detail(exc: Exception) -> str:
    """SSLCertVerificationError.verify_message 只在真实握手失败时存在。"""
    message = getattr(exc, 'verify_message', None)
    if message:
        return str(message)
    reason = getattr(exc, 'reason', None)
    if reason:
        return str(reason)
    return str(exc)

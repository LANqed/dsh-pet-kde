# -*- coding: utf-8 -*-
"""Chat 版功能测试：设置、响应解析、错误映射、可选模块探测。

不发起真实网络请求；用 monkeypatch 替换 _request。
"""

from __future__ import annotations

import json
import os
import time
import urllib.error

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from pet import chat as chat_mod  # noqa: E402
from pet import features  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture()
def settings(tmp_path):
    return chat_mod.ChatSettings(tmp_path)


# ---------------------------------------------------------------- 设置
def test_defaults_and_endpoint(settings):
    assert settings.enabled() is True
    assert settings.model() == chat_mod.DEFAULT_MODEL
    assert settings.endpoint() == chat_mod.DEFAULT_BASE_URL + "/chat/completions"


def test_endpoint_not_double_suffixed(settings):
    settings.set("base_url", "https://x.test/v1/chat/completions")
    assert settings.endpoint() == "https://x.test/v1/chat/completions"


def test_trailing_slash_stripped(settings):
    settings.set("base_url", "https://x.test/v1/")
    assert settings.endpoint() == "https://x.test/v1/chat/completions"


def test_is_configured_requires_key_for_remote(settings):
    settings.set("api_key", "")
    assert settings.is_configured() is False
    settings.set("api_key", "sk-abc")
    assert settings.is_configured() is True


def test_local_endpoint_needs_no_key(settings):
    settings.set("api_key", "")
    for url in ("http://localhost:11434/v1", "http://127.0.0.1:8000/v1"):
        settings.set("base_url", url)
        assert settings.is_configured() is True


def test_env_overrides(settings, monkeypatch):
    monkeypatch.setenv(chat_mod.ENV_API_KEY, "sk-env")
    monkeypatch.setenv(chat_mod.ENV_BASE_URL, "https://env.test/v1")
    monkeypatch.setenv(chat_mod.ENV_MODEL, "env-model")
    assert settings.api_key() == "sk-env"
    assert settings.base_url() == "https://env.test/v1"
    assert settings.model() == "env-model"


def test_settings_roundtrip_and_permissions(tmp_path):
    settings = chat_mod.ChatSettings(tmp_path)
    settings.set("api_key", "sk-secret")
    settings.set("model", "my-model")
    settings.save()

    assert oct(settings.path.stat().st_mode & 0o777) == "0o600"
    reloaded = chat_mod.ChatSettings(tmp_path)
    assert reloaded.get("api_key") == "sk-secret"
    assert reloaded.model() == "my-model"


# ---------------------------------------------------------------- 响应解析
def test_extract_openai_style():
    body = json.dumps({"choices": [{"message": {"content": " 你好 "}}]})
    assert chat_mod._extract_reply(body) == "你好"


def test_extract_ollama_style():
    body = json.dumps({"message": {"content": "本地回复"}})
    assert chat_mod._extract_reply(body) == "本地回复"


def test_extract_legacy_text_field():
    body = json.dumps({"choices": [{"text": "旧格式"}]})
    assert chat_mod._extract_reply(body) == "旧格式"


def test_extract_api_error_raises():
    body = json.dumps({"error": {"message": "quota exceeded"}})
    with pytest.raises(RuntimeError, match="quota exceeded"):
        chat_mod._extract_reply(body)


def test_extract_missing_content_raises():
    with pytest.raises(ValueError):
        chat_mod._extract_reply(json.dumps({"choices": []}))


# ---------------------------------------------------------------- 错误映射
def _http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("http://x", code, "err", {}, None)


@pytest.mark.parametrize(
    "code,fragment",
    [
        (401, "API key"),
        (403, "API key"),
        (402, "余额不足"),
        (404, "404"),
        (429, "429"),
        (500, "服务端故障"),
        (503, "服务端故障"),
        (418, "HTTP 418"),
    ],
)
def test_http_error_messages(code, fragment):
    assert fragment in chat_mod._friendly_error(_http_error(code))


def test_ssl_cert_error_carries_hint():
    import ssl

    exc = ssl.SSLCertVerificationError("CERTIFICATE_VERIFY_FAILED")
    message = chat_mod._friendly_error(exc)
    assert "证书校验失败" in message
    assert "跳过 SSL 证书验证" in message


def test_url_error_wrapping_ssl_reports_cert_hint():
    import ssl

    exc = urllib.error.URLError(ssl.SSLCertVerificationError("bad cert"))
    message = chat_mod._friendly_error(exc)
    assert "证书校验失败" in message
    assert "跳过 SSL 证书验证" in message


def test_build_ssl_context_modes():
    import ssl

    assert chat_mod.build_ssl_context(True) is None
    context = chat_mod.build_ssl_context(False)
    assert context is not None
    assert context.check_hostname is False
    assert context.verify_mode == ssl.CERT_NONE


def test_url_error_message():
    message = chat_mod._friendly_error(urllib.error.URLError("refused"))
    assert "网络无法连接" in message


def test_timeout_message():
    assert "超时" in chat_mod._friendly_error(TimeoutError())


# ---------------------------------------------------------------- 客户端
def test_send_without_config_emits_failure(qapp, settings):
    settings.set("api_key", "")
    client = chat_mod.ChatClient(settings)
    errors = []
    client.failed.connect(errors.append)
    assert client.send("hi") is False
    assert errors and "还没有配置" in errors[0]


def test_send_rejects_blank_text(qapp, settings):
    settings.set("api_key", "sk-x")
    client = chat_mod.ChatClient(settings)
    assert client.send("   ") is False


def test_history_grows_and_clears(qapp, settings, monkeypatch):
    settings.set("api_key", "sk-x")
    client = chat_mod.ChatClient(settings)
    monkeypatch.setattr(client, "_request", lambda messages: "机器回复")

    replies = []
    client.replied.connect(replies.append)
    client._worker(client._generation + 1, "问题", [])
    # generation 不匹配会被丢弃，用真实 generation 再来一次
    with client._lock:
        gen = client._generation
    client._worker(gen, "问题", [])

    assert replies == ["机器回复"]
    history = client.history()
    assert history[-2:] == [
        {"role": "user", "content": "问题"},
        {"role": "assistant", "content": "机器回复"},
    ]
    client.clear_history()
    assert client.history() == []


def test_stale_generation_result_dropped(qapp, settings, monkeypatch):
    settings.set("api_key", "sk-x")
    client = chat_mod.ChatClient(settings)
    monkeypatch.setattr(client, "_request", lambda messages: "过期回复")
    replies = []
    client.replied.connect(replies.append)
    client._worker(999, "问题", [])
    assert replies == []
    assert client.history() == []


def test_build_messages_respects_system_prompt_and_history(qapp, settings):
    settings.set("system_prompt", "你是猫")
    settings.set("max_history", 2)
    client = chat_mod.ChatClient(settings)
    client._history = [
        {"role": "user", "content": "a"},
        {"role": "assistant", "content": "b"},
        {"role": "user", "content": "c"},
        {"role": "assistant", "content": "d"},
    ]
    messages = client._build_messages("新问题")
    assert messages[0] == {"role": "system", "content": "你是猫"}
    assert messages[1:3] == [
        {"role": "user", "content": "c"},
        {"role": "assistant", "content": "d"},
    ]
    assert messages[-1] == {"role": "user", "content": "新问题"}


def test_max_history_zero_sends_only_current(qapp, settings):
    settings.set("system_prompt", "")
    settings.set("max_history", 0)
    client = chat_mod.ChatClient(settings)
    client._history = [{"role": "user", "content": "old"}]
    assert client._build_messages("现在") == [{"role": "user", "content": "现在"}]


def test_failed_request_reports_and_clears_busy(qapp, settings, monkeypatch):
    settings.set("api_key", "sk-x")
    client = chat_mod.ChatClient(settings)

    def boom(messages):
        raise urllib.error.URLError("down")

    monkeypatch.setattr(client, "_request", boom)
    errors = []
    client.failed.connect(errors.append)
    with client._lock:
        gen = client._generation
    client._worker(gen, "问题", [])
    assert errors and "网络无法连接" in errors[0]
    assert client.is_busy() is False


# ---------------------------------------------------------------- 角色隔离
def test_set_character_clears_history(qapp, settings):
    client = chat_mod.ChatClient(settings)
    client._history = [{"role": "user", "content": "旧角色消息"}]
    client.set_character("new-char", "你是新角色")
    assert client.history() == []
    assert client.character_id() == "new-char"


def test_same_character_keeps_history(qapp, settings):
    client = chat_mod.ChatClient(settings)
    client.set_character("same", "人设A")
    client._history = [{"role": "user", "content": "保留"}]
    client.set_character("same", "人设A")
    assert client.history() == [{"role": "user", "content": "保留"}]


def test_prompt_priority_user_over_manifest(qapp, settings):
    client = chat_mod.ChatClient(settings)
    client.set_character("c", "角色人设")
    settings.set("system_prompt", "用户人设")
    assert client.effective_system_prompt() == "用户人设"


def test_prompt_falls_back_to_manifest(qapp, settings):
    client = chat_mod.ChatClient(settings)
    client.set_character("c", "角色人设")
    settings.set("system_prompt", chat_mod.DEFAULT_SYSTEM_PROMPT)
    assert client.effective_system_prompt() == "角色人设"


def test_prompt_defaults_when_nothing_set(qapp, settings):
    client = chat_mod.ChatClient(settings)
    settings.set("system_prompt", chat_mod.DEFAULT_SYSTEM_PROMPT)
    assert client.effective_system_prompt() == chat_mod.DEFAULT_SYSTEM_PROMPT


def test_explicit_empty_prompt_is_respected(qapp, settings):
    client = chat_mod.ChatClient(settings)
    settings.set("system_prompt", "")
    assert client.effective_system_prompt() == ""


# ---------------------------------------------------------------- 生成参数
def test_temperature_and_max_tokens_clamped(settings):
    settings.set("temperature", 9.0)
    assert settings.temperature() == 2.0
    settings.set("temperature", -1.0)
    assert settings.temperature() == 0.0
    settings.set("max_tokens", 999999)
    assert settings.max_tokens() == 32768
    settings.set("max_tokens", -5)
    assert settings.max_tokens() == 0


def test_invalid_numbers_fall_back_to_defaults(settings):
    settings.set("temperature", "abc")
    assert settings.temperature() == chat_mod.DEFAULT_TEMPERATURE
    settings.set("max_tokens", None)
    assert settings.max_tokens() == chat_mod.DEFAULT_MAX_TOKENS
    settings.set("timeout", "oops")
    assert settings.timeout() == float(chat_mod.DEFAULT_TIMEOUT)


def test_verify_ssl_roundtrip(tmp_path):
    settings = chat_mod.ChatSettings(tmp_path)
    assert settings.verify_ssl() is True
    settings.set("verify_ssl", False)
    settings.save()
    assert chat_mod.ChatSettings(tmp_path).verify_ssl() is False


# ---------------------------------------------------------------- 连通性测试
def test_test_connection_requires_base_url(qapp, settings):
    settings.set("base_url", "")
    client = chat_mod.ChatClient(settings)
    results = []
    assert client.test_connection(lambda ok, msg: results.append((ok, msg))) is False
    assert results and results[0][0] is False


def test_test_connection_reports_failure(qapp, settings, monkeypatch):
    settings.set("base_url", "https://x.test/v1")
    client = chat_mod.ChatClient(settings)

    def boom(messages, max_tokens=None, timeout=None):
        raise urllib.error.URLError("down")

    monkeypatch.setattr(client, "_request", boom)
    results = []
    assert client.test_connection(lambda ok, msg: results.append((ok, msg))) is True

    deadline = time.time() + 5
    while time.time() < deadline and not results:
        qapp.processEvents()
        time.sleep(0.02)
    assert results and results[0][0] is False
    assert "网络无法连接" in results[0][1]


def test_test_connection_reports_success(qapp, settings, monkeypatch):
    settings.set("base_url", "https://x.test/v1")
    client = chat_mod.ChatClient(settings)
    monkeypatch.setattr(
        client, "_request", lambda messages, max_tokens=None, timeout=None: "pong"
    )
    results = []
    client.test_connection(lambda ok, msg: results.append((ok, msg)))
    deadline = time.time() + 5
    while time.time() < deadline and not results:
        qapp.processEvents()
        time.sleep(0.02)
    assert results and results[0][0] is True
    assert "连接正常" in results[0][1]


def test_repeated_test_connection_does_not_crash(qapp, settings, monkeypatch):
    """反复点击「测试连接」不应崩溃（不用 QThread，改 daemon 线程 + 信号）。"""
    settings.set("base_url", "https://x.test/v1")
    client = chat_mod.ChatClient(settings)
    monkeypatch.setattr(
        client, "_request", lambda messages, max_tokens=None, timeout=None: "ok"
    )
    for _ in range(5):
        client.test_connection(lambda ok, msg: None)
        qapp.processEvents()
        time.sleep(0.05)
    assert True  # 没有崩溃即通过


# ---------------------------------------------------------------- 可选模块
def test_chat_available_in_repo():
    """仓库内始终包含 chat 模块；无 Chat 版由安装器删除文件实现。"""
    assert features.chat_available() is True
    assert features.edition() == "chat"


def test_bubble_wraps_long_text(qapp):
    from pet.chat_ui import ChatBubble

    bubble = ChatBubble()
    try:
        bubble.set_text("测试" * 120)
        assert len(bubble._lines) > 1
        assert bubble.width() <= 320
    finally:
        bubble.close()

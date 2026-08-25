# -*- coding: utf-8 -*-
"""
可选功能探测 —— 支持「Chat 版」与「无 Chat 版」共用同一份主程序。

无 Chat 版的安装器不会复制 pet/chat.py 与 pet/chat_ui.py，
这里用一次性导入探测决定菜单是否显示 AI 对话相关项。
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_chat_checked = False
_chat_available = False


def chat_available() -> bool:
    """当前安装是否包含 AI 对话模块（结果缓存，导入失败静默降级）。"""
    global _chat_checked, _chat_available
    if _chat_checked:
        return _chat_available
    _chat_checked = True
    try:
        from . import chat  # noqa: F401
        from . import chat_ui  # noqa: F401
    except Exception as exc:
        _chat_available = False
        logger.info('未启用 AI 对话（无 Chat 版或导入失败）: %s', exc)
    else:
        _chat_available = True
    return _chat_available


def edition() -> str:
    """返回版本名，用于日志与关于信息。"""
    return 'chat' if chat_available() else 'lite'

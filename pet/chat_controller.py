# -*- coding: utf-8 -*-
"""
对话控制器 —— 把 ChatClient 与气泡/输入框接到桌宠窗口上。

只在 Chat 版存在（pet/chat.py 可导入）时被创建；无 Chat 版完全不加载。
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QObject, QTimer

from .chat import ChatClient, ChatSettings
from .chat_ui import ChatBubble, ChatInput, ChatSettingsDialog

logger = logging.getLogger(__name__)

THINKING_TEXT = '让我想想…'


def _manifest_prompt(window) -> str:
    """从角色 manifest.json 的 chat.system_prompt 取人设（可选字段）。"""
    lib = getattr(window, 'lib', None)
    manifest = getattr(lib, 'manifest', None)
    if not isinstance(manifest, dict):
        return ''
    chat_section = manifest.get('chat')
    if isinstance(chat_section, dict):
        prompt = chat_section.get('system_prompt')
        if isinstance(prompt, str):
            return prompt
    return ''


class ChatController(QObject):
    """管理输入框、气泡与请求生命周期；跟随桌宠窗口移动。"""

    def __init__(self, config_dir, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.settings = ChatSettings(config_dir)
        self.client = ChatClient(self.settings, parent=self)
        self.bubble = ChatBubble()
        self.input = ChatInput()
        self._window = None

        self.client.replied.connect(self._on_replied)
        self.client.failed.connect(self._on_failed)
        self.client.busyChanged.connect(self.input.set_busy)
        self.input.submitted.connect(self._on_submitted)

        # 桌宠会自己走动/被拖动，用低频计时器让气泡与输入框跟随
        self._follow = QTimer(self)
        self._follow.setInterval(80)
        self._follow.timeout.connect(self._reposition)

    # ---------------------------------------------------------- 绑定
    def attach(self, window) -> None:
        self._window = window
        # 角色隔离：切换角色时清空历史并采用角色 manifest 的人设
        character_id = str(getattr(window, 'cfg', None).get('character', '')) if getattr(window, 'cfg', None) else ''
        self.client.set_character(character_id, _manifest_prompt(window))

    def detach(self) -> None:
        self._follow.stop()
        self.client.cancel()
        self.bubble.hide()
        self.input.hide()
        self._window = None

    def enabled(self) -> bool:
        return self.settings.enabled()

    # ---------------------------------------------------------- 交互
    def toggle_input(self) -> None:
        if self.input.isVisible():
            self.close_input()
        else:
            self.open_input()

    def open_input(self) -> None:
        if self._window is None:
            return
        # 先定位再 show：_reposition 只处理已可见的窗口，否则会停在 (0,0)
        self.input.place_below(self._anchor_rect())
        self.input.show()
        self.input.raise_()
        self.input.activateWindow()
        self.input.focus_input()
        self._follow.start()

    def close_input(self) -> None:
        self.input.hide()
        if not self.bubble.isVisible():
            self._follow.stop()

    def say(self, text: str, auto_hide: bool = True) -> None:
        """直接让桌宠说一句话（不经过 AI）。"""
        if self._window is None:
            return
        self.bubble.set_text(text, auto_hide=auto_hide)
        # set_text 会改尺寸，定位必须在其后、show 之前
        self.bubble.place_near(self._anchor_rect())
        self.bubble.show()
        self.bubble.raise_()
        self._follow.start()

    def clear_history(self) -> None:
        self.client.clear_history()
        self.say('好，我们从头聊。')

    def open_settings(self, parent=None) -> bool:
        dialog = ChatSettingsDialog(self.settings, client=self.client, parent=parent)
        accepted = bool(dialog.exec())
        if accepted and not self.settings.enabled():
            self.detach_ui_only()
        return accepted

    def detach_ui_only(self) -> None:
        """关闭 UI 但保留控制器（用户在设置里停用对话时）。"""
        self.client.cancel()
        self.bubble.hide()
        self.input.hide()
        self._follow.stop()

    # ---------------------------------------------------------- 内部
    def _on_submitted(self, text: str) -> None:
        if not self.client.send(text):
            if self.client.is_busy():
                self.say('等我把上一句说完…')
            return
        self.say(THINKING_TEXT, auto_hide=False)

    def _on_replied(self, reply: str) -> None:
        self.say(reply)

    def _on_failed(self, message: str) -> None:
        logger.warning('AI 对话失败: %s', message)
        self.say(message)

    def _reposition(self) -> None:
        window = self._window
        if window is None:
            return
        rect = self._anchor_rect()
        if self.bubble.isVisible():
            self.bubble.place_near(rect)
        if self.input.isVisible():
            self.input.place_below(rect)
        if not self.bubble.isVisible() and not self.input.isVisible():
            self._follow.stop()

    def _anchor_rect(self):
        """定位锚点：角色可见形象的包围盒，而不是带透明留白的窗口矩形。"""
        window = self._window
        if window is None:
            return None
        getter = getattr(window, 'visible_global_rect', None)
        if getter is not None:
            return getter()
        return window.frameGeometry()

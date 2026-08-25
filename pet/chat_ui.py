# -*- coding: utf-8 -*-
"""
对话 UI —— 桌宠头顶气泡 + 输入框 + 接口设置对话框。

气泡与输入框都是独立的无边框 Tool 窗口，跟随桌宠位置，
不会破坏桌宠自身的透明/穿透/mask 逻辑。
"""

from __future__ import annotations

from PySide6.QtCore import QPoint, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from . import chat as chat_mod
from . import x11_hints
from .speech_bubble import Bubble, frameless_tool_flags

INPUT_W = 300
INPUT_GAP = 6


class ChatBubble(Bubble):
    """对话气泡：复用通用气泡实现（两版共享同一套绘制与定位）。"""


class ChatInput(QWidget):
    """跟随桌宠的输入框：回车发送，Esc 关闭。"""

    submitted = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.setWindowFlags(frameless_tool_flags())
        self.setWindowTitle('和桌宠说话')

        self._edit = QLineEdit(self)
        self._edit.setPlaceholderText('和桌宠说点什么…（Enter 发送，Esc 关闭）')
        self._edit.returnPressed.connect(self._on_submit)

        self._send = QPushButton('发送', self)
        self._send.setDefault(True)
        self._send.clicked.connect(self._on_submit)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)
        layout.addWidget(self._edit, 1)
        layout.addWidget(self._send)

        self.setFixedWidth(INPUT_W)
        self.setStyleSheet(
            'QWidget { background: rgba(252,252,254,246); border-radius: 10px; }'
            'QLineEdit { border: 1px solid rgba(0,0,0,40); border-radius: 6px;'
            ' padding: 4px 6px; background: white; }'
        )

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        QTimer.singleShot(0, lambda: x11_hints.skip_taskbar(int(self.winId())))

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() == Qt.Key.Key_Escape:
            self.hide()
            event.accept()
            return
        super().keyPressEvent(event)

    def _on_submit(self) -> None:
        text = self._edit.text().strip()
        if not text:
            return
        self._edit.clear()
        self.submitted.emit(text)

    def set_busy(self, busy: bool) -> None:
        self._edit.setEnabled(not busy)
        self._send.setEnabled(not busy)
        self._send.setText('…' if busy else '发送')

    def focus_input(self) -> None:
        self._edit.setFocus(Qt.FocusReason.OtherFocusReason)
        self._edit.selectAll()

    def place_below(self, anchor_rect) -> None:
        """放在桌宠下方；空间不足则改到上方。"""
        self.adjustSize()
        self.setFixedWidth(INPUT_W)
        x = anchor_rect.center().x() - self.width() // 2
        y = anchor_rect.bottom() + INPUT_GAP
        screen = self.screen()
        if screen is not None:
            avail = screen.availableGeometry()
            if y + self.height() > avail.bottom():
                y = anchor_rect.top() - self.height() - INPUT_GAP
            x = min(max(x, avail.left()),
                    max(avail.left(), avail.right() - self.width() + 1))
            y = min(max(y, avail.top()),
                    max(avail.top(), avail.bottom() - self.height() + 1))
        self.move(QPoint(int(x), int(y)))


class ChatSettingsDialog(QDialog):
    """AI 接口设置：base_url / model / api_key / 生成参数 / SSL / 连通性测试。"""

    def __init__(self, settings: chat_mod.ChatSettings,
                 client: chat_mod.ChatClient | None = None,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle('AI 对话设置')
        self.settings = settings
        self.client = client

        self._enabled = QCheckBox('启用 AI 对话', self)
        self._enabled.setChecked(settings.enabled())

        self._base_url = QLineEdit(str(settings.get('base_url') or ''), self)
        self._base_url.setPlaceholderText(chat_mod.DEFAULT_BASE_URL)

        self._model = QLineEdit(str(settings.get('model') or ''), self)
        self._model.setPlaceholderText(chat_mod.DEFAULT_MODEL)

        self._api_key = QLineEdit(str(settings.get('api_key') or ''), self)
        self._api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self._api_key.setPlaceholderText('sk-...（本地 Ollama 可留空）')

        self._prompt = QPlainTextEdit(str(settings.get('system_prompt') or ''), self)
        self._prompt.setFixedHeight(96)

        self._max_history = QSpinBox(self)
        self._max_history.setRange(0, 64)
        self._max_history.setValue(int(settings.get('max_history') or 0))
        self._max_history.setSuffix(' 条')

        self._timeout = QSpinBox(self)
        self._timeout.setRange(5, 300)
        self._timeout.setValue(int(settings.get('timeout') or chat_mod.DEFAULT_TIMEOUT))
        self._timeout.setSuffix(' 秒')

        self._temperature = QDoubleSpinBox(self)
        self._temperature.setRange(0.0, 2.0)
        self._temperature.setSingleStep(0.1)
        self._temperature.setDecimals(2)
        self._temperature.setValue(settings.temperature())

        self._max_tokens = QSpinBox(self)
        self._max_tokens.setRange(0, 32768)
        self._max_tokens.setSingleStep(64)
        self._max_tokens.setValue(settings.max_tokens())
        self._max_tokens.setSpecialValueText('不限制')

        self._verify_ssl = QCheckBox('跳过 SSL 证书验证（本地网关 / 自签名证书）', self)
        self._verify_ssl.setChecked(not settings.verify_ssl())

        self._test_button = QPushButton('测试连接', self)
        self._test_button.clicked.connect(self._on_test)
        self._test_label = QLabel('', self)
        self._test_label.setWordWrap(True)

        form = QFormLayout()
        form.addRow(self._enabled)
        form.addRow('接口地址', self._base_url)
        form.addRow('模型', self._model)
        form.addRow('API Key', self._api_key)
        form.addRow('人设提示', self._prompt)
        form.addRow('携带历史', self._max_history)
        form.addRow('超时', self._timeout)
        form.addRow('温度', self._temperature)
        form.addRow('最大输出', self._max_tokens)
        form.addRow(self._verify_ssl)

        test_row = QHBoxLayout()
        test_row.addWidget(self._test_button)
        test_row.addWidget(self._test_label, 1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addLayout(test_row)
        layout.addWidget(buttons)
        self.setMinimumWidth(460)

    def _collect(self) -> None:
        """把界面值写回 settings（测试连接与确定都要用当前输入）。"""
        self.settings.set('enabled', self._enabled.isChecked())
        self.settings.set('base_url', self._base_url.text().strip() or chat_mod.DEFAULT_BASE_URL)
        self.settings.set('model', self._model.text().strip() or chat_mod.DEFAULT_MODEL)
        self.settings.set('api_key', self._api_key.text().strip())
        self.settings.set('system_prompt', self._prompt.toPlainText().strip())
        self.settings.set('max_history', int(self._max_history.value()))
        self.settings.set('timeout', int(self._timeout.value()))
        self.settings.set('temperature', float(self._temperature.value()))
        self.settings.set('max_tokens', int(self._max_tokens.value()))
        self.settings.set('verify_ssl', not self._verify_ssl.isChecked())

    def _on_test(self) -> None:
        if self.client is None:
            self._test_label.setText('当前无可用客户端。')
            return
        self._collect()
        self._test_button.setEnabled(False)
        self._test_label.setText('正在测试…')

        def done(ok: bool, message: str) -> None:
            self._test_button.setEnabled(True)
            self._test_label.setText(('成功：' if ok else '失败：') + message)

        if not self.client.test_connection(done):
            self._test_button.setEnabled(True)
            self._test_label.setText('测试进行中，请稍候。')

    def accept(self) -> None:  # noqa: D102
        self._collect()
        self.settings.save()
        super().accept()

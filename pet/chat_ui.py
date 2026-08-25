# -*- coding: utf-8 -*-
"""
对话 UI —— 桌宠头顶气泡 + 输入框 + 接口设置对话框。

气泡与输入框都是独立的无边框 Tool 窗口，跟随桌宠位置，
不会破坏桌宠自身的透明/穿透/mask 逻辑。
"""

from __future__ import annotations

from PySide6.QtCore import QPoint, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from . import chat as chat_mod
from . import x11_hints

BUBBLE_MAX_W = 320
BUBBLE_PAD_H = 14
BUBBLE_PAD_V = 10
BUBBLE_RADIUS = 12
BUBBLE_TAIL_H = 9
BUBBLE_GAP = 6           # 气泡与桌宠之间的间距
BUBBLE_HIDE_MS = 9000    # 回复气泡自动消失时间

INPUT_W = 300
INPUT_GAP = 6


def _frameless_tool_flags():
    return (
        Qt.WindowType.FramelessWindowHint
        | Qt.WindowType.Tool
        | Qt.WindowType.WindowStaysOnTopHint
        | Qt.WindowType.NoDropShadowWindowHint
    )


def _wrap_text(text: str, metrics, max_width: int) -> list[str]:
    """按像素宽度换行，兼容中英文混排（无空格的中文按字符断行）。"""
    if not text:
        return ['']
    lines: list[str] = []
    for paragraph in text.split('\n'):
        if not paragraph:
            lines.append('')
            continue
        current = ''
        for char in paragraph:
            candidate = current + char
            if current and metrics.horizontalAdvance(candidate) > max_width:
                lines.append(current)
                current = char
            else:
                current = candidate
        lines.append(current)
    return lines or ['']


class ChatBubble(QWidget):
    """桌宠头顶的圆角气泡（自绘，带指向桌宠的小尾巴）。"""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowFlags(
            _frameless_tool_flags() | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAutoFillBackground(False)

        self._text = ''
        self._lines: list[str] = ['']
        self._tail_down = True
        font = QFont()
        font.setPointSizeF(max(9.0, font.pointSizeF()))
        self.setFont(font)

        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self.hide)

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        QTimer.singleShot(0, lambda: x11_hints.skip_taskbar(int(self.winId())))

    # ---------------------------------------------------------- 内容
    def set_text(self, text: str, auto_hide: bool = True) -> None:
        self._text = (text or '').strip()
        self._relayout()
        self.update()
        self._hide_timer.stop()
        if auto_hide and self._text:
            self._hide_timer.start(BUBBLE_HIDE_MS)

    def text(self) -> str:
        return self._text

    def _relayout(self) -> None:
        metrics = self.fontMetrics()
        max_text_w = BUBBLE_MAX_W - BUBBLE_PAD_H * 2
        self._lines = _wrap_text(self._text, metrics, max_text_w)
        text_w = max((metrics.horizontalAdvance(line) for line in self._lines), default=0)
        text_h = metrics.height() * max(1, len(self._lines))
        width = min(BUBBLE_MAX_W, text_w + BUBBLE_PAD_H * 2)
        height = text_h + BUBBLE_PAD_V * 2 + BUBBLE_TAIL_H
        self.setFixedSize(max(56, int(width)), max(34, int(height)))

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
        painter.fillRect(self.rect(), Qt.GlobalColor.transparent)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)

        body_h = self.height() - BUBBLE_TAIL_H
        body_top = 0.0 if self._tail_down else float(BUBBLE_TAIL_H)
        path = QPainterPath()
        path.addRoundedRect(0.5, body_top + 0.5, self.width() - 1.0, body_h - 1.0,
                            BUBBLE_RADIUS, BUBBLE_RADIUS)

        tail = QPainterPath()
        cx = self.width() / 2.0
        if self._tail_down:
            tail.moveTo(cx - 7, body_h - 1)
            tail.lineTo(cx, body_h - 1 + BUBBLE_TAIL_H)
            tail.lineTo(cx + 7, body_h - 1)
        else:
            tail.moveTo(cx - 7, BUBBLE_TAIL_H + 1)
            tail.lineTo(cx, 0.0)
            tail.lineTo(cx + 7, BUBBLE_TAIL_H + 1)
        tail.closeSubpath()

        painter.setPen(QPen(QColor(0, 0, 0, 40), 1))
        painter.setBrush(QColor(255, 255, 255, 240))
        painter.drawPath(path.united(tail))

        painter.setPen(QColor(32, 32, 36))
        metrics = self.fontMetrics()
        y = body_top + BUBBLE_PAD_V + metrics.ascent()
        for line in self._lines:
            painter.drawText(BUBBLE_PAD_H, int(y), line)
            y += metrics.height()
        painter.end()

    # ---------------------------------------------------------- 定位
    def place_near(self, anchor_rect) -> None:
        """默认放在桌宠正上方；顶部空间不足时翻到下方并反转尾巴。"""
        x = anchor_rect.center().x() - self.width() // 2
        y = anchor_rect.top() - self.height() - BUBBLE_GAP
        tail_down = True
        screen = self.screen()
        if screen is not None:
            avail = screen.availableGeometry()
            if y < avail.top():
                y = anchor_rect.bottom() + BUBBLE_GAP
                tail_down = False
            x = min(max(x, avail.left()),
                    max(avail.left(), avail.right() - self.width() + 1))
            y = min(max(y, avail.top()),
                    max(avail.top(), avail.bottom() - self.height() + 1))
        if tail_down != self._tail_down:
            self._tail_down = tail_down
            self.update()
        self.move(QPoint(int(x), int(y)))


class ChatInput(QWidget):
    """跟随桌宠的输入框：回车发送，Esc 关闭。"""

    submitted = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.setWindowFlags(_frameless_tool_flags())
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
    """AI 接口设置：base_url / model / api_key / system prompt。"""

    def __init__(self, settings: chat_mod.ChatSettings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle('AI 对话设置')
        self.settings = settings

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

        form = QFormLayout()
        form.addRow(self._enabled)
        form.addRow('接口地址', self._base_url)
        form.addRow('模型', self._model)
        form.addRow('API Key', self._api_key)
        form.addRow('人设提示', self._prompt)
        form.addRow('携带历史', self._max_history)
        form.addRow('超时', self._timeout)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)
        self.setMinimumWidth(420)

    def accept(self) -> None:  # noqa: D102
        self.settings.set('enabled', self._enabled.isChecked())
        self.settings.set('base_url', self._base_url.text().strip() or chat_mod.DEFAULT_BASE_URL)
        self.settings.set('model', self._model.text().strip() or chat_mod.DEFAULT_MODEL)
        self.settings.set('api_key', self._api_key.text().strip())
        self.settings.set('system_prompt', self._prompt.toPlainText().strip())
        self.settings.set('max_history', int(self._max_history.value()))
        self.settings.set('timeout', int(self._timeout.value()))
        self.settings.save()
        super().accept()

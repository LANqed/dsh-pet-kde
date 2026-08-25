# -*- coding: utf-8 -*-
"""
气泡组件与自言自语调度 —— 两个版本都可用（不依赖 AI 对话模块）。

- Bubble：自绘圆角气泡，尾巴指向桌宠，顶部空间不足时自动翻到下方。
- SpeechScheduler：按随机间隔让桌宠说一句本地文本。

定位以「角色当前可见形象的包围盒」为锚，而不是窗口矩形：
webm 四周有透明留白，用窗口矩形会让气泡离角色头顶太远。
"""

from __future__ import annotations

import random

from PySide6.QtCore import QObject, QPoint, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

from . import catalog
from . import x11_hints

BUBBLE_MAX_W = 320
BUBBLE_PAD_H = 14
BUBBLE_PAD_V = 10
BUBBLE_RADIUS = 12
BUBBLE_TAIL_H = 9
BUBBLE_GAP = 6           # 气泡与角色形象之间的间距
BUBBLE_HIDE_MS = 9000    # 默认自动消失时间


def frameless_tool_flags():
    """无边框、置顶、不进任务栏的浮层窗口标志。"""
    return (
        Qt.WindowType.FramelessWindowHint
        | Qt.WindowType.Tool
        | Qt.WindowType.WindowStaysOnTopHint
        | Qt.WindowType.NoDropShadowWindowHint
    )


def wrap_text(text: str, metrics, max_width: int) -> list[str]:
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


class Bubble(QWidget):
    """桌宠头顶的圆角气泡（自绘，带指向角色的小尾巴）。"""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowFlags(
            frameless_tool_flags() | Qt.WindowType.WindowDoesNotAcceptFocus
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
    def set_text(self, text: str, auto_hide: bool = True,
                 hide_ms: int = BUBBLE_HIDE_MS) -> None:
        self._text = (text or '').strip()
        self._relayout()
        self.update()
        self._hide_timer.stop()
        if auto_hide and self._text:
            self._hide_timer.start(max(500, int(hide_ms)))

    def text(self) -> str:
        return self._text

    def _relayout(self) -> None:
        metrics = self.fontMetrics()
        max_text_w = BUBBLE_MAX_W - BUBBLE_PAD_H * 2
        self._lines = wrap_text(self._text, metrics, max_text_w)
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
        """默认放在锚点矩形正上方并水平居中；顶部放不下则翻到下方。

        anchor_rect 应是角色可见形象的全局矩形，不是整个窗口。
        """
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


class SpeechScheduler(QObject):
    """随机自言自语：按 [min, max] 秒随机间隔说一句本地文本。"""

    def __init__(self, window, config, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.window = window
        self.cfg = config
        self.bubble = Bubble()
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._speak)

    # ---------------------------------------------------------- 配置
    def enabled(self) -> bool:
        return bool(self.cfg.get('speech_enabled', False))

    def lines(self) -> list[str]:
        raw = self.cfg.get('speech_lines') or []
        if isinstance(raw, str):
            raw = raw.splitlines()
        cleaned = [str(line).strip() for line in raw if str(line).strip()]
        return cleaned or list(catalog.SPEECH_LINES)

    def interval_range(self) -> tuple[float, float]:
        try:
            low = float(self.cfg.get('speech_min', catalog.SPEECH_MIN_SEC))
            high = float(self.cfg.get('speech_max', catalog.SPEECH_MAX_SEC))
        except (TypeError, ValueError):
            low, high = catalog.SPEECH_MIN_SEC, catalog.SPEECH_MAX_SEC
        low = max(5.0, low)
        high = max(low, high)
        return low, high

    def set_enabled(self, on: bool) -> None:
        self.cfg.set('speech_enabled', bool(on))
        self.cfg.save()
        if on:
            self.start()
        else:
            self.stop()

    # ---------------------------------------------------------- 生命周期
    def start(self) -> None:
        if not self.enabled():
            return
        self._schedule()

    def stop(self) -> None:
        self._timer.stop()
        self.bubble.hide()

    def _schedule(self) -> None:
        low, high = self.interval_range()
        self._timer.start(int(random.uniform(low, high) * 1000))

    def say_now(self) -> None:
        """立即说一句（菜单里的「说一句」入口，也用于预览）。"""
        self._show(random.choice(self.lines()))

    def _speak(self) -> None:
        if not self.enabled():
            return
        self.say_now()
        self._schedule()

    def _show(self, text: str) -> None:
        if self.window is None or not self.window.isVisible():
            return
        self.bubble.set_text(text)
        self.bubble.place_near(self.window.visible_global_rect())
        self.bubble.show()
        self.bubble.raise_()

    def reposition(self) -> None:
        if self.bubble.isVisible() and self.window is not None:
            self.bubble.place_near(self.window.visible_global_rect())

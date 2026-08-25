# -*- coding: utf-8 -*-
"""自言自语气泡设置对话框（两个版本都可用）。"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from . import catalog


class SpeechSettingsDialog(QDialog):
    """开关、随机间隔与自定义文本。"""

    def __init__(self, config, scheduler=None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle('自言自语设置')
        self.cfg = config
        self.scheduler = scheduler

        self._enabled = QCheckBox('开启随机自言自语气泡', self)
        self._enabled.setChecked(bool(config.get('speech_enabled', False)))

        self._min = QDoubleSpinBox(self)
        self._min.setRange(5.0, 3600.0)
        self._min.setDecimals(0)
        self._min.setSuffix(' 秒')
        self._min.setValue(float(config.get('speech_min', catalog.SPEECH_MIN_SEC)))

        self._max = QDoubleSpinBox(self)
        self._max.setRange(5.0, 7200.0)
        self._max.setDecimals(0)
        self._max.setSuffix(' 秒')
        self._max.setValue(float(config.get('speech_max', catalog.SPEECH_MAX_SEC)))

        lines = config.get('speech_lines') or []
        if isinstance(lines, str):
            lines = lines.splitlines()
        self._lines = QPlainTextEdit('\n'.join(str(line) for line in lines), self)
        self._lines.setPlaceholderText(
            '每行一条；留空使用内置文本：\n' + '\n'.join(catalog.SPEECH_LINES[:3]) + '\n…'
        )
        self._lines.setFixedHeight(140)

        preview = QPushButton('立即说一句', self)
        preview.clicked.connect(self._on_preview)

        form = QFormLayout()
        form.addRow(self._enabled)
        form.addRow('随机间隔最短', self._min)
        form.addRow('随机间隔最长', self._max)
        form.addRow('自言自语内容', self._lines)

        row = QHBoxLayout()
        row.addWidget(preview)
        row.addStretch(1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addLayout(row)
        layout.addWidget(buttons)
        self.setMinimumWidth(420)

    def _collect(self) -> None:
        low = float(self._min.value())
        high = max(low, float(self._max.value()))  # 上限不得低于下限
        lines = [line.strip() for line in self._lines.toPlainText().splitlines()]
        self.cfg.set('speech_enabled', self._enabled.isChecked())
        self.cfg.set('speech_min', low)
        self.cfg.set('speech_max', high)
        self.cfg.set('speech_lines', [line for line in lines if line])

    def _on_preview(self) -> None:
        self._collect()
        if self.scheduler is not None:
            self.scheduler.say_now()

    def accept(self) -> None:  # noqa: D102
        self._collect()
        self.cfg.save()
        super().accept()

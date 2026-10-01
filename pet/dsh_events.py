# -*- coding: utf-8 -*-
"""Linux 本地 DSH 事件适配层。

不依赖 DSH/Node/bridge 包。若环境变量 ``DSH_PET_EVENTS`` 指向 JSONL 文件，
后台监听新增事件并转换成统一状态：thinking / working / attention / error / idle。

支持事件示例：
    {"event":"turn/start","message":"开始处理"}
    {"event":"tool/call","tool":"bash","message":"执行命令"}
    {"event":"approval/asked","message":"需要确认"}
    {"event":"turn/end","status":"error","message":"失败"}
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path

from PySide6.QtCore import QObject, Signal

logger = logging.getLogger(__name__)


class DshEventWatcher(QObject):
    stateChanged = Signal(str, str)  # state, message

    def __init__(self, path: str | os.PathLike | None = None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.path = Path(path or os.environ.get('DSH_PET_EVENTS', '')).expanduser()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._offset = 0
        self._inode: tuple[int, int] | None = None

    @property
    def enabled(self) -> bool:
        return bool(str(self.path)) and self.path.is_file()

    def start(self) -> bool:
        if not self.enabled or self._thread is not None:
            return False
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name='dsh-events')
        self._thread.start()
        return True

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        self._thread = None
        if thread is not None:
            thread.join(timeout=0.5)

    def _run(self) -> None:
        while not self._stop.wait(0.25):
            try:
                stat = self.path.stat()
                identity = (stat.st_dev, stat.st_ino)
                if self._inode != identity or stat.st_size < self._offset:
                    self._offset = 0
                    self._inode = identity
                with self.path.open('r', encoding='utf-8', errors='replace') as stream:
                    stream.seek(self._offset)
                    for line in stream:
                        self._consume(line)
                    self._offset = stream.tell()
            except OSError:
                continue

    def _consume(self, line: str) -> None:
        try:
            data = json.loads(line)
        except (TypeError, ValueError):
            return
        if not isinstance(data, dict):
            return
        state, message = normalize_event(data)
        if state:
            self.stateChanged.emit(state, message)


def normalize_event(data: dict) -> tuple[str | None, str]:
    event = str(data.get('event') or data.get('type') or '').lower()
    message = str(data.get('message') or data.get('tool') or '').strip()
    if event in {'turn/start', 'step/start', 'thinking', 'agent/thinking'}:
        return 'thinking', message or '正在思考……'
    if event in {'tool/call', 'tool/start', 'working', 'agent/working'}:
        return 'working', message or '正在工作……'
    if event in {'approval/asked', 'ask_user_question', 'attention'}:
        return 'attention', message or '需要你的确认。'
    if event in {'turn/end', 'error', 'agent/error'}:
        status = str(data.get('status') or data.get('reason') or '').lower()
        if status in {'ok', 'completed', 'success', 'idle'} or event == 'turn/end' and status == 'done':
            return 'idle', message
        return 'error', message or '任务执行失败。'
    if event in {'idle', 'agent/idle', 'session/end'}:
        return 'idle', message
    return None, message

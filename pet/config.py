# -*- coding: utf-8 -*-
"""
配置持久化：`${XDG_CONFIG_HOME:-~/.config}/dsh-pet-standalone/config.json`。

记录：位置（相对屏幕可用区的中心比例，分辨率变化后仍正确）、朝向、缩放、
置顶开关、移动开关、锁定、播放速率、拖动物理、当前角色。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from . import catalog


def _default_base() -> Path:
    return Path(os.environ.get('XDG_CONFIG_HOME') or Path.home() / '.config')


class Config:
    def __init__(self, base: Path | str | None = None) -> None:
        base = Path(base) if isinstance(base, str) else (base or _default_base())
        self.dir = base / 'dsh-pet-standalone'
        self.path = self.dir / 'config.json'
        self.data: dict = {
            'version': 2,  # 配置结构版本；scale 语义变更时递增
            'rx': None,    # 窗口中心 x / 屏幕可用区宽（None=默认右下角）
            'ry': None,    # 窗口中心 y / 屏幕可用区高
            'facing': 'left',
            'scale': catalog.DEFAULT_SCALE,
            'on_top': True,
            'no_move': False,  # 不移动：勾选后状态机不再自动移动，仅手动点移动动画才走动
            'locked': False,  # 锁定：窗口完全鼠标穿透，只能从托盘解锁
            'speed': 1.0,  # 动画播放速率 1.0x ~ 2.0x
            'drag_physics': True,  # 拖动物理：松手抛出 + 重力 + 反弹衰减
            'character': catalog.DEFAULT_CHARACTER,  # 当前形象 ID
        }
        self._load()

    def _load(self) -> None:
        try:
            raw = json.loads(self.path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return
        if isinstance(raw, dict):
            if int(raw.get('version', 1)) < 2:
                # v1 → v2：素材从 220×124 换成 640×360，scale 语义变化，
                # 旧 scale（如 1.0 表示 220px）需重置为新的默认值。
                raw.pop('scale', None)
                raw['version'] = 2
            for key in self.data:
                if key in raw and raw[key] is not None:
                    self.data[key] = raw[key]

    def get(self, key: str, default=None):
        return self.data.get(key, default)

    def set(self, key: str, value) -> None:
        self.data[key] = value

    def save(self) -> None:
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                json.dumps(self.data, ensure_ascii=False, indent=2),
                encoding='utf-8',
            )
        except OSError:
            pass  # 配置写失败不致命

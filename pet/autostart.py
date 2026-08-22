# -*- coding: utf-8 -*-
"""
开机自启动管理（XDG autostart）。

写入 `${XDG_CONFIG_HOME:-~/.config}/autostart/<APP_ID>.desktop`。

设计原则：**系统自启配置是唯一真相**。菜单勾选状态直接查该文件是否存在，
不与 config.json 冗余存储，避免两处状态不同步。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_ID = "com.merzlin.dsh-pet-standalone"


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _desktop_path() -> Path:
    config_home = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return config_home / "autostart" / f"{APP_ID}.desktop"


def _desktop_quote(value: str) -> str:
    """Quote one argument according to the Desktop Entry Exec field rules."""
    escaped = value.replace('\\', '\\\\').replace('"', '\\"')
    escaped = escaped.replace('`', '\\`').replace('$', '\\$')
    return f'"{escaped}"'


def _desktop_entry() -> str:
    command = f"{_desktop_quote(sys.executable)} -m pet"
    return (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=dsh-pet\n"
        "Comment=Desktop pet for KDE Plasma\n"
        f"Exec={command}\n"
        f"Path={_project_root()}\n"
        "Terminal=false\n"
        "X-KDE-autostart-after=panel\n"
    )


def is_enabled() -> bool:
    """当前是否已注册开机自启。"""
    return _desktop_path().exists()


def enable() -> None:
    path = _desktop_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_desktop_entry(), encoding="utf-8")


def disable() -> None:
    _desktop_path().unlink(missing_ok=True)


def set_enabled(on: bool) -> None:
    enable() if on else disable()

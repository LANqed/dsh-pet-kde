"""KDE Plasma session compatibility helpers."""

from __future__ import annotations

import os


def configure_platform() -> None:
    """Use XWayland on Plasma Wayland so the pet can position and move itself."""
    desktop = os.environ.get("XDG_CURRENT_DESKTOP", "").lower()
    is_kde = "kde" in desktop or bool(os.environ.get("KDE_FULL_SESSION"))
    is_wayland = os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland"
    if is_kde and is_wayland:
        os.environ.setdefault("QT_QPA_PLATFORM", "xcb")

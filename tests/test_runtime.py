# -*- coding: utf-8 -*-
"""目录/常量完整性测试（无需 GUI）。"""

import os
from pathlib import Path
from unittest.mock import patch

from pet import catalog
from pet import autostart
from pet.kde import configure_platform


def test_catalog_integrity():
    assert len(catalog.ANIM_FILES) == 51
    assert len(catalog.ACTS) == 42
    assert len(catalog.CLICKS) == 3
    assert len(catalog.MOVES) == 3
    assert catalog.IDLE in catalog.ANIM_FILES
    assert catalog.TURN in catalog.ANIM_FILES
    assert catalog.DRAG in catalog.ANIM_FILES
    assert all(n in catalog.ANIM_FILES for n in catalog.CLICKS + catalog.MOVES)
    assert catalog.FRAME_MS > 0


def test_default_character_assets_exist():
    assert catalog.DEFAULT_CHARACTER in catalog.built_in_characters()
    assert catalog.resolve_character_video_dir(catalog.DEFAULT_CHARACTER).name == "videos"


def test_default_character_categories_match_original_asset_set():
    names = [path.stem for path in catalog.resolve_character_video_dir(
        catalog.DEFAULT_CHARACTER
    ).glob("*.webm")]
    categories = catalog.build_categories(names)
    assert len(names) == 51
    assert len(categories["idles"]) == 1
    assert len(categories["turns"]) == 1
    assert len(categories["moves"]) == 3
    assert len(categories["clicks"]) == 3
    assert categories["drag"] == catalog.DRAG
    assert len(categories["acts"]) == 42


def test_locked_config_persists(tmp_path: Path):
    from pet.config import Config

    config = Config(tmp_path)
    config.set("locked", True)
    config.save()
    assert Config(tmp_path).get("locked") is True


def test_kde_wayland_uses_xwayland():
    env = {
        "XDG_CURRENT_DESKTOP": "KDE",
        "XDG_SESSION_TYPE": "wayland",
    }
    with patch.dict(os.environ, env, clear=True):
        configure_platform()
        assert os.environ["QT_QPA_PLATFORM"] == "xcb"


def test_kde_platform_respects_explicit_qpa():
    env = {
        "KDE_FULL_SESSION": "true",
        "XDG_SESSION_TYPE": "wayland",
        "QT_QPA_PLATFORM": "wayland",
    }
    with patch.dict(os.environ, env, clear=True):
        configure_platform()
        assert os.environ["QT_QPA_PLATFORM"] == "wayland"


def test_linux_autostart_desktop_file(tmp_path: Path):
    with (
        patch.object(autostart, "_IS_WIN", False),
        patch.object(autostart, "_IS_MAC", False),
        patch.object(autostart, "_IS_LINUX", True),
        patch.dict(os.environ, {"XDG_CONFIG_HOME": str(tmp_path)}, clear=False),
    ):
        autostart.enable()
        desktop = tmp_path / "autostart" / f"{autostart.APP_ID}.desktop"
        text = desktop.read_text(encoding="utf-8")
        assert "Type=Application" in text
        assert "Exec=" in text
        assert "X-KDE-autostart-after=panel" in text
        assert autostart.is_enabled()
        autostart.disable()
        assert not desktop.exists()

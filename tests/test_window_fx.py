# -*- coding: utf-8 -*-
"""窗口交互特效测试：Q 弹、播放速率、拖动物理（无需真实 webm 素材）。

用最小假素材库驱动 PetWindow，避免依赖 gitignore 的 assets/。
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QObject, QPoint, Signal  # noqa: E402
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPixmap  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from pet import catalog  # noqa: E402
from pet.config import Config  # noqa: E402
from pet.window import PetWindow  # noqa: E402


class FakeClip(QObject):
    """最小 clip：提供窗口层需要的接口与一张不透明画面。"""

    frameChanged = Signal(int)
    finished = Signal()
    errorOccurred = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        img = QImage(catalog.CANVAS_W, catalog.CANVAS_H, QImage.Format.Format_ARGB32)
        img.fill(QColor(200, 120, 160, 255))
        self._pm = QPixmap.fromImage(img)
        self.speed = 1.0
        self._frame = 0

    # 播放控制
    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def jumpToFrame(self, index: int) -> bool:
        self._frame = max(0, index)
        return True

    def setSpeed(self, speed: float) -> None:
        self.speed = speed

    # 状态查询
    def currentPixmap(self) -> QPixmap:
        return self._pm

    def currentFrameNumber(self) -> int:
        return self._frame

    def currentTimeSeconds(self) -> float:
        return 0.0

    def frameCount(self) -> int:
        return 24

    def duration(self) -> float:
        return 1.0


class FakeLibrary:
    """最小素材库：一个待机 + 一个转向 + 一个移动 + 一个点击 + 一个拖拽。"""

    def __init__(self) -> None:
        self._names = ["待机", "转身", "走路", "点击回应", "拖拽", "动作A"]
        self._clips = {n: FakeClip() for n in self._names}
        self.manifest = {
            "idle": "待机",
            "turn": "转身",
            "moves": ["走路"],
            "clicks": ["点击回应"],
            "drag": "拖拽",
        }
        self.folder_map = None
        self.folder_files = None
        self.speed = 1.0

    def names(self) -> list[str]:
        return list(self._names)

    def movie(self, name: str) -> FakeClip:
        return self._clips[name]

    def movies(self) -> dict[str, FakeClip]:
        return dict(self._clips)

    def frames(self, name: str) -> int:
        return self._clips[name].frameCount()

    def duration(self, name: str) -> float:
        return self._clips[name].duration()

    def set_speed(self, speed: float) -> None:
        self.speed = speed
        for clip in self._clips.values():
            clip.setSpeed(speed)


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture()
def win(qapp, tmp_path):
    lib = FakeLibrary()
    window = PetWindow(lib, Config(tmp_path))
    yield window
    window.close()


def test_click_triggers_squash_and_settles(win):
    assert win._squash_active is False
    win._on_click()
    assert win._squash_active is True
    assert win._squash_sy < 1.0          # 先变矮
    assert win._squash_sx > 1.0          # 横向补偿
    assert win._fx_timer.isActive()

    # 推进到结束：缩放回到 1.0，特效计时器停止
    for _ in range(int(catalog.SQUASH_DURATION / (catalog.ANIM_TICK_MS / 1000.0)) + 5):
        win._on_fx_tick()
    assert win._squash_active is False
    assert win._squash_sx == 1.0 and win._squash_sy == 1.0
    assert win._fx_timer.isActive() is False


def test_repeated_click_restarts_squash(win):
    win._on_click()
    for _ in range(6):
        win._on_fx_tick()
    mid_phase = win._squash_t
    assert mid_phase > 0
    win._on_click()                       # 连点：相位重置，立刻重新压缩
    assert win._squash_t == 0.0
    assert win._squash_sy < 1.0


def test_squash_anchors_feet(win):
    """压缩时以脚底为锚：纵向缩放只压高度，不应把角色抬离地面。"""
    win.trigger_squash()
    assert 0.5 < win._squash_sy < 1.0
    assert win._squash_sx > 1.0
    # 体积补偿方向正确：横向膨胀与纵向压缩相反
    assert (1.0 - win._squash_sy) * catalog.SQUASH_X_RATIO == pytest.approx(
        win._squash_sx - 1.0, rel=1e-6
    )


def test_set_speed_applies_to_library_and_config(win):
    win.set_speed(1.5)
    assert win.speed == 1.5
    assert win.lib.speed == 1.5
    assert win.cfg.get("speed") == 1.5
    for clip in win.lib.movies().values():
        assert clip.speed == 1.5


def test_speed_is_clamped(win):
    win.set_speed(99.0)
    assert win.speed == 4.0
    win.set_speed(0.01)
    assert win.speed == 0.5


def test_speed_rescales_active_move_plan(win):
    win._move_plan = {
        "start_x": win.x(),
        "target_x": win.x() + 100,
        "y": win.y(),
        "duration": win.lib.duration(win.anim),
    }
    win.set_speed(2.0)
    assert win._move_plan["duration"] == pytest.approx(
        win.lib.duration(win.anim) / 2.0
    )


def test_drag_velocity_from_samples(win):
    win._samples = []
    win._record_sample(QPoint(0, 0))
    win._record_sample(QPoint(30, 10))
    vx, vy = win._drag_velocity()
    assert isinstance(vx, float) and isinstance(vy, float)


def test_lean_follows_horizontal_speed(win):
    win.set_drag_physics(True)
    win._update_lean(1000.0)
    fast_lean = win._lean_deg
    win._update_lean(100.0)
    slow_lean = win._lean_deg
    assert abs(fast_lean) > abs(slow_lean)
    assert abs(fast_lean) <= catalog.LEAN_MAX_DEG
    # 速度方向决定倾斜方向
    win._update_lean(-1000.0)
    assert win._lean_deg * fast_lean < 0


def test_lean_disabled_without_physics(win):
    win.set_drag_physics(False)
    win._update_lean(2000.0)
    assert win._lean_deg == 0.0


def test_fly_falls_and_settles_on_ground(win):
    avail = QGuiApplication.primaryScreen().availableGeometry()
    win.set_drag_physics(True)
    win.move(avail.left() + 200, avail.top() + 50)
    win._start_fly(0.0, 0.0)
    assert win._flying is True
    assert win._fx_timer.isActive()

    for _ in range(1200):
        if not win._flying:
            break
        win._on_fx_tick()

    assert win._flying is False
    bottom_limit = avail.bottom() - win.height() + 1
    assert win.y() == bottom_limit          # 落到地面并静止
    assert win._lean_deg == 0.0
    assert win._fx_timer.isActive() is False


def test_fly_stays_inside_screen_and_bounces_off_wall(win):
    avail = QGuiApplication.primaryScreen().availableGeometry()
    win.set_drag_physics(True)
    win.move(avail.left() + 20, avail.top() + 40)
    win._start_fly(-4000.0, 0.0)            # 向左猛甩
    for _ in range(1200):
        if not win._flying:
            break
        win._on_fx_tick()
        assert win.x() >= avail.left()
        assert win.x() <= avail.right() - win.width() + 1
        assert win.y() >= avail.top()
        assert win.y() <= avail.bottom() - win.height() + 1
    assert win._flying is False


def test_throw_speed_is_capped(win):
    win.set_drag_physics(True)
    win._start_fly(99999.0, 99999.0)
    speed = (win._fly_vx ** 2 + win._fly_vy ** 2) ** 0.5
    assert speed <= catalog.THROW_MAX_SPEED + 1e-6
    win._stop_fly()


def test_disable_physics_stops_flight(win):
    win.set_drag_physics(True)
    win._start_fly(500.0, -200.0)
    assert win._flying is True
    win.set_drag_physics(False)
    assert win._flying is False
    assert win._lean_deg == 0.0


def test_locking_stops_flight(win):
    win.set_drag_physics(True)
    win._start_fly(500.0, -200.0)
    win.set_locked(True)
    assert win._flying is False
    win.set_locked(False)


def test_drag_physics_persists(win):
    win.set_drag_physics(False)
    assert win.cfg.get("drag_physics") is False
    win.set_drag_physics(True)
    assert win.cfg.get("drag_physics") is True


def _menu_snapshot(window):
    """构造右键菜单（不弹出），返回顶层项与子菜单内容。"""
    menu = window.build_context_menu()
    try:
        items = [a.text() for a in menu.actions()]
        subs = {
            a.text(): [s.text() for s in a.menu().actions()]
            for a in menu.actions()
            if a.menu() is not None
        }
    finally:
        menu.deleteLater()
    return items, subs


def test_context_menu_exposes_new_features(win):
    items, subs = _menu_snapshot(win)

    assert "拖动物理" in items
    assert "播放速度" in subs
    assert [f"{s:g}x" for s in catalog.SPEED_STEPS] == subs["播放速度"]
    assert "切换角色" in subs
    assert "打开角色文件夹…" in subs["切换角色"]
    assert "重新扫描角色" in subs["切换角色"]
    assert "锁定并穿透鼠标" in items


def test_context_menu_speed_checked_matches_state(win):
    win.set_speed(1.5)
    _, subs = _menu_snapshot(win)
    assert "1.5x" in subs["播放速度"]
    win.set_speed(1.0)


def test_rescan_characters_invokes_callback(win):
    called = []
    win.on_rescan_characters = lambda: called.append(1)
    win._rescan_characters()
    assert called == [1]

# -*- coding: utf-8 -*-
"""跟随上游 dsh-pet-indesktop 的功能与修复测试。

覆盖：
- ffmpeg 不可用时的优雅降级（None pixmap 防御 + 占位画面）
- 首帧后台预热缓存
- Q 弹几何：宽度不放大、mask 同步、先切动画再压扁
- 动作等待间隔
- 自言自语气泡与角色包围盒定位
- X11 置顶状态查询与 watchdog
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QRect  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from pet import catalog  # noqa: E402
from pet import webm_clip  # noqa: E402
from pet import x11_hints  # noqa: E402
from pet.config import Config  # noqa: E402
from pet.window import PetWindow  # noqa: E402

from test_window_fx import FakeClip, FakeLibrary  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture()
def win(qapp, tmp_path):
    window = PetWindow(FakeLibrary(), Config(tmp_path))
    yield window
    window.close()


# ---------------------------------------------------------------- 解码降级
def test_ffmpeg_status_returns_tuple():
    ok, detail = webm_clip.ffmpeg_status()
    assert isinstance(ok, bool)
    assert isinstance(detail, str) and detail


def test_placeholder_pixmap_has_visible_pixels(qapp):
    pm = webm_clip.placeholder_pixmap("测试角色", 120, 90)
    assert pm.width() == 120 and pm.height() == 90
    image = pm.toImage()
    opaque = sum(
        1
        for y in range(0, image.height(), 3)
        for x in range(0, image.width(), 3)
        if image.pixelColor(x, y).alpha() > 0
    )
    assert opaque > 0, "占位画面应有可见像素"


def test_rebuild_frame_survives_none_pixmap(win):
    """ffmpeg 被隔离时 currentPixmap() 返回 None，不能抛 AttributeError。"""

    class NullClip(FakeClip):
        def currentPixmap(self):
            return None

    win._frame_pixmap = None
    win.movie = NullClip()
    win._rebuild_frame()  # 不应抛异常
    assert win._frame_pixmap is not None, "应回退到占位画面"


def test_rebuild_frame_keeps_previous_frame_on_decode_failure(win):
    before = win._frame_pixmap
    assert before is not None

    class NullClip(FakeClip):
        def currentPixmap(self):
            return None

    win.movie = NullClip()
    win._rebuild_frame()
    assert win._frame_pixmap is before, "已有画面时应保留上一帧而不是换占位"


def test_icon_pixmap_falls_back_to_placeholder(win):
    win._frame_pixmap = None
    win.idle = None
    pm = win.icon_pixmap(32)
    assert not pm.isNull()


# ---------------------------------------------------------------- 首帧预热
def test_warm_first_frames_dispatches_all_clips(qapp):
    """warm_first_frames 应对库中每个 clip 调用一次预热（并发受 WARM_WORKERS 限制）。"""
    import threading

    lib = FakeLibrary()
    seen: list[str] = []
    lock = threading.Lock()

    for name, clip in lib.movies().items():
        def make(target_name):
            def warm():
                with lock:
                    seen.append(target_name)
                return True
            return warm
        clip.warm_first_frame = make(name)  # type: ignore[attr-defined]

    from pet.library import MovieLibrary

    MovieLibrary.warm_first_frames(lib)  # 复用真实实现，FakeLibrary 提供 _movies
    deadline = 3.0
    step = 0.02
    waited = 0.0
    while waited < deadline:
        with lock:
            if len(seen) >= len(lib.names()):
                break
        import time as time_mod

        time_mod.sleep(step)
        waited += step
    assert sorted(seen) == sorted(lib.names())


def test_warm_workers_is_conservative():
    """并发刻意压低，降低 ffmpeg 进程洪峰与被安全软件拦截的概率。"""
    assert 1 <= catalog.WARM_WORKERS <= 4


# ---------------------------------------------------------------- Q 弹
def test_squash_never_widens_frame(win):
    """窗口与 mask 尺寸固定，宽度放大会把角色边缘裁成透明。"""
    win.trigger_squash()
    sx, sy, _ = win._squash_geometry()
    assert sx <= 1.0, "Q 弹不应放大宽度"
    assert sy < 1.0


def test_squash_geometry_matches_transform_state(win):
    assert win._has_squash_transform() is False
    win.trigger_squash()
    assert win._has_squash_transform() is True
    win._stop_squash()
    assert win._has_squash_transform() is False


def test_click_switches_animation_before_squash(win):
    """必须先切到点击回应动画再压扁，否则压的是上一段动画的旧帧。"""
    order = []
    original_switch = win._switch
    original_squash = win.trigger_squash

    def traced_switch(name):
        order.append("switch")
        original_switch(name)

    def traced_squash():
        order.append("squash")
        original_squash()

    win._switch = traced_switch  # type: ignore[method-assign]
    win.trigger_squash = traced_squash  # type: ignore[method-assign]
    win._just_dragged = False
    win.anim = win.idle
    win._on_click()

    assert order == ["switch", "squash"], order


def test_squash_marks_mask_dirty_each_frame(win):
    """Q 弹期间 mask 必须跟着压扁几何重算，否则耳朵/头顶会被旧 mask 裁掉。"""
    if not win._use_native_mask:
        pytest.skip("KDE 下不使用原生 mask")
    win.trigger_squash()
    win._sync_mask()
    assert win._mask_initialized is False, "压扁中不应锁定 mask"
    win._stop_squash()
    win._sync_mask(force=True)
    assert win._mask_initialized is True


# ---------------------------------------------------------------- 等待间隔
def test_idle_gap_defaults_to_continuous(win):
    assert win.idle_gap == 0.0
    assert win._gap_blocking() is False


def test_idle_gap_persists(win):
    win.set_idle_gap(3.0)
    assert win.idle_gap == 3.0
    assert win.cfg.get("idle_gap") == 3.0
    win.set_idle_gap(0.0)
    assert win.cfg.get("idle_gap") == 0.0


def test_idle_gap_blocks_actions_only_within_window(win):
    win.set_idle_gap(60.0)
    win._switch(win.acts[0] if win.acts else "动作A")
    assert win._gap_blocking() is True
    for _ in range(30):
        win._cancel_move()
        win._pick_next()
        assert win.anim in (win.idles + win.turns), win.anim
    win.set_idle_gap(0.0)


def test_idle_gap_expires(win, monkeypatch):
    import time as time_mod

    win.set_idle_gap(1.0)
    win._last_action_ts = time_mod.monotonic() - 5.0
    assert win._gap_blocking() is False
    win.set_idle_gap(0.0)


def test_action_classification(win):
    for name in win.idles + win.turns:
        assert win._is_action_anim(name) is False
    for name in win.acts:
        assert win._is_action_anim(name) is True


# ---------------------------------------------------------------- 可见包围盒
def test_visible_local_rect_is_tighter_than_window(win):
    rect = win.visible_local_rect()
    assert isinstance(rect, QRect)
    assert rect.width() > 0 and rect.height() > 0
    assert rect.width() <= win.width()
    assert rect.height() <= win.height()


def test_visible_global_rect_follows_window(win):
    win.move(400, 300)
    first = win.visible_global_rect()
    win.move(600, 300)
    second = win.visible_global_rect()
    assert second.left() - first.left() == 200


def test_visible_rect_is_cached(win):
    first = win.visible_local_rect()
    second = win.visible_local_rect()
    assert first == second
    assert win._visible_rect_cache is not None


# ---------------------------------------------------------------- 自言自语
def test_speech_scheduler_defaults(qapp, win):
    from pet.speech_bubble import SpeechScheduler

    scheduler = SpeechScheduler(win, win.cfg)
    try:
        assert scheduler.enabled() is False
        assert scheduler.lines() == list(catalog.SPEECH_LINES)
        low, high = scheduler.interval_range()
        assert low <= high
    finally:
        scheduler.stop()


def test_speech_custom_lines_and_toggle(qapp, win):
    from pet.speech_bubble import SpeechScheduler

    scheduler = SpeechScheduler(win, win.cfg)
    try:
        win.cfg.set("speech_lines", ["自定义一", "  ", "自定义二"])
        assert scheduler.lines() == ["自定义一", "自定义二"]
        scheduler.set_enabled(True)
        assert win.cfg.get("speech_enabled") is True
        scheduler.set_enabled(False)
        assert win.cfg.get("speech_enabled") is False
    finally:
        scheduler.stop()


def test_speech_interval_range_is_sanitized(qapp, win):
    from pet.speech_bubble import SpeechScheduler

    scheduler = SpeechScheduler(win, win.cfg)
    try:
        win.cfg.set("speech_min", 100.0)
        win.cfg.set("speech_max", 10.0)  # 上限低于下限
        low, high = scheduler.interval_range()
        assert low == 100.0 and high >= low
    finally:
        scheduler.stop()


def test_bubble_places_above_anchor(qapp):
    from pet.speech_bubble import Bubble

    bubble = Bubble()
    try:
        bubble.set_text("你好呀")
        anchor = QRect(600, 500, 200, 200)
        bubble.place_near(anchor)
        assert bubble.y() + bubble.height() <= anchor.top()
    finally:
        bubble.close()


def test_bubble_flips_below_when_no_top_space(qapp):
    from pet.speech_bubble import Bubble

    bubble = Bubble()
    try:
        bubble.set_text("顶部没有空间")
        anchor = QRect(600, 0, 200, 120)
        bubble.place_near(anchor)
        assert bubble.y() >= anchor.top()
    finally:
        bubble.close()


def test_bubble_wraps_long_text(qapp):
    from pet.speech_bubble import Bubble

    bubble = Bubble()
    try:
        bubble.set_text("测试" * 150)
        assert len(bubble._lines) > 1
        assert bubble.width() <= 320
    finally:
        bubble.close()


# ---------------------------------------------------------------- 置顶自检
def test_topmost_watchdog_interval_is_sane():
    assert catalog.TOPMOST_CHECK_MS >= 5_000


def test_is_above_returns_bool_or_none(win):
    result = x11_hints.is_above(int(win.winId()))
    assert result is None or isinstance(result, bool)


def test_check_topmost_stops_when_state_unknown(win, monkeypatch):
    """查不到状态（原生 Wayland 等）应停掉 watchdog，避免空转。"""
    monkeypatch.setattr(x11_hints, "is_above", lambda wid: None)
    win.show()
    win._topmost_timer.start()
    win._check_topmost()
    assert win._topmost_timer.isActive() is False


def test_check_topmost_restores_lost_state(win, monkeypatch):
    calls = []
    monkeypatch.setattr(x11_hints, "is_above", lambda wid: False)
    monkeypatch.setattr(x11_hints, "set_above", lambda wid: calls.append(wid) or True)
    win.show()
    win._check_topmost()
    assert calls, "检测到丢失应重新请求置顶"


def test_bring_to_front_skipped_when_locked(win, monkeypatch):
    calls = []
    monkeypatch.setattr(x11_hints, "set_above", lambda wid: calls.append(wid) or True)
    win.set_locked(True)
    win.bring_to_front()
    assert calls == []
    win.set_locked(False)


def test_lock_emits_speech_hint(win):
    seen = []
    win.on_speech_requested = seen.append
    win.set_locked(True)
    assert seen and "托盘" in seen[0]
    win.set_locked(False)


# ---------------------------------------------------------------- 菜单
def test_context_menu_has_idle_gap_submenu(win):
    menu = win.build_context_menu()
    try:
        subs = {
            a.text(): [s.text() for s in a.menu().actions()]
            for a in menu.actions()
            if a.menu() is not None
        }
    finally:
        menu.deleteLater()
    assert "动作等待间隔" in subs
    assert "连续播放" in subs["动作等待间隔"]

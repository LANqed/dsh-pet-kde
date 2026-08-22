# -*- coding: utf-8 -*-
"""
Smoke test for the webm-backed media layer + window behavior（真实 webm 素材）。

Run: python tests/smoke.py
"""

from __future__ import annotations

import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from pet import catalog  # noqa: E402
from pet.config import Config  # noqa: E402
from pet.library import MovieLibrary  # noqa: E402
from pet.window import PetWindow  # noqa: E402


def _wait_until(app: QApplication, predicate, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("timeout waiting for condition")


def main() -> int:
    app = QApplication([])
    video_dir = catalog.resolve_character_video_dir(catalog.DEFAULT_CHARACTER)
    if not video_dir.is_dir() or not any(video_dir.rglob("*.webm")):
        # assets/ 不随仓库分发；缺素材时给出明确提示而不是抛栈
        print(f"跳过：默认角色素材缺失 -> {video_dir}")
        print("请放入 webm 后重试，或运行 python -m pytest -q（无需素材）。")
        return 0
    lib = MovieLibrary()  # 真实 webm：assets/videos

    # 1. 51 段素材全量可加载，帧数/时长有效
    names = lib.names()
    assert len(names) == 51, len(names)
    for name in names:
        assert lib.frames(name) >= 1, (name, lib.frames(name))
        assert lib.duration(name) > 0, (name, lib.duration(name))

    # 2. 透明通道：待机首帧同时含透明与不透明像素
    idle = lib.movie(catalog.IDLE)
    idle.jumpToFrame(0)
    img = idle.currentPixmap().toImage()
    alphas = set()
    for x in range(0, img.width(), 20):
        for y in range(0, img.height(), 10):
            alphas.add(img.pixelColor(x, y).alpha())
    assert len(alphas) >= 2, sorted(alphas)

    # 3. 播放推进
    idle.start()
    _wait_until(app, lambda: idle.currentFrameNumber() >= 1)
    assert idle.currentFrameNumber() >= 1
    idle.stop()

    # 4. 窗口实例化：尺寸/初始动画/透明窗口
    cfg = Config(base=os.path.join(os.path.dirname(os.path.abspath(__file__)), "_tmp_cfg"))
    win = PetWindow(lib, cfg)
    win.show()
    assert win.anim == catalog.IDLE
    if win._use_native_mask:
        assert win.mask() is not None and not win.mask().isNull()
    else:
        # KWin/XWayland gets an unshaped translucent surface to avoid black
        # rectangles and stale first-frame outlines in its shape compositor.
        assert win.mask().isNull()
    assert win.width() == int(round(catalog.CONTENT_W * win.scale))
    assert win.height() == int(round(catalog.CONTENT_H * win.scale))

    # 4b. 帧已裁切到内容区：四周留出边距，窗口明显小于原始 16:9 画布
    pm = win._frame_pixmap
    assert pm is not None
    assert pm.width() == int(round(catalog.CONTENT_W * win.scale))
    assert pm.height() == int(round(catalog.CONTENT_H * win.scale))
    img = pm.toImage()
    bottom_most = -1
    for y in range(img.height() - 1, -1, -1):
        if any(img.pixelColor(x, y).alpha() >= 8 for x in range(0, img.width(), 2)):
            bottom_most = y
            break
    assert bottom_most >= 0
    # 脚底下方留地面边距，不应过大（约 20 画布像素，缩放后按比例放宽到 30）
    assert img.height() - 1 - bottom_most <= 30, (img.height(), bottom_most, "底部边距过大")
    # 相比原始 640×360 画布，裁切后窗口明显更小（不再悬在 16:9 大矩形里）
    assert win.width() < catalog.CANVAS_W * win.scale

    # 5. 缩放：底边不动
    bottom = win.geometry().bottom()
    win.change_scale(1.25)
    assert win.geometry().bottom() == bottom
    assert win.width() == int(round(catalog.CONTENT_W * 1.25))
    win.change_scale(1.0)

    # 6. 点击回应：仅待机时可点；播完回待机缓冲
    win._on_click()
    assert win.anim in catalog.CLICKS, win.anim
    win._on_anim_ended(win.anim)
    assert win.anim == catalog.IDLE

    # 7. 转向：东张西望播完翻转朝向
    facing_before = win.facing
    win._switch(catalog.TURN)
    win._on_anim_ended(catalog.TURN)
    assert win.facing != facing_before

    # 8. 移动：空间足够则生成移动计划并推进插值
    win._cancel_move()
    ok = win._try_move()
    assert isinstance(ok, bool)
    if ok:
        assert win._move_plan is not None
        x0 = win.x()
        win._on_move_tick()
        assert win.x() in (x0, win._move_plan["target_x"])  # 前后 2s 内位置不动或已到位
        win._cancel_move()

    # 9. 「不移动」：状态机不再进入移动动画；手动移动仍可走动；开关持久化
    win.set_no_move(True)
    assert win.no_move is True and cfg.get("no_move") is True
    for _ in range(200):
        win._cancel_move()
        win._pick_next()
        assert win.anim not in catalog.MOVES, win.anim
    win._cancel_move()
    win._trigger_move(catalog.MOVES[0])
    assert win.anim in catalog.MOVES, win.anim
    win._cancel_move()
    win.set_no_move(False)
    assert win.no_move is False and cfg.get("no_move") is False

    # 10. 点击 Q 弹：立即压缩，推进后回到原状
    win._just_dragged = False
    win._on_click()
    assert win._squash_active is True
    assert win._squash_sy < 1.0 and win._squash_sx > 1.0
    for _ in range(int(catalog.SQUASH_DURATION / (catalog.ANIM_TICK_MS / 1000.0)) + 5):
        win._on_fx_tick()
    assert win._squash_active is False
    assert win._squash_sx == 1.0 and win._squash_sy == 1.0

    # 11. 播放速率：库内所有 clip 同步生效并持久化
    win.set_speed(2.0)
    assert cfg.get("speed") == 2.0
    for clip in lib.movies().values():
        assert abs(clip.speed() - 2.0) < 1e-6
    win.set_speed(1.0)

    # 12. 拖动物理：抛出后受重力落地并停在屏幕内
    from PySide6.QtGui import QGuiApplication

    avail = QGuiApplication.primaryScreen().availableGeometry()
    win.set_drag_physics(True)
    win.move(avail.left() + 200, avail.top() + 40)
    win._start_fly(600.0, -200.0)
    for _ in range(2000):
        if not win._flying:
            break
        win._on_fx_tick()
        assert avail.left() <= win.x() <= avail.right() - win.width() + 1
        assert avail.top() <= win.y() <= avail.bottom() - win.height() + 1
    assert win._flying is False
    assert win.y() == avail.bottom() - win.height() + 1

    win.close()
    print("\n=== ALL SMOKE TESTS PASSED ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

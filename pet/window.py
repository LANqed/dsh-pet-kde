# -*- coding: utf-8 -*-
"""
桌宠主窗口 —— 透明无边框置顶窗口 + 动画链状态机 + 移动驱动 + 交互。

状态机（对应原插件 dsh-pet lib/client.js 的链式模型，行为 1:1 移植）：
  - 每个动画一次性播放，播完按概率选下一个：30% 待机 / 10% 转向 / 40% 动作 / 20% 移动；
  - 转向（东张西望）播完翻转朝向；facing=right 时水平镜像；
  - 点击回应 / 拖拽动画播完先回待机缓冲，待机播完再进随机链；
  - 移动：动画只提供"走路姿态"（3 选 1），位置由 QTimer 驱动，
    开头/结尾各 2s 不动，中间按播放进度插值；
  - 点击 Q 弹与拖动物理（抛出/重力/反弹/倾斜）由 60fps 特效定时器驱动。

透明穿透：KDE/XWayland 下不使用 QWidget.setMask()（KWin 6 的 shape 合成会
产生黑框与残影），改用无 shape 的完整透明 surface；其他 WM 仍逐帧生成 mask。
"""

from __future__ import annotations

import logging
import math
import os
import random
import time

from PySide6.QtCore import QPoint, Qt, QTimer, QUrl
from PySide6.QtGui import QBitmap, QDesktopServices, QImage, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QMenu, QWidget

from . import autostart as autostart_mod
from . import catalog
from . import x11_hints
from .config import Config
from .library import MovieLibrary


class PetWindow(QWidget):
    """桌宠窗口本体。"""

    def __init__(self, lib: MovieLibrary, config: Config) -> None:
        super().__init__()
        self.lib = lib
        self.cfg = config
        self.on_switch_character = None  # 由 app 注入，用于运行时切换角色
        self.on_rescan_characters = None  # 由 app 注入，重新扫描角色目录
        self.on_locked_changed = None  # 由 app 注入，同步托盘勾选状态
        self.on_chat_requested = None  # 由 app 注入（仅 Chat 版），打开对话输入框
        desktop = os.environ.get('XDG_CURRENT_DESKTOP', '').lower()
        self._use_native_mask = not (
            'kde' in desktop or os.environ.get('KDE_FULL_SESSION')
        )

        # 根据当前形象实际拥有的动画动态计算分类，支持不同角色动作不一致
        self.cats = catalog.build_categories(lib.names(), getattr(lib, 'manifest', None), getattr(lib, 'folder_map', None), getattr(lib, 'folder_files', None))
        self.idle = self.cats['idle']
        self.turn = self.cats['turn']
        self.idles = self.cats['idles']
        self.turns = self.cats['turns']
        self.moves = self.cats['moves']
        self.clicks = self.cats['clicks']
        self.drag = self.cats['drag']
        self.acts = self.cats['acts']
        self.locked = bool(config.get('locked', False))

        # 预载拖拽动画首帧，避免第一次进入拖拽状态时同步解码卡顿
        if self.drag:
            self.lib.movie(self.drag).jumpToFrame(0)

        # ---- 窗口属性：无边框 + 透明 + 不进任务栏；置顶可配置 ----
        # WindowDoesNotAcceptFocus 在 X11/XWayland 下同时设置
        # _NET_WM_STATE_SKIP_TASKBAR 与 _NET_WM_STATE_SKIP_PAGER，
        # 让 KDE 任务管理器/底部 dock 不显示桌宠图标（Tool 类型默认仍可能被显示）。
        flags = (
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowDoesNotAcceptFocus
            | Qt.WindowType.NoDropShadowWindowHint
        )
        if config.get('on_top', True):
            flags |= Qt.WindowType.WindowStaysOnTopHint
        if self.locked:
            flags |= Qt.WindowType.WindowTransparentForInput
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.setAutoFillBackground(False)

        # ---- 状态 ----
        self.anim: str = self.idle
        self.facing: str = config.get('facing', 'left')  # left | right
        self.scale: float = float(config.get('scale', catalog.DEFAULT_SCALE))
        self.no_move: bool = bool(config.get('no_move', False))  # 不移动：禁用自动移动
        self.speed: float = float(config.get('speed', 1.0))      # 播放速率 1.0x~2.0x
        self.drag_physics: bool = bool(config.get('drag_physics', True))
        self.movie = None
        self._frame_pixmap: QPixmap | None = None
        self._mask_initialized = False
        self._ended_fired = False
        lib.set_speed(self.speed)

        # ---- 交互状态 ----
        self._press_global: QPoint | None = None
        self._grab_offset: QPoint | None = None  # 按下时 鼠标全局坐标 - 窗口左上角
        self._dragging = False
        self._just_dragged = False               # 抑制拖拽结束后的幽灵点击

        # ---- Q 弹（点击挤压回弹）----
        self._squash_t = 0.0
        self._squash_active = False
        self._squash_sx = 1.0
        self._squash_sy = 1.0

        # ---- 拖动物理（抛出/重力/反弹 + 倾斜）----
        self._samples: list[tuple[float, QPoint]] = []  # (时间, 全局鼠标点)
        self._fly_vx = 0.0
        self._fly_vy = 0.0
        self._fly_x = 0.0
        self._fly_y = 0.0
        self._flying = False
        self._lean_deg = 0.0

        # 60fps 特效驱动：Q 弹 + 抛飞共用一个计时器
        self._fx_timer = QTimer(self)
        self._fx_timer.setInterval(catalog.ANIM_TICK_MS)
        self._fx_timer.timeout.connect(self._on_fx_tick)

        # ---- 移动驱动 ----
        self._move_plan: dict | None = None
        self._move_timer = QTimer(self)
        self._move_timer.setInterval(33)         # ~30fps 位置插值
        self._move_timer.timeout.connect(self._on_move_tick)

        # ---- 尺寸与初始状态 ----
        self._apply_scale()
        for name, movie in lib.movies().items():
            # 默认参数捕获 name，避免闭包晚绑定
            movie.frameChanged.connect(lambda n, name=name: self._on_frame(name, n))
        self._restore_position()
        self._switch(self.idle)

    # ================================================================ 尺寸
    def _apply_scale(self) -> None:
        """按缩放计算窗口尺寸：内容裁切区 325×273，脚底贴窗口底线。"""
        self._w = max(1, int(round(catalog.CONTENT_W * self.scale)))
        self._h = max(1, int(round(catalog.CONTENT_H * self.scale)))
        self.setFixedSize(self._w, self._h)

    def change_scale(self, scale: float) -> None:
        """切换缩放；保持窗口底边不动（脚踩的地面不变）。"""
        if abs(scale - self.scale) < 1e-6:
            return
        old_bottom = self.geometry().bottom()
        self.scale = scale
        self._apply_scale()
        self.move(self.x(), old_bottom - self._h + 1)
        self._rebuild_frame()
        self.update()
        self._save_position()

    # ================================================================ 位置
    def _screen_available(self):
        """窗口所在屏幕；未映射时兜底主屏。"""
        from PySide6.QtGui import QGuiApplication
        scr = self.screen()
        if scr is None:
            scr = QGuiApplication.primaryScreen()
        return scr

    def _restore_position(self) -> None:
        """恢复上次位置（按屏幕比例），无记录则落右下角。"""
        scr = self._screen_available()
        avail = scr.availableGeometry()
        rx, ry = self.cfg.get('rx'), self.cfg.get('ry')
        if rx is None or ry is None:
            x = avail.right() - self._w - catalog.CORNER_MARGIN
            y = avail.bottom() - self._h
        else:
            x = int(round(avail.left() + rx * avail.width())) - self._w // 2
            y = int(round(avail.top() + ry * avail.height())) - self._h // 2
            x = min(max(x, avail.left()), avail.right() - self._w)
            y = min(max(y, avail.top()), avail.bottom() - self._h)
        logging.info('恢复位置 screen=%s avail=(%d,%d,%d,%d) dpr=%s -> (%d,%d)',
                     scr.name(), avail.left(), avail.top(), avail.right(),
                     avail.bottom(), scr.devicePixelRatio(), x, y)
        self.move(x, y)

    def _save_position(self) -> None:
        """以"窗口中心相对屏幕可用区的比例"持久化位置（分辨率变化后仍正确）。"""
        scr = self._screen_available()
        avail = scr.availableGeometry()
        if avail.width() <= 0 or avail.height() <= 0:
            return
        cx = self.x() + self._w / 2
        cy = self.y() + self._h / 2
        self.cfg.set('rx', (cx - avail.left()) / avail.width())
        self.cfg.set('ry', (cy - avail.top()) / avail.height())
        self.cfg.set('facing', self.facing)
        self.cfg.set('scale', self.scale)
        self.cfg.save()

    def _go_default_corner(self) -> None:
        scr = self._screen_available()
        avail = scr.availableGeometry()
        x = avail.right() - self._w - catalog.CORNER_MARGIN
        y = avail.bottom() - self._h
        logging.info('回到右下角 screen=%s avail=(%d,%d,%d,%d) dpr=%s -> (%d,%d)',
                     scr.name(), avail.left(), avail.top(), avail.right(),
                     avail.bottom(), scr.devicePixelRatio(), x, y)
        self.move(x, y)
        self._save_position()

    def set_on_top(self, on: bool) -> None:
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, on)
        self.cfg.set('on_top', on)
        self.cfg.save()
        self.show()
        if on:
            self.raise_()

    def showEvent(self, event) -> None:  # noqa: N802 (Qt 命名)
        """窗口显示时请求跳过任务栏，避免出现在任务管理器/底部 dock。"""
        super().showEvent(event)
        # setWindowFlag 重建原生窗口后 XID 变化，故每次显示都重新应用。
        QTimer.singleShot(0, lambda: x11_hints.skip_taskbar(int(self.winId())))

    def set_no_move(self, on: bool) -> None:
        """切换「不移动」：禁用自动移动；勾选瞬间若正在移动则立即停下回待机。"""
        self.no_move = bool(on)
        self.cfg.set('no_move', self.no_move)
        self.cfg.save()
        if self.no_move and self._move_plan is not None:
            if self.idles:
                self._switch(self._pick(self.idles))  # 打断进行中的移动

    def set_locked(self, on: bool) -> None:
        """锁定后窗口完全鼠标穿透，只能通过系统托盘解锁。"""
        on = bool(on)
        if on == self.locked:
            return
        self.locked = on
        self._press_global = None
        self._grab_offset = None
        self._dragging = False
        self._stop_fly()
        self.cfg.set('locked', on)
        self.cfg.save()
        was_visible = self.isVisible()
        self.setWindowFlag(Qt.WindowType.WindowTransparentForInput, on)
        if was_visible:
            self.show()
            if self.cfg.get('on_top', True):
                self.raise_()
        # 通知 app 同步托盘勾选：从右键菜单锁定时托盘不会自己更新
        if self.on_locked_changed is not None:
            self.on_locked_changed(on)

    def set_speed(self, speed: float) -> None:
        """设置动画播放速率（1.0x ~ 2.0x），立即生效并持久化。"""
        speed = max(0.5, min(4.0, float(speed)))
        if abs(speed - self.speed) < 1e-6:
            return
        self.speed = speed
        self.lib.set_speed(speed)
        self.cfg.set('speed', speed)
        self.cfg.save()
        if self._move_plan is not None:
            # 移动时长按新速率折算，位置插值继续与动画同步
            self._move_plan['duration'] = self.lib.duration(self.anim) / speed

    def set_drag_physics(self, on: bool) -> None:
        """开关拖动物理（松手抛出、重力、反弹与倾斜）。"""
        self.drag_physics = bool(on)
        self.cfg.set('drag_physics', self.drag_physics)
        self.cfg.save()
        if not self.drag_physics:
            self._stop_fly()
            self._lean_deg = 0.0
            self.update()

    # ================================================================ 播放
    def _switch(self, name: str) -> None:
        """切换到指定动画（链式模型：全部一次性播放）。"""
        self._cancel_move()
        self.anim = name
        movie = self.lib.movie(name)
        self.movie = movie
        self._mask_initialized = False
        movie.stop()
        movie.jumpToFrame(0)
        self._ended_fired = False
        self._rebuild_frame()
        movie.start()

    def _on_frame(self, name: str, n: int) -> None:
        """媒体帧推进回调：重建画面；最后一帧触发播完处理。"""
        if name != self.anim or self.movie is None:
            return
        self._rebuild_frame()
        self.update()
        if n >= self.lib.frames(name) - 1 and not self._ended_fired:
            self._ended_fired = True
            self.movie.stop()  # 停在最后一帧，等 _on_anim_ended 切走
            self._on_anim_ended(name)

    def _rebuild_frame(self) -> None:
        """重建当前帧：裁切到内容区 + 缩放 + 朝向镜像 + 生成窗口 mask。"""
        if self.movie is None:
            return
        pm = self.movie.currentPixmap()
        if pm.isNull():
            return
        img = pm.toImage()
        if self.facing == 'right':
            img = img.mirrored(True, False)
        # 裁掉 webm 四周空白：只保留角色内容区，桌宠不再悬在大矩形里
        img = img.copy(catalog.CONTENT_X, catalog.CONTENT_Y,
                       catalog.CONTENT_W, catalog.CONTENT_H)
        w_c = max(1, int(round(catalog.CONTENT_W * self.scale)))
        h_c = max(1, int(round(catalog.CONTENT_H * self.scale)))
        img = img.scaled(w_c, h_c,
                         Qt.AspectRatioMode.IgnoreAspectRatio,
                         Qt.TransformationMode.SmoothTransformation)
        # XWayland's translucent backing store is more reliable with premultiplied
        # alpha. Keeping straight RGBA here can leave the old frame's RGB values
        # behind when a transparent pixel is repainted.
        self._frame_pixmap = QPixmap.fromImage(
            img.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
        )
        self._sync_mask()

    def _sync_mask(self, force: bool = False) -> None:
        """按当前帧 alpha 设置窗口 mask：透明区域鼠标穿透到下层窗口。"""
        if not self._use_native_mask:
            # KWin's XWayland shape path can leave a black rectangle or the
            # first frame's outline on translucent windows. KDE receives the
            # full transparent surface instead; the visual frame is still
            # alpha-composited normally.
            if not self.mask().isNull():
                self.clearMask()
            return
        if self._dragging or (self._mask_initialized and not force):
            return
        canvas = QImage(self._w, self._h, QImage.Format.Format_ARGB32)
        canvas.fill(Qt.GlobalColor.transparent)
        p = QPainter(canvas)
        if self._frame_pixmap is not None:
            p.drawPixmap(0, 0, self._frame_pixmap)
        p.end()
        # VP9 alpha decoding can leave black RGB values with alpha 1-2 around
        # the frame edges. Treat those codec crumbs as transparent so KWin does
        # not turn them into a visible rectangular window outline.
        alpha_mask = canvas.createAlphaMask(
            Qt.ImageConversionFlag.MonoOnly | Qt.ImageConversionFlag.ThresholdDither
        )
        self.setMask(QBitmap.fromImage(alpha_mask))
        self._mask_initialized = True

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt 命名)
        painter = QPainter(self)
        # Clear removes both alpha and RGB from the backing store. Source-over
        # with a transparent brush only changes alpha and can preserve a black
        # RGB fringe in KWin/XWayland.
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
        painter.fillRect(self.rect(), Qt.GlobalColor.transparent)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        if self._frame_pixmap is not None:
            sx, sy = self._squash_sx, self._squash_sy
            lean = self._lean_deg
            if abs(sx - 1.0) > 1e-3 or abs(sy - 1.0) > 1e-3 or abs(lean) > 1e-3:
                # 以脚底中点为锚：Q 弹压缩与惯性倾斜都不该让角色离地
                painter.translate(self._w / 2.0, float(self._h))
                if abs(lean) > 1e-3:
                    painter.rotate(lean)
                painter.scale(sx, sy)
                painter.translate(-self._w / 2.0, -float(self._h))
            # 内容已裁切到脚贴底线的区域，直接铺满窗口
            painter.drawPixmap(0, 0, self._frame_pixmap)
        painter.end()

    def icon_pixmap(self, size: int = 64) -> QPixmap:
        """托盘图标：取当前帧（无则待机首帧）缩放。"""
        pm = self._frame_pixmap
        if pm is None and self.idle:
            pm = self.lib.movie(self.idle).currentPixmap()
        return pm.scaled(size, size,
                         Qt.AspectRatioMode.KeepAspectRatio,
                         Qt.TransformationMode.SmoothTransformation)

    # ================================================================ 动画链
    def _on_anim_ended(self, name: str) -> None:
        if name == self.drag and self._dragging:
            # 超长拖拽：拖拽动画循环重播，继续跟手
            self.movie.jumpToFrame(0)
            self._ended_fired = False
            self.movie.start()
            return
        if name in self.turns:
            # 转向动画播完 → 翻转朝向
            self.facing = 'right' if self.facing == 'left' else 'left'
        if name == self.drag or name in self.clicks:
            # 交互打断的动画播完 → 待机缓冲（一次性），待机播完再进链
            if self.idles:
                self._switch(self._pick(self.idles))
            return
        self._pick_next()

    def _pick_next(self) -> None:
        """动画链：30% 待机 / 10% 转向 / 40% 动作 / 20% 移动（空间不够回退动作）。

        「不移动」模式下跳过移动分支，其概率并入动作 → 30% 待机 / 10% 转向 / 60% 动作。
        """
        roll = random.random()
        if roll < catalog.P_IDLE:
            if self.idles:
                self._switch(self._pick(self.idles, exclude=self.anim))
            else:
                self._switch(self._pick(self.acts, exclude=self.anim))
        elif roll < catalog.P_TURN:
            if self.turns:
                self._switch(self._pick(self.turns, exclude=self.anim))
            else:
                self._switch(self._pick(self.acts, exclude=self.anim))
        elif roll < catalog.P_ACTS:
            self._switch(self._pick(self.acts, exclude=self.anim))
        else:
            if self.no_move or not self._try_move():
                self._switch(self._pick(self.acts, exclude=self.anim))

    @staticmethod
    def _pick(pool: list[str], exclude: str | None = None) -> str:
        entries = [n for n in pool if n != exclude] or pool
        return random.choice(entries)

    # ================================================================ 移动
    def _try_move(self, name: str | None = None) -> bool:
        """计划一次朝 facing 方向的移动；屏幕空间不够返回 False。

        name 给定时使用指定动画（手动触发），否则随机选一个移动姿态。
        """
        if self._move_plan is not None:
            return True  # 已在移动/已计划
        scr = self._screen_available()
        if scr is None:
            return False
        avail = scr.availableGeometry()
        dir_sign = 1 if self.facing == 'right' else -1
        cx = self.x() + self._w / 2
        distance = random.randint(catalog.MOVE_MIN_PX, catalog.MOVE_MAX_PX)
        target_cx = cx + dir_sign * distance
        half_w = self._w / 2
        left_bound = avail.left() + catalog.MOVE_MARGIN + half_w
        right_bound = avail.right() - catalog.MOVE_MARGIN - half_w
        if target_cx < left_bound or target_cx > right_bound:
            return False
        if not self.moves:
            return False
        move_name = name or self._pick(self.moves)
        duration = self.lib.duration(move_name)
        self._switch(move_name)
        self._move_plan = {
            'start_x': self.x(),
            'target_x': int(round(target_cx - half_w)),
            'y': self.y(),
            'duration': duration,
        }
        self._move_timer.start()
        return True

    def _trigger_move(self, name: str) -> None:
        """手动触发移动（右键菜单）：先打断当前移动，再朝 facing 方向走动；
        屏幕空间不足则原地播放走路姿态（不位移）。"""
        self._cancel_move()
        if not self._try_move(name):
            self._switch(name)  # 贴边放不下：原地播放走路姿态，不位移

    def _on_move_tick(self) -> None:
        """位置驱动：跟随动画播放进度插值（前后各 2s 不动，中间走完全程）。"""
        plan = self._move_plan
        if not plan or self.movie is None:
            self._move_timer.stop()
            return
        t = self.movie.currentTimeSeconds()
        lead, tail = catalog.MOVE_LEAD_SEC, catalog.MOVE_TAIL_SEC
        dur = plan['duration']
        if t <= lead:
            x = plan['start_x']
        elif t >= dur - tail:
            x = plan['target_x']
        else:
            progress = (t - lead) / max(0.1, dur - lead - tail)
            x = plan['start_x'] + (plan['target_x'] - plan['start_x']) * progress
        self.move(int(round(x)), plan['y'])
        if t >= dur - tail:
            # 到位：提交终点，动画自然播完后续链
            self._move_timer.stop()
            self._move_plan = None
            self._save_position()

    def _cancel_move(self) -> None:
        self._move_timer.stop()
        self._move_plan = None

    # ================================================================ Q 弹 / 物理
    def _fx_needed(self) -> bool:
        return self._squash_active or self._flying

    def _sync_fx_timer(self) -> None:
        if self._fx_needed():
            if not self._fx_timer.isActive():
                self._fx_timer.start()
        elif self._fx_timer.isActive():
            self._fx_timer.stop()

    def trigger_squash(self) -> None:
        """点击 Q 弹：立即从压缩状态开始阻尼回弹（连点会重新触发）。"""
        self._squash_t = 0.0
        self._squash_active = True
        self._apply_squash()
        self._sync_fx_timer()
        self.update()

    def _apply_squash(self) -> None:
        """按当前相位算出纵/横缩放（体积守恒式补偿，脚底为锚）。"""
        if not self._squash_active:
            self._squash_sx = 1.0
            self._squash_sy = 1.0
            return
        t = self._squash_t
        decay = math.exp(-t / catalog.SQUASH_TAU)
        # cos 相位保证 t=0 时压缩最大，随后阻尼振荡回 1.0
        offset = catalog.SQUASH_AMPLITUDE * decay * math.cos(catalog.SQUASH_OMEGA * t)
        self._squash_sy = max(0.5, 1.0 - offset)
        self._squash_sx = max(0.5, 1.0 + offset * catalog.SQUASH_X_RATIO)

    def _stop_squash(self) -> None:
        self._squash_active = False
        self._squash_t = 0.0
        self._squash_sx = 1.0
        self._squash_sy = 1.0
        self._sync_fx_timer()

    def _record_sample(self, global_pos: QPoint) -> None:
        """记录拖拽轨迹采样点，用于松手时估算抛出速度。"""
        now = time.monotonic()
        self._samples.append((now, QPoint(global_pos)))
        cutoff = now - max(catalog.VELOCITY_WINDOW_SEC * 3, 0.25)
        while self._samples and self._samples[0][0] < cutoff:
            self._samples.pop(0)

    def _drag_velocity(self) -> tuple[float, float]:
        """用采样窗口内的位移估算速度（px/s）。"""
        if len(self._samples) < 2:
            return 0.0, 0.0
        t_end, p_end = self._samples[-1]
        base = t_end - catalog.VELOCITY_WINDOW_SEC
        t_start, p_start = self._samples[0]
        for t, p in self._samples:
            if t >= base:
                t_start, p_start = t, p
                break
        dt = t_end - t_start
        if dt <= 1e-4:
            return 0.0, 0.0
        return (p_end.x() - p_start.x()) / dt, (p_end.y() - p_start.y()) / dt

    def _update_lean(self, vx: float) -> None:
        """按水平速度产生倾斜（惯性/离心感）。"""
        if not self.drag_physics:
            self._lean_deg = 0.0
            return
        lean = max(-catalog.LEAN_MAX_DEG,
                   min(catalog.LEAN_MAX_DEG, -vx * catalog.LEAN_PER_PX_S))
        self._lean_deg = lean

    def _start_fly(self, vx: float, vy: float) -> None:
        """松手抛出：进入重力/反弹模拟。"""
        speed = math.hypot(vx, vy)
        if speed > catalog.THROW_MAX_SPEED:
            k = catalog.THROW_MAX_SPEED / speed
            vx, vy = vx * k, vy * k
        self._fly_vx = vx
        self._fly_vy = vy
        self._fly_x = float(self.x())
        self._fly_y = float(self.y())
        self._flying = True
        self._sync_fx_timer()

    def _stop_fly(self) -> None:
        if self._flying:
            self._flying = False
            self._fly_vx = 0.0
            self._fly_vy = 0.0
            self._lean_deg = 0.0
        self._sync_fx_timer()

    def _on_fx_tick(self) -> None:
        """60fps 特效步进：Q 弹相位推进 + 抛飞物理积分。"""
        dt = catalog.ANIM_TICK_MS / 1000.0
        dirty = False

        if self._squash_active:
            self._squash_t += dt
            if self._squash_t >= catalog.SQUASH_DURATION:
                self._stop_squash()
            else:
                self._apply_squash()
            dirty = True

        if self._flying:
            self._step_fly(dt)
            dirty = True

        self._sync_fx_timer()
        if dirty:
            self.update()

    def _step_fly(self, dt: float) -> None:
        """一步抛体运动：重力 + 空气阻力 + 屏幕边界反弹。"""
        scr = self._screen_available()
        if scr is None:
            self._stop_fly()
            return
        avail = scr.availableGeometry()
        left, top = avail.left(), avail.top()
        right = avail.right() - self._w + 1
        bottom = avail.bottom() - self._h + 1

        self._fly_vy += catalog.GRAVITY * dt
        self._fly_vx *= catalog.AIR_DRAG
        self._fly_vy *= catalog.AIR_DRAG
        self._fly_x += self._fly_vx * dt
        self._fly_y += self._fly_vy * dt

        on_ground = False
        if self._fly_x <= left:
            self._fly_x = float(left)
            self._fly_vx = abs(self._fly_vx) * catalog.WALL_BOUNCE
        elif self._fly_x >= right:
            self._fly_x = float(right)
            self._fly_vx = -abs(self._fly_vx) * catalog.WALL_BOUNCE
        if self._fly_y <= top:
            self._fly_y = float(top)
            self._fly_vy = abs(self._fly_vy) * catalog.WALL_BOUNCE
        elif self._fly_y >= bottom:
            self._fly_y = float(bottom)
            self._fly_vy = -abs(self._fly_vy) * catalog.GROUND_BOUNCE
            self._fly_vx *= catalog.GROUND_FRICTION
            on_ground = True

        self.move(int(round(self._fly_x)), int(round(self._fly_y)))
        self._update_lean(self._fly_vx)

        settled = (
            on_ground
            and abs(self._fly_vy) < catalog.SETTLE_SPEED
            and abs(self._fly_vx) < catalog.SETTLE_SPEED
        )
        if settled:
            self._stop_fly()
            self._save_position()
            if self.idles and self.anim == self.drag:
                self._switch(self._pick(self.idles))

    # ================================================================ 交互
    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_global = event.globalPosition().toPoint()
            self._grab_offset = self._press_global - self.pos()
            self._dragging = False
            self._cancel_move()  # 按下即打断移动
            self._stop_fly()     # 抓住正在飞的桌宠
            self._samples = []
            self._record_sample(self._press_global)
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._press_global is None or not (event.buttons() & Qt.MouseButton.LeftButton):
            return
        g = event.globalPosition().toPoint()
        delta = g - self._press_global
        if not self._dragging:
            if math.hypot(delta.x(), delta.y()) < catalog.DRAG_THRESHOLD * self.scale:
                return  # 未超阈值：仍是点击候选
            self._dragging = True
            if self.drag:
                self._switch(self.drag)  # 进入拖拽：播放悬空反馈动画
        self.move(g - self._grab_offset)  # 跟手（保持抓起时的偏移）
        self._record_sample(g)
        if self.drag_physics:
            vx, _ = self._drag_velocity()
            self._update_lean(vx)   # 拖拽过程中的惯性/离心倾斜
        event.accept()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton:
            super().mouseReleaseEvent(event)
            return
        was_dragging = self._dragging
        g = event.globalPosition().toPoint()
        dist = 0.0
        if self._press_global is not None:
            d = g - self._press_global
            dist = math.hypot(d.x(), d.y())
        if was_dragging:
            self._just_dragged = True  # 抑制拖拽结束后的幽灵点击
            QTimer.singleShot(150, self._clear_just_dragged)
            if self._grab_offset is not None:
                self.move(g - self._grab_offset)  # 停在松手处
            self._record_sample(g)
            vx, vy = self._drag_velocity()
            if self.drag_physics and math.hypot(vx, vy) >= catalog.THROW_MIN_SPEED:
                self._start_fly(vx, vy)  # 甩出去：交给重力/反弹接管
            else:
                self._lean_deg = 0.0
                self._save_position()
                if self.idles:
                    self._switch(self._pick(self.idles))  # 回待机缓冲
        elif dist < catalog.DRAG_THRESHOLD * self.scale:
            self._on_click()
        self._dragging = False
        self._samples = []
        self._mask_initialized = False
        self._sync_mask()
        self._press_global = None
        self._grab_offset = None
        event.accept()

    def _clear_just_dragged(self) -> None:
        self._just_dragged = False

    def _on_click(self) -> None:
        """真点击 → Q 弹反馈 + 随机一个点击回应动画。"""
        if self._just_dragged:
            return
        # Q 弹独立于动画链：连点也能立刻重复触发挤压回弹
        self.trigger_squash()
        if not self.clicks:
            return
        if self.idles and self.anim not in self.idles and self.anim not in self.clicks:
            return  # 链上非待机动画播放中不打断
        self._cancel_move()
        self._switch(self._pick(self.clicks))

    def _is_character_pixel(self, point: QPoint) -> bool:
        """当前窗口坐标是否落在角色可见像素上。"""
        if self._frame_pixmap is None:
            return False
        x = point.x()
        y = point.y()
        if x < 0 or y < 0 or x >= self._frame_pixmap.width() or y >= self._frame_pixmap.height():
            return False
        return self._frame_pixmap.toImage().pixelColor(x, y).alpha() >= 8

    def contextMenuEvent(self, event) -> None:  # noqa: N802
        if self.locked or not self._is_character_pixel(event.pos()):
            event.ignore()
            return
        menu = self.build_context_menu()
        menu.exec(event.globalPos())

    def build_context_menu(self) -> QMenu:
        """构造右键菜单（不弹出），便于复用与测试。"""
        menu = QMenu(self)

        # 仅 Chat 版注入了回调；无 Chat 版不显示该项
        if self.on_chat_requested is not None:
            menu.addAction('和它说话…', self._request_chat)
            menu.addSeparator()

        if self.idles:
            m_idle = menu.addMenu('动画 · 待机')
            for n in self.idles:
                m_idle.addAction(n, lambda n=n: self._switch(n))
        if self.turns:
            m_turn = menu.addMenu('动画 · 转向')
            for n in self.turns:
                m_turn.addAction(n, lambda n=n: self._switch(n))

        m_moves = menu.addMenu('动画 · 移动')
        for n in self.moves:
            m_moves.addAction(n, lambda n=n: self._trigger_move(n))

        m_clicks = menu.addMenu('动画 · 点击回应')
        for n in self.clicks:
            m_clicks.addAction(n, lambda n=n: self._switch(n))

        m_acts = menu.addMenu('动画 · 随机动作')
        for n in self.acts:
            m_acts.addAction(n, lambda n=n: self._switch(n))

        m_char = menu.addMenu('切换角色')
        current = str(self.cfg.get('character', catalog.DEFAULT_CHARACTER))
        for cid in catalog.list_available_characters():
            act = m_char.addAction(cid)
            act.setCheckable(True)
            act.setChecked(cid == current)
            act.triggered.connect(lambda checked=False, cid=cid: self._request_switch_character(cid))
        m_char.addSeparator()
        m_char.addAction('打开角色文件夹…', self._open_characters_dir)
        m_char.addAction('重新扫描角色', self._rescan_characters)

        menu.addSeparator()
        menu.addAction('回到右下角', self._go_default_corner)

        on_top = menu.addAction('窗口置顶')
        on_top.setCheckable(True)
        on_top.setChecked(bool(self.cfg.get('on_top', True)))
        on_top.toggled.connect(self.set_on_top)

        no_move = menu.addAction('不移动')
        no_move.setCheckable(True)
        no_move.setChecked(self.no_move)
        no_move.toggled.connect(self.set_no_move)

        physics = menu.addAction('拖动物理')
        physics.setCheckable(True)
        physics.setChecked(self.drag_physics)
        physics.toggled.connect(self.set_drag_physics)

        lock = menu.addAction('锁定并穿透鼠标')
        lock.triggered.connect(lambda checked=False: self.set_locked(True))

        auto = menu.addAction('开机自启')
        auto.setCheckable(True)
        auto.setChecked(autostart_mod.is_enabled())
        auto.toggled.connect(autostart_mod.set_enabled)

        m_speed = menu.addMenu('播放速度')
        for sp in catalog.SPEED_STEPS:
            act = m_speed.addAction(f'{sp:g}x')
            act.setCheckable(True)
            act.setChecked(abs(self.speed - sp) < 0.02)
            act.triggered.connect(lambda checked=False, sp=sp: self.set_speed(sp))

        m_scale = menu.addMenu('大小')
        for s in catalog.SCALE_STEPS:
            px = int(round(catalog.CONTENT_W * s))
            act = m_scale.addAction(f'{px}px')
            act.setCheckable(True)
            act.setChecked(abs(self.scale - s) < 0.02)
            act.triggered.connect(lambda checked=False, s=s: self.change_scale(s))

        menu.addSeparator()
        menu.addAction('退出', self._request_quit)
        return menu

    def _request_switch_character(self, character_id: str) -> None:
        """请求切换角色；优先交给 app 做热切换，否则只保存配置。"""
        if self.on_switch_character is not None:
            self.on_switch_character(character_id)
        else:
            self.cfg.set('character', character_id)
            self.cfg.save()

    def _open_characters_dir(self) -> None:
        """打开用户角色目录（不存在则先创建），方便一键安装的用户放素材。"""
        path = catalog.user_characters_dir()
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError:
            logging.warning('创建角色目录失败: %s', path)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def _rescan_characters(self) -> None:
        """重新扫描角色目录；新增角色无需重启即可出现在菜单里。"""
        ids = catalog.list_available_characters()
        logging.info('重新扫描角色: %s', ids)
        if self.on_rescan_characters is not None:
            self.on_rescan_characters()

    def _request_chat(self) -> None:
        """打开 AI 对话输入框（仅 Chat 版有回调）。"""
        if self.on_chat_requested is not None:
            self.on_chat_requested()

    def _request_quit(self) -> None:
        self._save_position()
        QApplication.instance().quit()

    def closeEvent(self, event) -> None:  # noqa: N802
        self._fx_timer.stop()
        self._save_position()
        super().closeEvent(event)

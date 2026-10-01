# -*- coding: utf-8 -*-
"""
WebM-backed clip library（webm 主路线）。

使用 imageio-ffmpeg 自带的静态 ffmpeg 解码 640×360 透明 webm：
- read_frames(..., pix_fmt='rgba', bits_per_pixel=32, input_params=['-c:v','libvpx-vp9'])
  可正确保留 VP9 alpha，输出 RGBA 原始帧。

线程模型：
- 后台 reader 线程只负责把 RGBA 字节放入有界队列；
- 主线程 QTimer 按视频 fps 从队列取帧，构造 QImage/QPixmap 并发出 frameChanged；
- 所有 Qt GUI 操作只发生在主线程。
"""

from __future__ import annotations

import logging
import os
import queue
import threading
from pathlib import Path

from PySide6.QtCore import QObject, QRect, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen, QPixmap

from . import catalog

logger = logging.getLogger(__name__)

# 进程内元数据缓存：避免反复切换角色时重复调用 count_frames_and_secs
_META_CACHE: dict[str, tuple[int, float]] = {}

try:
    import imageio_ffmpeg
except Exception as exc:  # pragma: no cover - 依赖缺失时无法使用 webm 路线
    imageio_ffmpeg = None
    _IMPORT_ERROR = exc
else:
    _IMPORT_ERROR = None


def ffmpeg_status() -> tuple[bool, str]:
    """检查 ffmpeg 解码组件是否可用。

    返回 (可用, 说明)。用于启动自检：若 ffmpeg 缺失或被安全软件隔离，
    程序不崩溃，改为占位画面并提示用户。
    """
    if imageio_ffmpeg is None:
        return False, f'imageio-ffmpeg 不可用：{_IMPORT_ERROR}'
    try:
        exe = imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as exc:
        return False, f'找不到 ffmpeg 可执行文件：{exc}'
    path = Path(exe)
    # 系统 ffmpeg 通常在 PATH 上，用 which 结果也会落到这里
    if not path.exists():
        return False, f'ffmpeg 不存在（可能被安全软件隔离或已删除）：{exe}'
    if not os.access(exe, os.X_OK):
        return False, f'ffmpeg 没有可执行权限：{exe}'
    return True, exe


def placeholder_pixmap(label: str, width: int, height: int) -> QPixmap:
    """解码不可用时的占位画面：半透明圆 + 角色首字，保证桌宠可见可交互。"""
    image = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    diameter = int(min(width, height) * 0.6)
    left = (width - diameter) // 2
    top = (height - diameter) // 2

    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    painter.setPen(QPen(QColor(80, 130, 200, 180), 2))
    painter.setBrush(QColor(140, 190, 245, 170))
    painter.drawEllipse(left, top, diameter, diameter)

    text = (label or '?').strip()[:1] or '?'
    font = QFont()
    font.setPixelSize(max(12, int(diameter * 0.5)))
    font.setBold(True)
    painter.setFont(font)
    painter.setPen(QColor(255, 255, 255, 230))
    painter.drawText(
        QRect(left, top, diameter, diameter),
        int(Qt.AlignmentFlag.AlignCenter),
        text,
    )
    painter.end()
    return QPixmap.fromImage(image)


class WebMClip(QObject):
    """与窗口层期望的媒体播放器接口兼容。"""

    available = imageio_ffmpeg is not None

    frameChanged = Signal(int)
    finished = Signal()
    errorOccurred = Signal(str)

    def __init__(self, path, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.path = path
        self._w = catalog.CANVAS_W
        self._h = catalog.CANVAS_H
        self._bpp = 4  # RGBA

        # 元数据（惰性填充；由 MovieLibrary 并行 warm 或首次使用时读取）
        self._frame_count = 0
        self._duration = 0.0
        self._fps = 24.0

        # 播放状态
        self._speed = 1.0
        self._queue: queue.Queue = queue.Queue(maxsize=8)
        self._stop_evt = threading.Event()
        self._thread: threading.Thread | None = None
        self._retired_threads: list[tuple[threading.Thread, threading.Event]] = []
        self._reader_lock = threading.Lock()
        self._timer = QTimer(self)
        self._timer.setInterval(self._timer_interval())
        self._timer.timeout.connect(self._poll)

        self._current_image: QImage | None = None
        self._current_pixmap: QPixmap | None = None
        self._first_image: QImage | None = None
        self._first_lock = threading.Lock()   # warm_first_frame 与主线程共享 _first_image
        self._decode_failed = False
        self._frame_index = 0
        self._ended_fired = False
        self._running = False

    # ------------------------------------------------------------ 预热
    def warm_first_frame(self) -> bool:
        """后台预解码首帧（只产出 QImage，不触碰 QPixmap/QTimer，线程安全）。

        QPixmap 只能在 GUI 线程构造，所以这里只缓存 QImage；
        jumpToFrame(0) 在主线程用缓存直接建 QPixmap，零同步解码卡顿。
        """
        with self._first_lock:
            if self._first_image is not None:
                return True
        image = self._decode_first_image()
        if image is None:
            return False
        with self._first_lock:
            self._first_image = image
        return True

    def decode_failed(self) -> bool:
        return self._decode_failed

    # ------------------------------------------------------------ metadata
    def _ensure_meta(self) -> None:
        if self._duration > 0 or imageio_ffmpeg is None:
            return
        key = str(self.path)
        cached = _META_CACHE.get(key)
        if cached is not None:
            self._frame_count, self._duration = cached
            if self._frame_count > 0 and self._duration > 0:
                self._fps = self._frame_count / self._duration
            return
        try:
            frames, secs = imageio_ffmpeg.count_frames_and_secs(key)
            if frames and frames > 0:
                self._frame_count = int(frames)
            if secs and secs > 0:
                self._duration = float(secs)
            if self._frame_count > 0 and self._duration > 0:
                self._fps = self._frame_count / self._duration
            _META_CACHE[key] = (self._frame_count, self._duration)
        except Exception as exc:
            logger.warning('webm 元数据读取失败 %s: %s', self.path, exc)
            # 保留默认值，后续 reader 会尝试从 read_frames 的 meta 补充

    def warm_meta(self) -> None:
        """预取元数据（可被线程池并行调用）。"""
        self._ensure_meta()

    def _timer_interval(self) -> int:
        speed = self._speed if self._speed > 0 else 1.0
        if self._fps > 0:
            return max(1, int(round(1000 / (self._fps * speed))))
        return max(1, int(round(catalog.FRAME_MS / speed)))

    def setSpeed(self, speed: float) -> None:
        """设置播放速率（1.0 = 原速）；播放中立即生效。"""
        speed = max(0.1, float(speed))
        if abs(speed - self._speed) < 1e-6:
            return
        self._speed = speed
        self._timer.setInterval(self._timer_interval())

    def speed(self) -> float:
        return self._speed

    def frameCount(self) -> int:
        if self._frame_count <= 0:
            self._ensure_meta()
        return max(1, self._frame_count)

    def duration(self) -> float:
        if self._duration <= 0:
            self._ensure_meta()
        return self._duration

    def currentFrameNumber(self) -> int:
        return self._frame_index

    def currentTimeSeconds(self) -> float:
        if self._fps <= 0:
            return 0.0
        return self._frame_index / self._fps

    def effectiveDuration(self) -> float:
        """按当前播放速率折算的实际播放时长（秒）。"""
        speed = self._speed if self._speed > 0 else 1.0
        return self.duration() / speed

    def currentPixmap(self):
        return self._current_pixmap

    # ------------------------------------------------------------ lifecycle
    def start(self) -> None:
        self.cleanup_readers()
        if self._running:
            return
        if imageio_ffmpeg is None:
            self.errorOccurred.emit(str(_IMPORT_ERROR or 'imageio_ffmpeg 不可用'))
            return

        stop_evt = threading.Event()
        self._stop_evt = stop_evt
        self._queue = queue.Queue(maxsize=8)
        self._frame_index = 0
        self._ended_fired = False
        self._running = True

        self._thread = threading.Thread(target=self._reader, args=(stop_evt,), daemon=True)
        self._thread.start()
        self._timer.setInterval(self._timer_interval())
        self._timer.start()

    def stop(self) -> None:
        self._running = False
        self._timer.stop()
        old_thread = self._thread
        old_stop = self._stop_evt
        old_stop.set()
        self._thread = None
        if old_thread is not None and old_thread.is_alive():
            # 短等让正常 reader 收尾；卡死线程保留追踪，下一次 start/cleanup 再收。
            old_thread.join(timeout=0.12)
            if old_thread.is_alive():
                with self._reader_lock:
                    self._retired_threads.append((old_thread, old_stop))
                    self._retired_threads = self._retired_threads[-2:]

    def cleanup_readers(self) -> None:
        """回收已停止 reader；不丢弃仍存活的追踪记录。"""
        with self._reader_lock:
            retired = list(self._retired_threads)
        alive: list[tuple[threading.Thread, threading.Event]] = []
        for thread, stop_evt in retired:
            stop_evt.set()
            thread.join(timeout=0.05)
            if thread.is_alive():
                alive.append((thread, stop_evt))
        with self._reader_lock:
            self._retired_threads = alive[-2:]

    def reader_snapshot(self) -> tuple[bool, int]:
        """返回 (当前 reader 是否存活, 退役 reader 数)，供诊断/测试使用。"""
        self.cleanup_readers()
        return bool(self._thread and self._thread.is_alive()), len(self._retired_threads)

    def jumpToFrame(self, frame_index: int) -> bool:
        # 本项目只需要回到首帧；完整 seek 通过重启 reader + 丢弃帧实现。
        if frame_index <= 0:
            self.stop()
            self._frame_index = 0
            with self._first_lock:
                cached = self._first_image
            if cached is None:
                # 未预热成功：退回同步解码（GUI 线程，可能有短暂卡顿）
                if self.warm_first_frame():
                    with self._first_lock:
                        cached = self._first_image
            if cached is not None:
                self._current_image = cached.copy()
                self._current_pixmap = QPixmap.fromImage(self._current_image)
            return True
        return False

    def _decode_first_image(self) -> QImage | None:
        """解码首帧为 QImage；顺带补齐 fps/duration 元数据。失败返回 None。"""
        if imageio_ffmpeg is None:
            self._decode_failed = True
            return None
        gen = None
        try:
            gen = imageio_ffmpeg.read_frames(
                str(self.path),
                pix_fmt='rgba',
                bits_per_pixel=self._bpp * 8,
                input_params=['-c:v', 'libvpx-vp9'],
            )
            meta = next(gen)
            frame = next(gen)
            if meta.get('fps'):
                self._fps = float(meta['fps'])
            if meta.get('duration'):
                self._duration = float(meta['duration'])
            if self._frame_count <= 0 and self._fps > 0 and self._duration > 0:
                self._frame_count = int(round(self._fps * self._duration))
            expect = self._w * self._h * self._bpp
            if len(frame) != expect:
                self._decode_failed = True
                return None
            img = QImage(frame, self._w, self._h, self._w * self._bpp,
                         QImage.Format.Format_RGBA8888)
            if img.isNull():
                self._decode_failed = True
                return None
            self._decode_failed = False
            return img.copy()
        except Exception as exc:
            # ffmpeg 被安全软件隔离/删除时会走到这里，不能让它冒泡成崩溃
            logger.warning('webm 首帧预解码失败 %s: %s', self.path, exc)
            self._decode_failed = True
            return None
        finally:
            if gen is not None:
                try:
                    gen.close()
                except Exception:
                    pass

    # ------------------------------------------------------------ reader
    def _reader(self, stop_evt: threading.Event) -> None:
        gen = None
        try:
            q = self._queue
            gen = imageio_ffmpeg.read_frames(
                str(self.path),
                pix_fmt='rgba',
                bits_per_pixel=self._bpp * 8,
                input_params=['-c:v', 'libvpx-vp9'],
            )
            meta = next(gen)
            # 用实际流信息修正元数据
            if meta.get('fps'):
                self._fps = float(meta['fps'])
            if meta.get('duration'):
                self._duration = float(meta['duration'])
            if self._frame_count <= 0 and self._fps > 0 and self._duration > 0:
                self._frame_count = int(round(self._fps * self._duration))

            for frame in gen:
                if stop_evt.is_set():
                    break
                while not stop_evt.is_set():
                    try:
                        q.put(frame, timeout=0.2)
                        break
                    except queue.Full:
                        # Do not drop frames: dropping makes the animation jump
                        # and can expose stale/partially updated window content.
                        continue
            # 正常播完时放入结束标记
            if not stop_evt.is_set():
                try:
                    q.put(None, timeout=0.2)
                except queue.Full:
                    pass
        except Exception as exc:
            logger.exception('webm 解码失败: %s', self.path)
            self.errorOccurred.emit(str(exc))
        finally:
            if gen is not None:
                try:
                    gen.close()
                except Exception:
                    pass

    def _poll(self) -> None:
        """主线程按视频帧率逐帧取帧，不跳帧、不积压追帧。

        注意：不能一次清空队列只处理最新帧，否则会把中间帧丢弃，
        导致动画视觉上“快进”。这里每次只取最早的一帧。
        """
        try:
            item = self._queue.get_nowait()
        except queue.Empty:
            return

        if item is None:
            # 正常播完；若在处理最后一帧时已经由窗口层启动了下一个动画，
            # self._queue 已被替换，不会走到这里。
            if not self._ended_fired:
                self._ended_fired = True
                self._running = False
                self._timer.stop()
                self.finished.emit()
            return

        self._process_frame(item)

    def _process_frame(self, data: bytes) -> None:
        expect = self._w * self._h * self._bpp
        if len(data) != expect:
            logger.warning('webm 帧长度异常: got=%d expect=%d', len(data), expect)
            return
        img = QImage(data, self._w, self._h, self._w * self._bpp,
                     QImage.Format.Format_RGBA8888)
        if img.isNull():
            return
        self._current_image = img.copy()
        self._current_pixmap = QPixmap.fromImage(self._current_image)
        self._frame_index += 1
        self.frameChanged.emit(self._frame_index)

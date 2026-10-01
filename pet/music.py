# -*- coding: utf-8 -*-
"""Linux 音乐状态与歌词服务。

不绑定网易云/QQ 等 Windows 播放器：
- 播放状态通过可选的 ``playerctl`` 读取 MPRIS，覆盖 VLC、Spotify、
  Chromium、Firefox、网易云 Linux 客户端等支持 MPRIS 的播放器；
- 歌词通过 LRCLIB 的公开接口获取，网络调用在后台线程执行；
- 没有 playerctl 或网络不可用时静默降级，不影响桌宠主循环。
"""

from __future__ import annotations

import json
import logging
import subprocess
import threading
import urllib.parse
import urllib.request
from dataclasses import dataclass

from PySide6.QtCore import QObject, Signal

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Track:
    title: str = ''
    artist: str = ''
    album: str = ''
    status: str = 'Stopped'
    position: float = 0.0
    length: float = 0.0

    @property
    def is_playing(self) -> bool:
        return self.status.lower() == 'playing'

    @property
    def query(self) -> str:
        return ' - '.join(part for part in (self.artist, self.title) if part)


def _playerctl(*args: str, timeout: float = 1.0) -> str | None:
    try:
        result = subprocess.run(
            ['playerctl', *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def playerctl_available() -> bool:
    return _playerctl('--version') is not None


def current_track() -> Track | None:
    """读取当前默认 MPRIS 播放器，读取失败返回 None。"""
    raw = _playerctl('metadata', '--format', '{{status}}\t{{title}}\t{{artist}}\t{{album}}\t{{duration(position)}}\t{{mpris:length}}')
    if not raw:
        return None
    parts = raw.split('\t')
    if len(parts) < 6:
        return None

    def number(value: str) -> float:
        try:
            # playerctl duration 可能是 1:23.45，也可能是微秒数
            if ':' in value:
                minutes, seconds = value.split(':', 1)
                return float(minutes) * 60 + float(seconds)
            number_value = float(value)
            return number_value / 1_000_000 if number_value > 100_000 else number_value
        except (TypeError, ValueError):
            return 0.0

    return Track(
        status=parts[0],
        title=parts[1],
        artist=parts[2],
        album=parts[3],
        position=number(parts[4]),
        length=number(parts[5]),
    )


def player_command(command: str) -> bool:
    """执行 play/pause/next/previous/position 等 playerctl 命令。"""
    try:
        return subprocess.run(
            ['playerctl', command],
            timeout=1.0,
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        ).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def seek_relative(seconds: float) -> bool:
    sign = '+' if seconds >= 0 else ''
    return player_command(f'seek {sign}{seconds:g}')


def seek_absolute(seconds: float) -> bool:
    return player_command(f'position {max(0.0, seconds):g}')


def fetch_lyrics(track: Track, timeout: float = 8.0) -> list[tuple[float, str]]:
    """从 LRCLIB 读取同步歌词，返回 (秒, 文本) 列表。"""
    if not track.title:
        return []
    query = urllib.parse.urlencode({
        'track_name': track.title,
        'artist_name': track.artist,
        'album_name': track.album,
    })
    request = urllib.request.Request(
        f'https://lrclib.net/api/get?{query}',
        headers={'User-Agent': 'dsh-pet-kde/1.0'},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        data = json.loads(response.read().decode('utf-8'))
    synced = data.get('syncedLyrics') if isinstance(data, dict) else None
    return parse_lrc(synced or '')


def parse_lrc(text: str) -> list[tuple[float, str]]:
    """解析常见 [mm:ss.xx] LRC 时间轴。"""
    result: list[tuple[float, str]] = []
    for line in str(text or '').splitlines():
        if not line.startswith('[') or ']' not in line:
            continue
        tag, lyric = line.split(']', 1)
        raw_time = tag[1:]
        try:
            minute, second = raw_time.split(':', 1)
            timestamp = int(minute) * 60 + float(second)
        except (TypeError, ValueError):
            continue
        if lyric.strip():
            result.append((timestamp, lyric.strip()))
    return sorted(result)


def lyric_at(lines: list[tuple[float, str]], position: float) -> str:
    current = ''
    for timestamp, text in lines:
        if timestamp > position:
            break
        current = text
    return current


class MusicService(QObject):
    """后台轮询播放器并异步取歌词。"""

    trackChanged = Signal(object)
    lyricsReady = Signal(object, object)  # Track, list[(seconds, text)]
    error = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._track: Track | None = None
        self._lyrics: list[tuple[float, str]] = []
        self._fetching = False
        self._lock = threading.Lock()

    @property
    def track(self) -> Track | None:
        return self._track

    @property
    def lyrics(self) -> list[tuple[float, str]]:
        return list(self._lyrics)

    def poll(self) -> Track | None:
        track = current_track()
        old_key = self._track_key(self._track)
        self._track = track
        if self._track_key(track) != old_key:
            self._lyrics = []
            self.trackChanged.emit(track)
            if track is not None:
                self.fetch_lyrics()
        return track

    def fetch_lyrics(self) -> bool:
        if self._track is None or self._fetching:
            return False
        track = self._track
        self._fetching = True

        def worker() -> None:
            try:
                lines = fetch_lyrics(track)
            except Exception as exc:
                logger.info('歌词获取失败: %s', exc)
                self.error.emit(str(exc))
            else:
                self._lyrics = lines
                self.lyricsReady.emit(track, lines)
            finally:
                self._fetching = False

        threading.Thread(target=worker, daemon=True).start()
        return True

    @staticmethod
    def _track_key(track: Track | None) -> tuple | None:
        if track is None:
            return None
        return track.artist, track.title, track.album

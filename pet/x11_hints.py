"""X11 窗口提示辅助 —— 让 KDE 任务管理器/底部 dock 不显示桌宠。

Qt 的 WindowDoesNotAcceptFocus 在部分构建下不会发出
_NET_WM_STATE_SKIP_TASKBAR，这里通过标准 _NET_WM_STATE client message
直接请求 KWin 跳过任务栏与分页器。原生 Wayland 下无 libX11/DISPLAY，
调用会静默失败并返回 False。
"""

from __future__ import annotations

import ctypes
import ctypes.util

_NET_WM_STATE_ADD = 1
_SUBSTRUCTURE_REDIRECT_MASK = 1 << 20
_SUBSTRUCTURE_NOTIFY_MASK = 1 << 19


class _XClientMessageEvent(ctypes.Structure):
    """64 位布局的 XClientMessageEvent（与 XEvent 前段对齐）。"""

    _fields_ = [
        ("type", ctypes.c_int),
        ("serial", ctypes.c_ulong),
        ("send_event", ctypes.c_int),
        ("display", ctypes.c_void_p),
        ("window", ctypes.c_ulong),
        ("message_type", ctypes.c_ulong),
        ("format", ctypes.c_int),
        ("data_l", ctypes.c_long * 5),
    ]


class _XEvent(ctypes.Union):
    """XEvent 整体大小（96 字节），保证 XSendEvent 拷贝安全。"""

    _fields_ = [
        ("pad", ctypes.c_long * 24),  # 192 字节 ≥ XEvent 所需
        ("xclient", _XClientMessageEvent),
    ]


_lib_handle = None
_lib_tried = False


def _lib():
    global _lib_handle, _lib_tried
    if _lib_tried:
        return _lib_handle
    _lib_tried = True
    name = ctypes.util.find_library("X11")
    if not name:
        return None
    try:
        _lib_handle = ctypes.cdll.LoadLibrary(name)
    except OSError:
        _lib_handle = None
    return _lib_handle


def skip_taskbar(window_id: int) -> bool:
    """请求 WM 把窗口从任务栏/分页器隐藏（幂等，失败静默返回 False）。

    setWindowFlag 会重建原生窗口（XID 变化），因此每次 showEvent 都应重新调用。
    """
    x11 = _lib()
    if x11 is None:
        return False
    try:
        x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
        x11.XOpenDisplay.restype = ctypes.c_void_p
        x11.XInternAtom.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
        x11.XInternAtom.restype = ctypes.c_ulong
        x11.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
        x11.XDefaultRootWindow.restype = ctypes.c_ulong
        x11.XSendEvent.argtypes = [
            ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int,
            ctypes.c_long, ctypes.c_void_p,
        ]
        x11.XFlush.argtypes = [ctypes.c_void_p]
        x11.XFlush.restype = ctypes.c_int

        display = x11.XOpenDisplay(None)
        if not display:
            return False
        root = x11.XDefaultRootWindow(display)
        atom_net_wm_state = x11.XInternAtom(display, b"_NET_WM_STATE", 0)
        atom_skip_taskbar = x11.XInternAtom(display, b"_NET_WM_STATE_SKIP_TASKBAR", 0)
        atom_skip_pager = x11.XInternAtom(display, b"_NET_WM_STATE_SKIP_PAGER", 0)

        ev = _XEvent()
        xclient = ev.xclient
        xclient.type = 33  # ClientMessage
        xclient.window = window_id
        xclient.message_type = atom_net_wm_state
        xclient.format = 32
        xclient.data_l[0] = _NET_WM_STATE_ADD
        xclient.data_l[1] = atom_skip_taskbar
        xclient.data_l[2] = atom_skip_pager
        xclient.data_l[3] = 0
        xclient.data_l[4] = 0

        x11.XSendEvent(display, root, 0, _SUBSTRUCTURE_REDIRECT_MASK | _SUBSTRUCTURE_NOTIFY_MASK, ctypes.byref(ev))
        x11.XFlush(display)
        return True
    except Exception:
        return False
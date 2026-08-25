"""X11 窗口提示辅助 —— 任务栏跳过与置顶状态自检。

Qt 的 WindowDoesNotAcceptFocus 在部分构建下不会发出
_NET_WM_STATE_SKIP_TASKBAR；WindowStaysOnTopHint 也可能在
合成器重启、分辨率/DPI 变更、休眠唤醒后被静默丢弃。
这里通过标准 _NET_WM_STATE client message 直接向 WM 请求，
并提供读取当前状态的能力供 watchdog 使用。

原生 Wayland 下无 libX11/DISPLAY，所有调用静默失败并返回 False。
"""

from __future__ import annotations

import ctypes
import ctypes.util

_NET_WM_STATE_ADD = 1
_SUBSTRUCTURE_REDIRECT_MASK = 1 << 20
_SUBSTRUCTURE_NOTIFY_MASK = 1 << 19
_CLIENT_MESSAGE = 33
_XA_ATOM = 4


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
    """XEvent 整体大小，保证 XSendEvent 拷贝安全。"""

    _fields_ = [
        ("pad", ctypes.c_long * 24),  # 192 字节 ≥ XEvent 所需
        ("xclient", _XClientMessageEvent),
    ]


_lib_handle = None
_lib_tried = False
_signatures_set = False
_error_handler = None

# X 错误处理回调类型：int handler(Display*, XErrorEvent*)
_XErrorHandler = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p)


def _ignore_x_error(display, event) -> int:  # pragma: no cover - 由 Xlib 调用
    """忽略 BadWindow 等非致命错误。

    Xlib 默认处理器会在遇到 BadWindow 时打印并可能终止进程；
    查询已销毁/无效窗口（offscreen 平台的假 winId）时必须自己兜住。
    """
    return 0


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


def _prepare(x11) -> None:
    """声明 argtypes/restype。

    64 位下必须显式声明：ctypes 默认按 c_int 截断返回值，
    损坏的 Display*/Atom 会让 Xlib 段错误。
    """
    global _signatures_set
    if _signatures_set:
        return
    x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
    x11.XOpenDisplay.restype = ctypes.c_void_p
    x11.XCloseDisplay.argtypes = [ctypes.c_void_p]
    x11.XCloseDisplay.restype = ctypes.c_int
    x11.XInternAtom.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
    x11.XInternAtom.restype = ctypes.c_ulong
    x11.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
    x11.XDefaultRootWindow.restype = ctypes.c_ulong
    x11.XSendEvent.argtypes = [
        ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int,
        ctypes.c_long, ctypes.c_void_p,
    ]
    x11.XSendEvent.restype = ctypes.c_int
    x11.XFlush.argtypes = [ctypes.c_void_p]
    x11.XFlush.restype = ctypes.c_int
    x11.XGetWindowProperty.argtypes = [
        ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong,
        ctypes.c_long, ctypes.c_long, ctypes.c_int, ctypes.c_ulong,
        ctypes.POINTER(ctypes.c_ulong), ctypes.POINTER(ctypes.c_int),
        ctypes.POINTER(ctypes.c_ulong), ctypes.POINTER(ctypes.c_ulong),
        ctypes.POINTER(ctypes.POINTER(ctypes.c_ulong)),
    ]
    x11.XGetWindowProperty.restype = ctypes.c_int
    x11.XFree.argtypes = [ctypes.c_void_p]
    x11.XFree.restype = ctypes.c_int
    x11.XSetErrorHandler.argtypes = [_XErrorHandler]
    x11.XSetErrorHandler.restype = ctypes.c_void_p
    _install_error_handler(x11)
    _signatures_set = True


def _install_error_handler(x11) -> None:
    """安装一次性的 X 错误忽略处理器（回调必须保持强引用）。"""
    global _error_handler
    if _error_handler is not None:
        return
    _error_handler = _XErrorHandler(_ignore_x_error)
    try:
        x11.XSetErrorHandler(_error_handler)
    except Exception:
        _error_handler = None


def _send_state(window_id: int, atoms: tuple[bytes, ...]) -> bool:
    """向 WM 发送 _NET_WM_STATE 添加请求（最多两个原子，符合 EWMH）。"""
    x11 = _lib()
    if x11 is None or not atoms:
        return False
    display = None
    try:
        _prepare(x11)
        display = x11.XOpenDisplay(None)
        if not display:
            return False
        root = x11.XDefaultRootWindow(display)
        state_atom = x11.XInternAtom(display, b"_NET_WM_STATE", 0)
        values = [x11.XInternAtom(display, name, 0) for name in atoms[:2]]

        ev = _XEvent()
        xclient = ev.xclient
        xclient.type = _CLIENT_MESSAGE
        xclient.window = window_id
        xclient.message_type = state_atom
        xclient.format = 32
        xclient.data_l[0] = _NET_WM_STATE_ADD
        xclient.data_l[1] = values[0]
        xclient.data_l[2] = values[1] if len(values) > 1 else 0
        xclient.data_l[3] = 0
        xclient.data_l[4] = 0

        x11.XSendEvent(
            display, root, 0,
            _SUBSTRUCTURE_REDIRECT_MASK | _SUBSTRUCTURE_NOTIFY_MASK,
            ctypes.byref(ev),
        )
        x11.XFlush(display)
        return True
    except Exception:
        return False
    finally:
        if display:
            try:
                x11.XCloseDisplay(display)
            except Exception:
                pass


def skip_taskbar(window_id: int) -> bool:
    """请求 WM 把窗口从任务栏/分页器隐藏（幂等，失败静默返回 False）。

    setWindowFlag 会重建原生窗口（XID 变化），因此每次 showEvent 都应重新调用。
    """
    return _send_state(
        window_id,
        (b"_NET_WM_STATE_SKIP_TASKBAR", b"_NET_WM_STATE_SKIP_PAGER"),
    )


def set_above(window_id: int) -> bool:
    """请求 WM 把窗口置于其他窗口之上（_NET_WM_STATE_ABOVE）。"""
    return _send_state(window_id, (b"_NET_WM_STATE_ABOVE",))


def has_state(window_id: int, atom_name: bytes) -> bool | None:
    """查询窗口 _NET_WM_STATE 是否含指定原子。

    返回 True/False；无法查询（非 X11、libX11 缺失、属性读失败）返回 None，
    让调用方能区分「确认丢失」与「查不到」，避免在 Wayland 下反复瞎重设。
    """
    x11 = _lib()
    if x11 is None:
        return None
    display = None
    try:
        _prepare(x11)
        display = x11.XOpenDisplay(None)
        if not display:
            return None
        state_atom = x11.XInternAtom(display, b"_NET_WM_STATE", 0)
        target = x11.XInternAtom(display, atom_name, 0)

        actual_type = ctypes.c_ulong()
        actual_format = ctypes.c_int()
        nitems = ctypes.c_ulong()
        bytes_after = ctypes.c_ulong()
        data = ctypes.POINTER(ctypes.c_ulong)()

        status = x11.XGetWindowProperty(
            display, window_id, state_atom,
            0, 32, 0, _XA_ATOM,
            ctypes.byref(actual_type), ctypes.byref(actual_format),
            ctypes.byref(nitems), ctypes.byref(bytes_after),
            ctypes.byref(data),
        )
        if status != 0 or not data:
            return None
        try:
            found = any(data[i] == target for i in range(int(nitems.value)))
        finally:
            x11.XFree(data)
        return found
    except Exception:
        return None
    finally:
        if display:
            try:
                x11.XCloseDisplay(display)
            except Exception:
                pass


def is_above(window_id: int) -> bool | None:
    """窗口当前是否仍带 _NET_WM_STATE_ABOVE。"""
    return has_state(window_id, b"_NET_WM_STATE_ABOVE")

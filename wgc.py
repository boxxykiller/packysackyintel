"""Capture one EVE client's window with Windows Graphics Capture - the API OBS, Discord and the Windows
Snipping Tool use for window capture.

The picture comes from the Windows desktop compositor (DWM), which keeps a copy of every window's image, so
this still works when the client is covered by another window (e.g. another EVE client). It does not attach
to, open, read from, inject into or send anything to the game process: Windows hands over the same pixels
it would show on screen. A minimized window has nothing to show, so it can't be captured then."""
import asyncio
import concurrent.futures
import ctypes
import queue
import threading
import time
from ctypes import wintypes

from PIL import Image

from winrt.windows.graphics.capture import (Direct3D11CaptureFramePool, GraphicsCaptureAccess,
                                            GraphicsCaptureAccessKind)
from winrt.windows.graphics.capture.interop import create_for_window
from winrt.windows.graphics.directx import DirectXPixelFormat
from winrt.windows.graphics.directx.direct3d11.interop import create_direct3d11_device_from_dxgi_device
from winrt.windows.graphics.imaging import SoftwareBitmap
from winrt.windows.storage.streams import Buffer

_IID_IDXGIDevice = (ctypes.c_byte * 16)(*bytes.fromhex("fa77ec547713e6448c3288fd5f44c84c"))
_FORMAT = DirectXPixelFormat.B8_G8_R8_A8_UINT_NORMALIZED


def _com_call(ptr, index, restype, *args):
    """Call method #index of the COM object at ptr (raw vtable call)."""
    vtbl = ctypes.cast(ptr, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    fn = ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *[type(a) for a in args])(vtbl[index])
    return fn(ptr, *args)


def _d3d_device():
    """A WinRT IDirect3DDevice on the default GPU (needed by the capture frame pool)."""
    d3d11 = ctypes.windll.d3d11
    dev = ctypes.c_void_p()
    hr = d3d11.D3D11CreateDevice(None, 1, None, 0x20, None, 0, 7, ctypes.byref(dev), None, None)  # HARDWARE, BGRA
    if hr < 0 or not dev.value:
        hr = d3d11.D3D11CreateDevice(None, 5, None, 0x20, None, 0, 7, ctypes.byref(dev), None, None)  # WARP
    if hr < 0 or not dev.value:
        raise OSError(f"D3D11CreateDevice failed 0x{hr & 0xFFFFFFFF:08X}")
    dxgi = ctypes.c_void_p()
    try:
        hr = _com_call(dev.value, 0, ctypes.c_long, ctypes.cast(_IID_IDXGIDevice, ctypes.c_void_p),
                       ctypes.cast(ctypes.byref(dxgi), ctypes.c_void_p))      # QueryInterface
        if hr < 0:
            raise OSError(f"QueryInterface(IDXGIDevice) failed 0x{hr & 0xFFFFFFFF:08X}")
        return create_direct3d11_device_from_dxgi_device(dxgi.value)
    finally:
        if dxgi.value:
            _com_call(dxgi.value, 2, ctypes.c_ulong)                        # Release
        _com_call(dev.value, 2, ctypes.c_ulong)


def frame_origin(hwnd):
    """Screen position of the captured image's top-left: the window's visible bounds (without the invisible
    resize border that GetWindowRect includes)."""
    r = wintypes.RECT()
    if ctypes.windll.dwmapi.DwmGetWindowAttribute(wintypes.HWND(hwnd), 9, ctypes.byref(r), ctypes.sizeof(r)) != 0:
        ctypes.windll.user32.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(r))   # DWMWA_EXTENDED_FRAME_BOUNDS
    return r.left, r.top


# All capture work runs on one worker thread with its own event loop. Waiting on WinRT async calls from the
# Tk (UI) thread deadlocks, because Windows routes their completion back through the blocked UI thread.
_jobs = queue.Queue()
_loop = None


def _worker():
    global _loop
    _loop = asyncio.new_event_loop()
    while True:
        fn, fut = _jobs.get()
        try:
            fut.set_result(fn())
        except Exception as ex:
            fut.set_exception(ex)


threading.Thread(target=_worker, daemon=True, name="window-capture").start()


def _run(fn):
    """Run fn on the capture thread and return its result (callable from any thread)."""
    if threading.current_thread().name == "window-capture":
        return fn()
    fut = concurrent.futures.Future()
    _jobs.put((fn, fut))
    return fut.result()


_borderless_asked = False


class WindowCapture:
    """Live capture of one window. grab() returns the newest frame as a PIL image plus its screen origin."""

    def __init__(self, hwnd):
        self.hwnd = hwnd
        self.lock = threading.Lock()
        _run(self._start)

    def _start(self):
        global _borderless_asked
        hwnd = self.hwnd
        if not _borderless_asked:                         # Windows 11: no yellow "being captured" border
            _borderless_asked = True
            try:
                _loop.run_until_complete(GraphicsCaptureAccess.request_access_async(
                    GraphicsCaptureAccessKind.BORDERLESS))
            except Exception:
                pass
        self.device = _d3d_device()
        self.item = create_for_window(hwnd)
        self.size = self.item.size
        self.pool = Direct3D11CaptureFramePool.create_free_threaded(self.device, _FORMAT, 2, self.size)
        self.session = self.pool.create_capture_session(self.item)
        for attr in ("is_cursor_capture_enabled", "is_border_required"):
            try:
                setattr(self.session, attr, False)
            except Exception:
                pass
        self.session.start_capture()
        self.image = None
        self.stamp = 0.0

    def _latest(self):
        frame, f = None, self.pool.try_get_next_frame()
        while f is not None:                              # drain the queue, keep only the newest
            if frame is not None:
                frame.close()
            frame, f = f, self.pool.try_get_next_frame()
        return frame

    def grab(self, timeout=1.0):
        """(PIL RGB image of the whole window, (screen x, screen y) of its top-left). Raises if no frame."""
        with self.lock:
            return _run(lambda: self._grab(timeout))

    def _grab(self, timeout):
        end = time.time() + timeout
        frame = self._latest()
        while frame is None and self.image is None and time.time() < end:
            time.sleep(0.02)                          # first frame after start
            frame = self._latest()
        if frame is not None:
            try:
                cs = frame.content_size
                if (cs.width, cs.height) != (self.size.width, self.size.height):
                    self.size = cs                    # window resized: new buffers for the next frames
                    self.pool.recreate(self.device, _FORMAT, 2, cs)
                bmp = _loop.run_until_complete(SoftwareBitmap.create_copy_from_surface_async(frame.surface))
            finally:
                frame.close()
            w, h = bmp.pixel_width, bmp.pixel_height
            buf = Buffer(w * h * 4)
            bmp.copy_to_buffer(buf)
            bmp.close()
            self.image = Image.frombuffer("RGB", (w, h), bytes(buf), "raw", "BGRX", 0, 1)
            self.stamp = time.time()
        if self.image is None:
            raise OSError("no frame from window capture")
        return self.image, frame_origin(self.hwnd)

    def close(self):
        _run(self._close)

    def _close(self):
        for o in (self.session, self.pool):
            try:
                o.close()
            except Exception:
                pass


_captures = {}
_captures_lock = threading.Lock()


def capture_for(hwnd):
    """Shared WindowCapture for hwnd (one capture session per window, reused across calls)."""
    with _captures_lock:
        cap = _captures.get(hwnd)
        if cap is None:
            cap = _captures[hwnd] = WindowCapture(hwnd)
        return cap


def release(keep=None):
    """Stop capturing every window except keep."""
    with _captures_lock:
        for h in [h for h in _captures if h != keep]:
            _captures.pop(h).close()


def supported():
    try:
        from winrt.windows.graphics.capture import GraphicsCaptureSession
        return bool(GraphicsCaptureSession.is_supported())
    except Exception:
        return False

"""``connector.capture_target``: which window Pitwall should grab for a capture.

Add to ``recorder.py``, in ``__init__`` before ``self.client.start()``::

    self.client.method("connector.capture_target", lambda ctx, p: capture_target(game_pid))

Pitwall takes the picture itself; the connector only names the window (docs/connector-protocol.md
section 4). Pitwall asks before every capture with a 1 s deadline, so answer from a thread that
is not the sampling loop. Three answers:

- a window: ``{hwnd, pid, title?, crop?}``;
- ``game_not_running``: no window of the game's process; Pitwall refuses the capture;
- ``window_minimized``: the window exists but cannot be grabbed; the message tells the user how
  to fix it. Any other reason: ``not_capturable`` (Pitwall then grabs the foreground window).
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from typing import Any

from stintmate_ipc import RemoteError

user32 = ctypes.windll.user32
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.IsIconic.argtypes = [wintypes.HWND]
ENUM_PROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


def windows_of(pid: int) -> list[int]:
    """The visible top-level windows of a process."""
    found: list[int] = []

    def visit(hwnd: int, _lparam: int) -> bool:
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid and user32.IsWindowVisible(hwnd):
            found.append(hwnd)
        return True

    user32.EnumWindows(ENUM_PROC(visit), 0)
    return found


def capture_target(pid: int | None) -> dict[str, Any]:
    """``pid``: the game's process id, from the game check (``game.py``), or None."""
    windows = windows_of(pid) if pid else []
    if not windows:
        raise RemoteError("game_not_running", "the game has no window")
    hwnd = windows[0]
    if user32.IsIconic(hwnd):
        raise RemoteError("window_minimized", "The game window is minimized: restore it. "
                          "It does not need focus.")
    title = ctypes.create_unicode_buffer(256)
    user32.GetWindowTextW(hwnd, title, len(title))
    # `crop` ({x, y, w, h}, pixels of the client area) keeps part of the window: leave it out
    # to keep the whole client area.
    return {"hwnd": int(hwnd), "pid": pid, "title": title.value}

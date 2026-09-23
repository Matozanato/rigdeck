"""vJoy output through ctypes.

The game only ever sees a plain joystick, so every function that can be bound in
Options -> Controls can be driven from the panel -- including the ones that have no
default key and are therefore unreachable by keystroke emulation.
"""

from __future__ import annotations

import ctypes
import threading
import time
from pathlib import Path

from . import vjoydev

DLL_CANDIDATES = [
    Path(r"C:\Program Files\vJoy\x64\vJoyInterface.dll"),
    Path(r"C:\Program Files\vJoy\x86\vJoyInterface.dll"),
    Path(r"C:\Program Files (x86)\vJoy\x64\vJoyInterface.dll"),
]

STATUS_OWN, STATUS_FREE, STATUS_BUSY, STATUS_MISS, STATUS_UNKNOWN = range(5)
STATUS_TEXT = {
    STATUS_OWN: "owned by us",
    STATUS_FREE: "free",
    STATUS_BUSY: "in use by another program",
    STATUS_MISS: "device not configured",
    STATUS_UNKNOWN: "unknown",
}


class VJoy:
    def __init__(self, device: int = 1, auto_device: bool = True) -> None:
        self.device = device
        self.auto_device = auto_device
        self.dll = None
        self.available = False
        self.reason = "not initialised"
        self.device_note = ""
        self._switched_on = False
        self._lock = threading.Lock()
        self._held: set[int] = set()

    def start(self) -> bool:
        # The vJoy device is kept switched off while nothing needs it, because its driver
        # bugchecks the machine now and then when something enumerates it (see
        # vjoydev.py).  Switch it on first; if that fails we still try, in case it was
        # left on by hand.
        if self.auto_device:
            on, why = vjoydev.enable()
            self.device_note = why
            self._switched_on = on and why == "switched on"

        dll_path = next((p for p in DLL_CANDIDATES if p.is_file()), None)
        if dll_path is None:
            self.reason = "vJoy is not installed"
            return False
        try:
            self.dll = ctypes.WinDLL(str(dll_path))
        except OSError as exc:
            self.reason = f"could not load vJoyInterface.dll ({exc})"
            return False

        self.dll.SetBtn.argtypes = [ctypes.c_int, ctypes.c_uint, ctypes.c_ubyte]
        self.dll.SetBtn.restype = ctypes.c_int
        self.dll.GetVJDStatus.argtypes = [ctypes.c_uint]
        self.dll.GetVJDStatus.restype = ctypes.c_int
        self.dll.AcquireVJD.argtypes = [ctypes.c_uint]
        self.dll.AcquireVJD.restype = ctypes.c_int
        self.dll.RelinquishVJD.argtypes = [ctypes.c_uint]
        self.dll.ResetVJD.argtypes = [ctypes.c_uint]

        # A device that has just been switched on is not ready the instant Windows says
        # it exists: the driver still has to finish starting, and until it does
        # vJoyEnabled() and GetVJDStatus() answer as if there were no device at all.
        # So the first few seconds are retried rather than believed.
        deadline = time.monotonic() + (6.0 if self._switched_on else 0.0)
        while True:
            if not self.dll.vJoyEnabled():
                self.reason = "the vJoy driver is installed but disabled"
            else:
                status = self.dll.GetVJDStatus(self.device)
                if status not in (STATUS_OWN, STATUS_FREE):
                    self.reason = (f"vJoy device {self.device} is "
                                   f"{STATUS_TEXT.get(status, 'unavailable')}")
                elif not self.dll.AcquireVJD(self.device):
                    self.reason = f"could not acquire vJoy device {self.device}"
                else:
                    break
            if time.monotonic() >= deadline:
                self._release_device()
                return False
            time.sleep(0.3)

        self.dll.ResetVJD(self.device)
        self.available = True
        self.reason = "ready"
        return True

    def stop(self) -> None:
        if self.available and self.dll is not None:
            for button in list(self._held):
                self.set_button(button, False)
            self.dll.RelinquishVJD(self.device)
        self.available = False
        self._release_device()

    def _release_device(self) -> None:
        """Switch the device back off, but only if we were the ones who switched it on.

        Leaving someone else's device off would be rude, and leaving our own on is what
        this whole arrangement exists to avoid.
        """
        if not self._switched_on:
            return
        self._switched_on = False
        ok, why = vjoydev.disable()
        self.device_note = why if ok else "could not switch the device off: " + why

    def set_button(self, button: int, pressed: bool) -> bool:
        if not self.available or button < 1 or button > 128:
            return False
        with self._lock:
            ok = bool(self.dll.SetBtn(1 if pressed else 0, self.device, button))
            if pressed:
                self._held.add(button)
            else:
                self._held.discard(button)
            return ok

    def pulse(self, button: int, ms: int = 70) -> bool:
        """Press and release, the joystick equivalent of tapping a key."""
        if not self.set_button(button, True):
            return False
        time.sleep(ms / 1000.0)
        self.set_button(button, False)
        return True

    def status(self) -> dict:
        return {"available": self.available, "device": self.device,
                "reason": self.reason, "device_note": self.device_note}

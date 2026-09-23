"""Switching the vJoy device on for as long as the panel needs it, and off after.

Why it is off the rest of the time is in packaging/vjoydevice.ps1: the driver reads two
characters past the end of its own device name, and roughly once every few days that
read crosses a page boundary and takes the machine down with a bugcheck in
vjoy.sys+0x6093.  A device nothing enumerates cannot do that.

Switching a PnP device needs administrator rights and the panel has none, so setup
registers two elevated scheduled tasks and this module only triggers them.  A task run
that way does not raise a UAC prompt, which is the whole reason for the detour.

Everything here fails soft.  If the tasks are missing -- an install from before this
existed, or a machine where setup was never run as administrator -- the panel carries on
with whatever state the device is already in, exactly as it did before.
"""

from __future__ import annotations

import subprocess
import time

TASK_ON = "RigDeckVJoyOn"
TASK_OFF = "RigDeckVJoyOff"

_NOWINDOW = 0x08000000  # CREATE_NO_WINDOW: the tray app has no console to print into


def _run(args: list[str]) -> tuple[int, str]:
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=30,
                           creationflags=_NOWINDOW)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except (OSError, subprocess.SubprocessError) as exc:
        return -1, str(exc)


def task_exists(name: str) -> bool:
    return _run(["schtasks", "/query", "/tn", name])[0] == 0


def device_is_on() -> bool:
    """True when the vJoy root device reports Status OK.

    Read-only, so it needs no elevation and no task.
    """
    code, out = _run([
        "powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command",
        "(Get-PnpDevice -InstanceId 'ROOT\\HIDCLASS\\0000' "
        "-ErrorAction SilentlyContinue).Status"])
    return code == 0 and "OK" in out


def enable(timeout: float = 8.0) -> tuple[bool, str]:
    """Switch the device on and wait until Windows says it is there.

    Returns (on, why).  `on` True means a caller may go ahead and acquire the device.
    """
    if device_is_on():
        return True, "already on"
    if not task_exists(TASK_ON):
        return False, ("the %s task is missing -- run the installer as administrator "
                       "to add it, or switch the device on yourself" % TASK_ON)
    code, out = _run(["schtasks", "/run", "/tn", TASK_ON])
    if code != 0:
        return False, "could not start %s: %s" % (TASK_ON, out.strip())
    # schtasks returns as soon as the task is queued, so the wait is ours.  The device
    # reappearing is the signal; the driver then needs a breath before AcquireVJD works,
    # which VJoy.start() retries around.
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if device_is_on():
            return True, "switched on"
        time.sleep(0.4)
    return False, "the device did not come back within %.0f s" % timeout


def disable() -> tuple[bool, str]:
    """Switch the device off again.  Never raises: this runs in shutdown paths."""
    if not device_is_on():
        return True, "already off"
    if not task_exists(TASK_OFF):
        return False, "the %s task is missing -- the device stays on" % TASK_OFF
    code, out = _run(["schtasks", "/run", "/tn", TASK_OFF])
    if code != 0:
        return False, "could not start %s: %s" % (TASK_OFF, out.strip())
    deadline = time.monotonic() + 6.0
    while time.monotonic() < deadline:
        if not device_is_on():
            return True, "switched off"
        time.sleep(0.3)
    return False, "the device was still there after 6 s"

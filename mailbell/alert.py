"""Plays a sound and shows a desktop notification on macOS, Windows and Linux."""

from __future__ import annotations

import platform
import shutil
import subprocess
import sys


def play_sound(path: str = "") -> None:
    system = platform.system()
    try:
        if system == "Darwin":
            subprocess.Popen(["afplay", path or "/System/Library/Sounds/Glass.aiff"])
            return
        if system == "Windows":
            import winsound

            if path:
                winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC)
            else:
                winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
            return
        # Linux and others
        default = "/usr/share/sounds/freedesktop/stereo/message-new-instant.oga"
        for player in ("paplay", "pw-play", "aplay"):
            if shutil.which(player):
                subprocess.Popen([player, path or default], stderr=subprocess.DEVNULL)
                return
    except Exception:
        pass
    sys.stdout.write("\a")  # last resort: terminal bell
    sys.stdout.flush()


def desktop_notification(title: str, message: str) -> None:
    system = platform.system()
    try:
        if system == "Darwin":
            script = f"display notification {_as_applescript(message)} with title {_as_applescript(title)}"
            subprocess.Popen(["osascript", "-e", script])
        elif system == "Windows":
            ps = (
                "[void][System.Reflection.Assembly]::LoadWithPartialName('System.Windows.Forms');"
                "$n=New-Object System.Windows.Forms.NotifyIcon;"
                "$n.Icon=[System.Drawing.SystemIcons]::Information;$n.Visible=$true;"
                f"$n.ShowBalloonTip(8000,{_as_powershell(title)},{_as_powershell(message)},'Info');"
                "Start-Sleep -s 9;$n.Dispose()"
            )
            subprocess.Popen(["powershell", "-NoProfile", "-Command", ps])
        elif shutil.which("notify-send"):
            subprocess.Popen(["notify-send", title, message])
    except Exception:
        pass


def _as_applescript(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _as_powershell(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"

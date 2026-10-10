"""Which volume a folder is on, and which volumes are mounted now (SPEC §6.5, §7). TR-ING-05.

An input root may be a removable drive whose letter (Windows) or mount path (macOS) changes between
connections. Each approved root therefore stores the identity of its volume: the volume serial number on
Windows, the volume UUID on macOS (``diskutil``, local only), the filesystem UUID on Linux (development and
CI). At job start the root is found again by that identity. Nothing is ever written to the drive.
"""

from __future__ import annotations

import os
import plistlib
import subprocess  # nosec B404 - fixed local command (diskutil), no shell, no user input
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class Volume:
    mount: Path
    volume_id: str  # "" when the OS gives no stable identity
    label: str = ""
    removable: bool = False


class VolumeProbe(Protocol):
    def volume_of(self, path: Path) -> Volume | None: ...

    def mounted(self) -> list[Volume]: ...


def _mount_point(path: Path) -> Path:
    p = path.resolve()
    while not os.path.ismount(p) and p.parent != p:
        p = p.parent
    return p


class _Windows:
    def __init__(self) -> None:
        import ctypes

        self._ct = ctypes
        self._k32 = getattr(ctypes, "windll").kernel32  # noqa: B009 - Windows only; same typing on every OS

    def _info(self, root: str) -> Volume | None:
        ct = self._ct
        label, fs = ct.create_unicode_buffer(261), ct.create_unicode_buffer(261)
        serial, maxlen, flags = ct.c_uint32(), ct.c_uint32(), ct.c_uint32()
        old = ct.c_uint32()
        self._k32.SetThreadErrorMode(1, ct.byref(old))  # SEM_FAILCRITICALERRORS: no "insert disk" dialog
        try:
            ok = self._k32.GetVolumeInformationW(
                ct.c_wchar_p(root), label, 261, ct.byref(serial), ct.byref(maxlen), ct.byref(flags), fs, 261
            )
        finally:
            self._k32.SetThreadErrorMode(old.value, None)
        if not ok:
            return None
        removable = self._k32.GetDriveTypeW(ct.c_wchar_p(root)) == 2  # DRIVE_REMOVABLE
        return Volume(Path(root), f"win:{serial.value:08X}", label.value, removable)

    def volume_of(self, path: Path) -> Volume | None:
        buf = self._ct.create_unicode_buffer(1024)
        if not self._k32.GetVolumePathNameW(self._ct.c_wchar_p(str(path)), buf, 1024):
            return None
        return self._info(buf.value)

    def mounted(self) -> list[Volume]:
        mask = self._k32.GetLogicalDrives()
        roots = [f"{chr(65 + i)}:\\" for i in range(26) if mask >> i & 1]
        return [v for v in (self._info(r) for r in roots) if v is not None]


class _MacOS:
    def _info(self, mount: Path) -> Volume | None:
        try:
            out = subprocess.run(  # noqa: S603  # nosec B603 B607 - fixed argument list, local tool
                ["/usr/sbin/diskutil", "info", "-plist", str(mount)],
                capture_output=True,
                timeout=15,
                check=False,
            )
            info = plistlib.loads(out.stdout) if out.returncode == 0 else {}
        except (OSError, subprocess.SubprocessError, plistlib.InvalidFileException):
            info = {}
        uuid = str(info.get("VolumeUUID") or info.get("DiskUUID") or "")
        removable = bool(
            info.get("Removable") or info.get("RemovableMedia") or not info.get("Internal", True)
        )
        return Volume(mount, f"mac:{uuid}" if uuid else "", str(info.get("VolumeName") or ""), removable)

    def volume_of(self, path: Path) -> Volume | None:
        return self._info(_mount_point(path))

    def mounted(self) -> list[Volume]:
        mounts = [Path("/")]
        vols = Path("/Volumes")
        if vols.is_dir():
            mounts += [p for p in sorted(vols.iterdir()) if p.is_dir() and os.path.ismount(p)]
        return [v for v in (self._info(m) for m in mounts) if v is not None]


class _Linux:
    def _uuid_of_device(self, dev: int) -> str:
        by_uuid = Path("/dev/disk/by-uuid")
        if by_uuid.is_dir():
            for link in by_uuid.iterdir():
                try:
                    if getattr(os.stat(link), "st_rdev", None) == dev:
                        return f"uuid:{link.name}"
                except OSError:
                    continue
        return f"dev:{dev}"

    def volume_of(self, path: Path) -> Volume | None:
        mount = _mount_point(path)
        try:
            dev = os.stat(mount).st_dev
        except OSError:
            return None
        return Volume(mount, self._uuid_of_device(dev), mount.name)

    def mounted(self) -> list[Volume]:
        out: list[Volume] = []
        try:
            lines = Path("/proc/self/mounts").read_text(encoding="utf-8").splitlines()
        except OSError:
            lines = []
        for line in lines:
            parts = line.split()
            if len(parts) > 1 and parts[0].startswith("/dev/"):
                vol = self.volume_of(Path(parts[1].replace("\\040", " ")))
                if vol is not None:
                    out.append(vol)
        return out


def system_probe() -> VolumeProbe:
    if sys.platform == "win32":
        return _Windows()
    if sys.platform == "darwin":
        return _MacOS()
    return _Linux()

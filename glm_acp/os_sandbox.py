"""Optional OS-enforced command isolation with fail-closed required mode."""

from __future__ import annotations

import ctypes
import os
import shutil
import sys
from pathlib import Path
from typing import Any

SANDBOX_MODE_ENV = "GLM_ACP_OS_SANDBOX"
SANDBOX_NETWORK_ENV = "GLM_ACP_SANDBOX_NETWORK"


def sandbox_mode() -> str:
    value = os.environ.get(SANDBOX_MODE_ENV, "off").strip().lower()
    return value if value in {"off", "auto", "required"} else "required"


def sandbox_network_enabled() -> bool:
    return os.environ.get(SANDBOX_NETWORK_ENV, "1").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }


def _seatbelt_quote(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _macos_profile(roots: list[Path], network: bool) -> str:
    """Build a deny-by-default Seatbelt profile with only workspace writes."""
    readable = [
        "/System",
        "/usr",
        "/bin",
        "/sbin",
        "/Library",
        "/Applications/Xcode.app",
        "/opt",
        "/private/etc",
        "/dev",
    ]
    rules = [
        "(version 1)",
        "(deny default)",
        "(allow process*)",
        "(allow sysctl-read)",
        "(allow mach-lookup)",
        "(allow file-read-metadata)",
    ]
    for path in readable:
        if Path(path).exists():
            rules.append(f'(allow file-read* (subpath "{_seatbelt_quote(path)}"))')
    for root in roots:
        quoted = _seatbelt_quote(str(root.resolve()))
        rules.append(f'(allow file-read* (subpath "{quoted}"))')
        rules.append(f'(allow file-write* (subpath "{quoted}"))')
    for temporary in ("/tmp", "/private/tmp"):
        rules.append(f'(allow file-read* file-write* (subpath "{temporary}"))')
    rules.append("(allow network*)" if network else "(deny network*)")
    return " ".join(rules)


def command_prefix(
    roots: list[Path],
    mode_override: str | None = None,
    network_override: bool | None = None,
) -> tuple[list[str], str]:
    """Return an argv prefix ending before the shell command and backend status."""
    mode = mode_override or sandbox_mode()
    if mode not in {"off", "auto", "required"}:
        mode = "required"
    if mode == "off":
        return [], "disabled"
    if sys.platform.startswith("linux"):
        bwrap = shutil.which("bwrap")
        if bwrap:
            argv = [
                bwrap,
                "--die-with-parent",
                "--new-session",
                "--unshare-pid",
                "--proc",
                "/proc",
                "--dev-bind",
                "/dev",
                "/dev",
                "--tmpfs",
                "/tmp",
            ]
            for system_path in ("/usr", "/bin", "/lib", "/lib64", "/etc"):
                if Path(system_path).exists():
                    argv.extend(["--ro-bind", system_path, system_path])
            network = sandbox_network_enabled() if network_override is None else network_override
            if not network:
                argv.append("--unshare-net")
            for root in roots:
                parents = list(root.parents)[:-1]
                for parent in reversed(parents):
                    if str(parent) not in {"/usr", "/bin", "/lib", "/lib64", "/etc"}:
                        argv.extend(["--dir", str(parent)])
                argv.extend(["--bind", str(root), str(root)])
            argv.extend(
                [
                    "--dir",
                    "/tmp/glm-acp-home",
                    "--setenv",
                    "HOME",
                    "/tmp/glm-acp-home",
                    "--chdir",
                    str(roots[0]),
                    "--",
                ]
            )
            return argv, "bubblewrap"
    if sys.platform == "darwin":
        sandbox_exec = shutil.which("sandbox-exec")
        if sandbox_exec:
            network = sandbox_network_enabled() if network_override is None else network_override
            return [sandbox_exec, "-p", _macos_profile(roots, network)], "macos-seatbelt"
    if sys.platform == "win32":
        network = sandbox_network_enabled() if network_override is None else network_override
        if mode == "required":
            detail = "network isolation" if not network else "filesystem isolation"
            raise RuntimeError(
                f"OS sandboxing is required, but Windows Job Objects do not provide {detail}"
            )
        return [], "windows-job"
    if mode == "required":
        raise RuntimeError("OS sandboxing is required but no supported backend is available")
    return [], "workspace-only"


class WindowsJob:
    """Best-effort Windows process-tree containment; not a filesystem sandbox."""

    def __init__(self) -> None:
        if sys.platform != "win32":
            raise RuntimeError("Windows Job Objects are only available on Windows")
        kernel32: Any = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = kernel32.CreateJobObjectW(None, None)
        if not handle:
            raise OSError(ctypes.get_last_error(), "CreateJobObjectW failed")

        class IoCounters(ctypes.Structure):
            _fields_ = [
                (name, ctypes.c_uint64)
                for name in (
                    "ReadOperationCount",
                    "WriteOperationCount",
                    "OtherOperationCount",
                    "ReadTransferCount",
                    "WriteTransferCount",
                    "OtherTransferCount",
                )
            ]

        class BasicLimits(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_int64),
                ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", ctypes.c_uint32),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", ctypes.c_uint32),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", ctypes.c_uint32),
                ("SchedulingClass", ctypes.c_uint32),
            ]

        class ExtendedLimits(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", BasicLimits),
                ("IoInfo", IoCounters),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        limits = ExtendedLimits()
        limits.BasicLimitInformation.LimitFlags = 0x00002000  # KILL_ON_JOB_CLOSE
        if not kernel32.SetInformationJobObject(
            handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)
        ):
            error = ctypes.get_last_error()
            kernel32.CloseHandle(handle)
            raise OSError(error, "SetInformationJobObject failed")
        self._kernel32 = kernel32
        self._handle = handle

    def assign(self, pid: int) -> None:
        process = self._kernel32.OpenProcess(0x0100 | 0x0001, False, pid)
        if not process:
            raise OSError(ctypes.get_last_error(), "OpenProcess failed")
        try:
            if not self._kernel32.AssignProcessToJobObject(self._handle, process):
                raise OSError(ctypes.get_last_error(), "AssignProcessToJobObject failed")
        finally:
            self._kernel32.CloseHandle(process)

    def close(self) -> None:
        if self._handle:
            self._kernel32.CloseHandle(self._handle)
            self._handle = None

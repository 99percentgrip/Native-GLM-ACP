"""Optional OS-enforced command isolation with fail-closed required mode."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

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
    if mode == "required":
        raise RuntimeError("OS sandboxing is required but no supported backend is available")
    return [], "workspace-only"

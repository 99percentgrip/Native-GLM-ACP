"""Tests for the installed command and terminal authentication setup."""

from __future__ import annotations

import pytest

from glm_acp import __version__
from glm_acp.cli import configure_credentials, main
from glm_acp.config import CONFIG_DIR_ENV, load_stored_api_key
from glm_acp.uninstall import UninstallResult


def test_version_has_single_source(capsys):
    try:
        main(["--version"])
    except SystemExit as error:
        assert error.code == 0
    assert capsys.readouterr().out.strip() == __version__


def test_setup_stores_prompted_key_without_printing_it(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv(CONFIG_DIR_ENV, str(tmp_path))
    monkeypatch.delenv("ZAI_API_KEY", raising=False)
    monkeypatch.delenv("Z_AI_API_KEY", raising=False)

    assert configure_credentials(lambda _: "top-secret-value") == 0

    output = capsys.readouterr().out
    assert "top-secret-value" not in output
    assert "Credentials saved" in output
    assert load_stored_api_key() == "top-secret-value"


def test_check_auth_reports_only_status(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv(CONFIG_DIR_ENV, str(tmp_path))
    monkeypatch.setenv("ZAI_API_KEY", "top-secret-value")

    assert main(["--check-auth"]) == 0

    output = capsys.readouterr().out
    assert "configured" in output
    assert "top-secret-value" not in output


def test_uninstall_reports_removed_state(monkeypatch, tmp_path, capsys):
    settings = tmp_path / "settings.json"
    backup = tmp_path / "settings.json.backup"
    monkeypatch.setattr(
        "glm_acp.cli.uninstall_release",
        lambda *, purge: UninstallResult(
            commands=(tmp_path / "glm-acp", tmp_path / "native-glm-acp"),
            scheduled=False,
            profile_updated=True,
            zed_settings=settings,
            zed_backup=backup,
            credentials_removed=False,
        ),
    )

    assert main(["--uninstall"]) == 0

    output = capsys.readouterr().out
    assert "commands removed" in output
    assert str(settings) in output
    assert "credentials were preserved" in output
    assert "Restart Zed" in output


def test_purge_requires_uninstall():
    with pytest.raises(SystemExit) as error:
        main(["--purge"])
    assert error.value.code == 2


def test_cli_actions_are_mutually_exclusive():
    with pytest.raises(SystemExit) as error:
        main(["--setup", "--uninstall"])
    assert error.value.code == 2


def test_cli_module_does_not_eagerly_import_heavy_dependencies():
    """Importing ``glm_acp.cli`` must stay cheap.

    Heavy chains (``acp``/``acp.schema``, ``httpx``, ``croniter``,
    ``cryptography``, ``rich``, ``glm_acp.agent``, ``glm_acp.cron``,
    ``glm_acp.plugins``) are deferred to the code paths that actually need
    them so ``--version``, ``--setup``, ``--check-auth``, ``--uninstall``,
    and the ``cron``/``plugin``/``observe``/``harden``/``meta-*`` commands
    do not pay their startup cost. This guards against regressions that
    reintroduce top-level imports.
    """
    import subprocess
    import sys

    heavy = (
        "acp",
        "acp.schema",
        "httpx",
        "croniter",
        "cryptography",
        "rich.console",
        "glm_acp.agent",
        "glm_acp.cron",
        "glm_acp.plugins",
    )
    snippet = (
        "import sys\n"
        "import glm_acp.cli\n"
        f"heavy = {heavy!r}\n"
        "loaded = sorted(m for m in heavy if m in sys.modules)\n"
        "print(','.join(loaded))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", snippet],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    loaded = [name for name in result.stdout.strip().split(",") if name]
    assert loaded == [], f"glm_acp.cli eagerly imported heavy modules: {loaded}"

"""Flatpak companion cleanup in install.sh existing-install cleanup and uninstall.sh.

Both scripts share the same best-effort `cleanup_flatpak_companion` behavior:
stop the running companion, uninstall only the VRhotspot app ID from the user
scope (and best-effort from the system scope), and remove only the invoking
desktop user's companion app data and companion autostart entry.
"""

import os
from pathlib import Path
import shlex
import shutil
import stat
import subprocess
from typing import Optional

import pytest


ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "install.sh"
UNINSTALLER = ROOT / "uninstall.sh"
APP_ID = "io.github.josethevrtech.VRhotspot"

FAKE_FLATPAK = """#!/bin/sh
printf '%s\\n' "$*" >> "$FAKE_FLATPAK_LOG"
case "$1" in
    kill)
        exit "${FAKE_FLATPAK_KILL_STATUS:-0}"
        ;;
    info)
        if [ "$2" = "--user" ] && [ "${FAKE_FLATPAK_USER_INSTALLED:-0}" -eq 1 ]; then
            exit 0
        fi
        if [ "$2" = "--system" ] && [ "${FAKE_FLATPAK_SYSTEM_INSTALLED:-0}" -eq 1 ]; then
            exit 0
        fi
        exit 1
        ;;
    uninstall)
        exit "${FAKE_FLATPAK_UNINSTALL_STATUS:-0}"
        ;;
esac
exit 0
"""


def run_bash(
    script: str, *, env: Optional[dict[str, str]] = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["/bin/bash", "-c", script],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def make_fake_home(tmp_path: Path) -> Path:
    home = tmp_path / "home"
    owned_app_data = home / ".var" / "app" / APP_ID / "config"
    owned_app_data.mkdir(parents=True)
    (owned_app_data / "state.json").write_text("{}", encoding="utf-8")
    unrelated_app_data = home / ".var" / "app" / "org.unrelated.OtherApp"
    unrelated_app_data.mkdir(parents=True)
    (unrelated_app_data / "keep.txt").write_text("keep", encoding="utf-8")
    autostart = home / ".config" / "autostart"
    autostart.mkdir(parents=True)
    (autostart / f"{APP_ID}.desktop").write_text("[Desktop Entry]\n", encoding="utf-8")
    (autostart / "org.unrelated.OtherApp.desktop").write_text(
        "[Desktop Entry]\n", encoding="utf-8"
    )
    return home


def make_cleanup_path(tmp_path: Path, *, with_flatpak: bool = True) -> Path:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    for tool in ("id", "rm", "env"):
        real_tool = shutil.which(tool)
        assert real_tool, f"required tool missing on test host: {tool}"
        (fake_bin / tool).symlink_to(real_tool)
    if with_flatpak:
        stub = fake_bin / "flatpak"
        stub.write_text(FAKE_FLATPAK, encoding="utf-8")
        stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
    return fake_bin


def cleanup_env(tmp_path: Path, fake_bin: Path, **fake_vars: str) -> dict[str, str]:
    log = tmp_path / "flatpak.log"
    log.touch()
    env = os.environ.copy()
    env.update({"PATH": str(fake_bin), "FAKE_FLATPAK_LOG": str(log)})
    env.update(fake_vars)
    return env


def cleanup_script(script_path: Path, home: Path, overrides: str = "") -> str:
    return f"""
    source {shlex.quote(str(script_path))}
    resolve_flatpak_cleanup_user() {{
        FLATPAK_CLEANUP_USER="$(id -un)"
        FLATPAK_CLEANUP_UID="$(id -u)"
        FLATPAK_CLEANUP_HOME={shlex.quote(str(home))}
    }}
    {overrides}
    cleanup_flatpak_companion
    echo cleanup-done
    """


def flatpak_log_lines(env: dict[str, str]) -> list[str]:
    return Path(env["FAKE_FLATPAK_LOG"]).read_text(encoding="utf-8").splitlines()


@pytest.fixture(params=[INSTALLER, UNINSTALLER], ids=["install.sh", "uninstall.sh"])
def script_path(request):
    return request.param


def test_cleanup_kills_tray_before_user_uninstall_and_removes_only_owned_artifacts(
    tmp_path, script_path
):
    home = make_fake_home(tmp_path)
    fake_bin = make_cleanup_path(tmp_path)
    env = cleanup_env(tmp_path, fake_bin, FAKE_FLATPAK_USER_INSTALLED="1")

    result = run_bash(cleanup_script(script_path, home), env=env)

    assert result.returncode == 0, result.stderr
    assert "cleanup-done" in result.stdout

    log = flatpak_log_lines(env)
    kill_index = log.index(f"kill {APP_ID}")
    uninstall_index = log.index(f"uninstall --user -y {APP_ID}")
    assert kill_index < uninstall_index
    assert all(APP_ID in line for line in log if line.startswith("uninstall"))
    assert not any("--system" in line and line.startswith("uninstall") for line in log)

    assert not (home / ".var" / "app" / APP_ID).exists()
    assert (home / ".var" / "app" / "org.unrelated.OtherApp" / "keep.txt").exists()
    assert not (home / ".config" / "autostart" / f"{APP_ID}.desktop").exists()
    assert (home / ".config" / "autostart" / "org.unrelated.OtherApp.desktop").exists()


def test_cleanup_attempts_system_scope_best_effort_and_failures_are_nonfatal(
    tmp_path, script_path
):
    home = make_fake_home(tmp_path)
    fake_bin = make_cleanup_path(tmp_path)
    env = cleanup_env(
        tmp_path,
        fake_bin,
        FAKE_FLATPAK_USER_INSTALLED="1",
        FAKE_FLATPAK_SYSTEM_INSTALLED="1",
        FAKE_FLATPAK_UNINSTALL_STATUS="1",
    )

    result = run_bash(cleanup_script(script_path, home), env=env)

    assert result.returncode == 0, result.stderr
    assert "cleanup-done" in result.stdout
    assert "Could not remove the user-scoped Flatpak companion" in result.stdout
    assert "Could not remove the system-scoped Flatpak companion" in result.stdout

    log = flatpak_log_lines(env)
    assert f"uninstall --user -y {APP_ID}" in log
    assert f"uninstall --system -y {APP_ID}" in log
    # Owned local artifacts are still removed even when flatpak uninstall fails.
    assert not (home / ".var" / "app" / APP_ID).exists()
    assert not (home / ".config" / "autostart" / f"{APP_ID}.desktop").exists()


def test_missing_flatpak_is_nonfatal_and_still_removes_owned_user_artifacts(
    tmp_path, script_path
):
    home = make_fake_home(tmp_path)
    fake_bin = make_cleanup_path(tmp_path, with_flatpak=False)
    env = cleanup_env(tmp_path, fake_bin)

    result = run_bash(cleanup_script(script_path, home), env=env)

    assert result.returncode == 0, result.stderr
    assert "cleanup-done" in result.stdout
    assert "flatpak is not installed" in result.stdout
    assert flatpak_log_lines(env) == []
    assert not (home / ".var" / "app" / APP_ID).exists()
    assert not (home / ".config" / "autostart" / f"{APP_ID}.desktop").exists()
    assert (home / ".var" / "app" / "org.unrelated.OtherApp" / "keep.txt").exists()
    assert (home / ".config" / "autostart" / "org.unrelated.OtherApp.desktop").exists()


def test_missing_app_is_nonfatal_and_never_calls_flatpak_uninstall(
    tmp_path, script_path
):
    home = make_fake_home(tmp_path)
    fake_bin = make_cleanup_path(tmp_path)
    env = cleanup_env(tmp_path, fake_bin)

    result = run_bash(cleanup_script(script_path, home), env=env)

    assert result.returncode == 0, result.stderr
    assert "cleanup-done" in result.stdout
    assert not any(
        line.startswith("uninstall") for line in flatpak_log_lines(env)
    )


def test_unknown_desktop_user_is_nonfatal_and_skips_user_scope_only(
    tmp_path, script_path
):
    home = make_fake_home(tmp_path)
    fake_bin = make_cleanup_path(tmp_path)
    env = cleanup_env(
        tmp_path,
        fake_bin,
        FAKE_FLATPAK_USER_INSTALLED="1",
        FAKE_FLATPAK_SYSTEM_INSTALLED="1",
    )

    result = run_bash(
        cleanup_script(
            script_path,
            home,
            overrides="resolve_flatpak_cleanup_user() { return 1; }",
        ),
        env=env,
    )

    assert result.returncode == 0, result.stderr
    assert "cleanup-done" in result.stdout
    assert "skipping user-scoped Flatpak companion cleanup" in result.stdout

    log = flatpak_log_lines(env)
    assert not any("--user" in line for line in log)
    assert f"uninstall --system -y {APP_ID}" in log
    # Without a resolved desktop user, no home directory content is touched.
    assert (home / ".var" / "app" / APP_ID).exists()
    assert (home / ".config" / "autostart" / f"{APP_ID}.desktop").exists()


def test_already_stopped_tray_is_nonfatal_and_uninstall_proceeds(
    tmp_path, script_path
):
    home = make_fake_home(tmp_path)
    fake_bin = make_cleanup_path(tmp_path)
    env = cleanup_env(
        tmp_path,
        fake_bin,
        FAKE_FLATPAK_USER_INSTALLED="1",
        FAKE_FLATPAK_KILL_STATUS="1",
    )

    result = run_bash(cleanup_script(script_path, home), env=env)

    assert result.returncode == 0, result.stderr
    assert "cleanup-done" in result.stdout
    assert f"uninstall --user -y {APP_ID}" in flatpak_log_lines(env)
    assert not (home / ".var" / "app" / APP_ID).exists()


def test_installer_existing_install_cleanup_invokes_companion_cleanup(tmp_path):
    install_root = tmp_path / "install-root"
    install_root.mkdir()

    result = run_bash(
        f"""
        source {shlex.quote(str(INSTALLER))}
        INTERACTIVE=0
        INSTALL_ROOT={shlex.quote(str(install_root))}
        CONFIG_DIR={shlex.quote(str(tmp_path / "config-dir"))}
        function systemctl {{ :; }}
        pkill() {{ :; }}
        rm() {{ :; }}
        rollback_owned_firewall_rules() {{ echo firewall-rollback-called; }}
        cleanup_flatpak_companion() {{ echo companion-cleanup-called; }}
        cleanup_previous_install
        """
    )

    assert result.returncode == 0, result.stderr
    assert "companion-cleanup-called" in result.stdout
    assert "firewall-rollback-called" in result.stdout
    assert result.stdout.index("companion-cleanup-called") < result.stdout.index(
        "firewall-rollback-called"
    )
    assert "Cleanup complete" in result.stdout


def test_uninstaller_main_invokes_companion_cleanup_after_stopping_services():
    result = run_bash(
        f"""
        source {shlex.quote(str(UNINSTALLER))}
        check_root() {{ :; }}
        clear() {{ :; }}
        detect_os() {{ :; }}
        function systemctl {{ :; }}
        rm() {{ :; }}
        rollback_owned_firewall_rules() {{ :; }}
        cleanup_flatpak_companion() {{ echo companion-cleanup-called; }}
        main --yes
        """
    )

    assert result.returncode == 0, result.stderr
    assert "companion-cleanup-called" in result.stdout
    assert result.stdout.index("Services stopped and disabled") < result.stdout.index(
        "companion-cleanup-called"
    )
    assert result.stdout.index("companion-cleanup-called") < result.stdout.index(
        "successfully uninstalled"
    )

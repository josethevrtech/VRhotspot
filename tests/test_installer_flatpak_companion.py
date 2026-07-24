import json
import os
from pathlib import Path
import shlex
import stat
import subprocess
from typing import Optional

import pytest


ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "install.sh"
TOP_UNINSTALLER = ROOT / "uninstall.sh"
BACKEND_UNINSTALLER = ROOT / "backend" / "scripts" / "uninstall.sh"
FLATPAK_MANIFEST = (
    ROOT / "packaging" / "flatpak" / "io.github.josethevrtech.VRhotspot.json"
)


def make_executable(path: Path, contents: str = "#!/bin/sh\nexit 0\n") -> Path:
    path.write_text(contents, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


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


def selection_script(response: str, interactive: int = 1) -> str:
    return f"""
    source {shlex.quote(str(INSTALLER))}
    INTERACTIVE={interactive}
    FLATPAK_COMPANION_OPT_IN=0
    interactive_read() {{
        printf 'prompt=%s\\ndefault=%s\\n' "$3" n
        printf -v "$4" '%s' {shlex.quote(response)}
    }}
    configure_flatpak_companion_install
    check_flatpak_companion_prerequisites() {{
        echo companion-prerequisites-called
    }}
    build_and_install_flatpak_companion() {{
        echo companion-build-called
    }}
    install_flatpak_companion_if_requested
    echo "selected=$INSTALL_FLATPAK_COMPANION"
    """


def test_guided_default_no_does_not_attempt_flatpak_companion_install():
    result = run_bash(selection_script(""))

    assert result.returncode == 0, result.stderr
    assert "prompt=Install the Flatpak companion app? (y/N) " in result.stdout
    assert "default=n" in result.stdout
    assert "selected=n" in result.stdout
    assert "companion-prerequisites-called" not in result.stdout
    assert "companion-build-called" not in result.stdout


def test_guided_yes_attempts_flatpak_companion_install():
    result = run_bash(selection_script("yes"))

    assert result.returncode == 0, result.stderr
    assert "prompt=Install the Flatpak companion app? (y/N) " in result.stdout
    assert "selected=y" in result.stdout
    assert "companion-prerequisites-called" in result.stdout
    assert "companion-build-called" in result.stdout


def test_noninteractive_default_does_not_prompt_or_install_companion():
    result = run_bash(
        f"""
        source {shlex.quote(str(INSTALLER))}
        INTERACTIVE=0
        FLATPAK_COMPANION_OPT_IN=0
        prompt_yes_no() {{
            echo forbidden-prompt
            return 99
        }}
        configure_flatpak_companion_install
        check_flatpak_companion_prerequisites() {{
            echo forbidden-prerequisite-check
        }}
        install_flatpak_companion_if_requested
        echo "selected=$INSTALL_FLATPAK_COMPANION"
        """
    )

    assert result.returncode == 0, result.stderr
    assert "selected=n" in result.stdout
    assert "default: No" in result.stdout
    assert "forbidden-prompt" not in result.stdout
    assert "forbidden-prerequisite-check" not in result.stdout


def test_explicit_noninteractive_flag_opts_in_and_attempts_install():
    result = run_bash(
        f"""
        source {shlex.quote(str(INSTALLER))}
        INTERACTIVE=0
        FLATPAK_COMPANION_OPT_IN=1
        prompt_yes_no() {{
            echo forbidden-prompt
            return 99
        }}
        configure_flatpak_companion_install
        check_flatpak_companion_prerequisites() {{
            echo companion-prerequisites-called
        }}
        build_and_install_flatpak_companion() {{
            echo companion-build-called
        }}
        install_flatpak_companion_if_requested
        echo "selected=$INSTALL_FLATPAK_COMPANION"
        """
    )

    assert result.returncode == 0, result.stderr
    assert "selected=y" in result.stdout
    assert "explicitly requested" in result.stdout
    assert "companion-prerequisites-called" in result.stdout
    assert "companion-build-called" in result.stdout
    assert "forbidden-prompt" not in result.stdout


def test_main_parses_explicit_noninteractive_companion_opt_in():
    result = run_bash(
        f"""
        source {shlex.quote(str(INSTALLER))}
        check_root() {{ :; }}
        cleanup_previous_install() {{ :; }}
        detect_os() {{
            OS_ID=ubuntu
            OS_ID_LIKE=
            OS_NAME=Ubuntu
            PKG_MANAGER=apt
        }}
        install_dependencies() {{ :; }}
        get_source_files() {{ TEMP_INSTALL_DIR="$PWD"; }}
        validate_endeavouros_runtime_dependencies() {{ :; }}
        configure_install() {{ configure_flatpak_companion_install; }}
        install_daemon() {{ :; }}
        install_flatpak_companion_if_requested() {{
            echo "main-selected=$INSTALL_FLATPAK_COMPANION"
        }}
        show_completion() {{ :; }}
        main --non-interactive --install-flatpak-companion --no-clear
        """
    )

    assert result.returncode == 0, result.stderr
    assert "main-selected=y" in result.stdout
    assert "Flatpak companion install explicitly requested" in result.stdout


@pytest.mark.parametrize("missing_tool", ["flatpak", "flatpak-builder"])
def test_missing_flatpak_prerequisite_is_clear_and_nonfatal(tmp_path, missing_tool):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    available_tool = (
        "flatpak-builder" if missing_tool == "flatpak" else "flatpak"
    )
    make_executable(fake_bin / available_tool)
    env = os.environ.copy()
    env["PATH"] = str(fake_bin)

    result = run_bash(
        f"""
        source {shlex.quote(str(INSTALLER))}
        INSTALL_FLATPAK_COMPANION=y
        TEMP_INSTALL_DIR={shlex.quote(str(ROOT))}
        install_flatpak_companion_if_requested
        echo daemon-install-continues
        """,
        env=env,
    )

    assert result.returncode == 0, result.stderr
    assert f"missing required tool(s): {missing_tool}" in result.stdout
    assert "Continuing without the optional Flatpak companion app" in result.stdout
    assert "daemon-install-continues" in result.stdout


def test_flatpak_build_failure_is_bounded_nonfatal_and_cleans_temp_dir(tmp_path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    builder_args = tmp_path / "flatpak-builder.args"
    credential_state = tmp_path / "flatpak-builder.credentials"
    make_executable(fake_bin / "flatpak")
    make_executable(
        fake_bin / "flatpak-builder",
        """#!/bin/sh
printf '%s\n' "$@" > "$FAKE_FLATPAK_BUILDER_ARGS"
if [ "${VR_HOTSPOTD_API_TOKEN+x}" = x ] || [ "${API_TOKEN+x}" = x ]; then
    echo present > "$FAKE_FLATPAK_CREDENTIAL_STATE"
else
    echo absent > "$FAKE_FLATPAK_CREDENTIAL_STATE"
fi
i=1
while [ "$i" -le 40 ]; do
    echo "builder-line-$i"
    i=$((i + 1))
done
echo "deterministic fake builder failure"
exit 23
""",
    )
    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{fake_bin}:{env['PATH']}",
            "FAKE_FLATPAK_BUILDER_ARGS": str(builder_args),
            "FAKE_FLATPAK_CREDENTIAL_STATE": str(credential_state),
            "VR_HOTSPOTD_API_TOKEN": "must-not-reach-flatpak-builder",
            "API_TOKEN": "must-not-reach-flatpak-builder-either",
        }
    )

    result = run_bash(
        f"""
        source {shlex.quote(str(INSTALLER))}
        INSTALL_FLATPAK_COMPANION=y
        TEMP_INSTALL_DIR={shlex.quote(str(ROOT))}
        resolve_flatpak_companion_user() {{
            FLATPAK_COMPANION_USER="$(id -un)"
            FLATPAK_COMPANION_UID="$(id -u)"
            FLATPAK_COMPANION_GID="$(id -g)"
        }}
        install_flatpak_companion_if_requested
        echo daemon-install-continues
        """,
        env=env,
    )

    assert result.returncode == 0, result.stderr
    assert "failed (exit 23)" in result.stdout
    assert "deterministic fake builder failure" in result.stdout
    assert "Continuing without the optional Flatpak companion app" in result.stdout
    assert "daemon-install-continues" in result.stdout
    assert "must-not-reach-flatpak-builder" not in result.stdout
    output_lines = result.stdout.splitlines()
    assert "builder-line-1" not in output_lines
    assert "builder-line-21" not in output_lines
    assert "builder-line-22" in output_lines
    assert "builder-line-40" in output_lines
    assert credential_state.read_text(encoding="utf-8").strip() == "absent"

    args = builder_args.read_text(encoding="utf-8").splitlines()
    assert "--user" in args
    assert "--install" in args
    assert "--assumeyes" in args
    assert "--force-clean" in args
    assert "--delete-build-dirs" in args
    assert not any(argument.startswith("--install-deps-from") for argument in args)
    assert "flathub" not in args
    assert args[-1] == str(FLATPAK_MANIFEST)
    state_arg = next(argument for argument in args if argument.startswith("--state-dir="))
    build_root = Path(state_arg.removeprefix("--state-dir=")).parent
    assert build_root.name.startswith("vrhotspot-flatpak-companion.")
    assert not build_root.exists()


def test_flatpak_builder_success_is_reported_as_user_scoped(tmp_path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    make_executable(fake_bin / "flatpak")
    make_executable(fake_bin / "flatpak-builder")
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env['PATH']}"

    result = run_bash(
        f"""
        source {shlex.quote(str(INSTALLER))}
        TEMP_INSTALL_DIR={shlex.quote(str(ROOT))}
        resolve_flatpak_companion_user() {{
            FLATPAK_COMPANION_USER=test-desktop-user
            FLATPAK_COMPANION_UID="$(id -u)"
            FLATPAK_COMPANION_GID="$(id -g)"
        }}
        check_flatpak_companion_prerequisites
        build_and_install_flatpak_companion
        """,
        env=env,
    )

    assert result.returncode == 0, result.stderr
    assert (
        "io.github.josethevrtech.VRhotspot) installed for test-desktop-user"
        in result.stdout
    )


def test_installer_companion_boundary_does_not_read_or_forward_daemon_tokens():
    installer = INSTALLER.read_text(encoding="utf-8")
    start = installer.index("# --- Optional Flatpak Companion ---")
    end = installer.index("\n_fix_apt_code_repo_signedby_conflict()", start)
    companion = installer[start:end]

    assert "${VR_HOTSPOTD_API_TOKEN" not in companion
    assert "$VR_HOTSPOTD_API_TOKEN" not in companion
    assert "${API_TOKEN" not in companion
    assert "$API_TOKEN" not in companion
    assert "/etc/" not in companion
    assert "/var/lib/" not in companion
    assert "keyring" not in companion.lower()
    assert "portal" not in companion.lower()
    assert "--token" not in companion
    assert "--live-pairing-smoke-json" not in companion
    assert companion.count(
        "env -u VR_HOTSPOTD_API_TOKEN -u API_TOKEN"
    ) == 2


def test_no_installer_token_cli_argument_and_companion_flag_is_documented():
    result = subprocess.run(
        ["/bin/bash", str(INSTALLER), "--help"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "--install-flatpak-companion" in result.stdout
    assert "--token" not in result.stdout
    assert "--api-token" not in result.stdout


def test_flatpak_manifest_permissions_remain_minimal_and_unchanged():
    manifest = json.loads(FLATPAK_MANIFEST.read_text(encoding="utf-8"))

    assert manifest["finish-args"] == [
        "--share=network",
        "--share=ipc",
        "--socket=wayland",
        "--socket=fallback-x11",
        "--talk-name=org.kde.StatusNotifierWatcher",
        "--talk-name=org.freedesktop.secrets",
    ]
    assert not any(
        argument.startswith("--filesystem=")
        or "system-bus" in argument
        or argument.startswith("--device=")
        for argument in manifest["finish-args"]
    )
    assert {
        argument
        for argument in manifest["finish-args"]
        if argument.startswith("--talk-name=")
    } == {
        "--talk-name=org.kde.StatusNotifierWatcher",
        "--talk-name=org.freedesktop.secrets",
    }


def test_daemon_uninstallers_do_not_remove_user_flatpaks_or_remotes():
    for path in (TOP_UNINSTALLER, BACKEND_UNINSTALLER):
        uninstaller = path.read_text(encoding="utf-8")
        assert "flatpak uninstall" not in uninstaller
        assert "flatpak remote" not in uninstaller
        assert "io.github.josethevrtech.VRhotspot" not in uninstaller

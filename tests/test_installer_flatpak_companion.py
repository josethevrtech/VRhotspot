import json
import os
from pathlib import Path
import re
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


def installer_companion_section() -> str:
    installer = INSTALLER.read_text(encoding="utf-8")
    start = installer.index("# --- Optional Flatpak Companion ---")
    end = installer.index("\n_fix_apt_code_repo_signedby_conflict()", start)
    return installer[start:end]


def test_installer_companion_token_use_is_limited_to_stdin_pairing_pipe():
    companion = installer_companion_section()

    # The daemon token variable may appear only as a value-free emptiness
    # guard and as the single builtin expansion feeding the stdin pipe of
    # the Flatpak stdin pairing mode.
    expansions = [
        line.strip()
        for line in companion.splitlines()
        if "$API_TOKEN" in line
        or "${API_TOKEN" in line
        or "$VR_HOTSPOTD_API_TOKEN" in line
        or "${VR_HOTSPOTD_API_TOKEN" in line
    ]
    assert expansions == [
        'if [ -z "${API_TOKEN:-}" ]; then',
        """printf '%s\\n' "$API_TOKEN" |""",
    ]
    assert re.search(
        r'printf \'%s\\n\' "\$API_TOKEN" \|\s*'
        r"run_flatpak_companion_session_as_user \\\s*"
        r'flatpak run "\$FLATPAK_COMPANION_APP_ID" \\\s*'
        r"--pair-token-stdin --save",
        companion,
    )

    # The token never travels through argv token flags or other channels.
    assert "--token" not in companion
    assert "--api-token" not in companion
    assert "--pair-token-stdin=" not in companion
    assert "--live-pairing-smoke-json" not in companion
    assert "/etc/" not in companion
    assert "/var/lib/" not in companion
    assert "keyring" not in companion.lower()
    assert "portal" not in companion.lower()
    assert "localstorage" not in companion.lower()
    assert "sessionstorage" not in companion.lower()
    assert "indexeddb" not in companion.lower()

    # Every companion command runner scrubs credential variables from the
    # environment; the session runner appears in both UID branches, as does
    # the build runner.
    assert companion.count(
        "env -u VR_HOTSPOTD_API_TOKEN -u API_TOKEN"
    ) == 4


def auto_pair_script(overrides: str = "") -> str:
    return f"""
    source {shlex.quote(str(INSTALLER))}
    FLATPAK_COMPANION_INSTALLED=1
    FLATPAK_COMPANION_USER="$(id -un)"
    FLATPAK_COMPANION_UID="$(id -u)"
    FLATPAK_COMPANION_GID="$(id -g)"
    API_TOKEN=deterministic-test-pairing-token
    export API_TOKEN
    wait_for_daemon_health() {{ echo health-polled; }}
    flatpak_companion_session_bus_available() {{ return 0; }}
    {overrides}
    pair_flatpak_companion_if_installed
    echo "paired=$FLATPAK_COMPANION_PAIRED"
    echo "tray=$FLATPAK_COMPANION_TRAY_LAUNCHED"
    """


def write_fake_pairing_flatpak(fake_bin: Path, *, pairing_body: str) -> None:
    make_executable(
        fake_bin / "flatpak",
        f"""#!/bin/sh
printf '%s\\n' "$@" >> "$FAKE_FLATPAK_ARGS"
if [ "${{API_TOKEN+x}}" = x ] || [ "${{VR_HOTSPOTD_API_TOKEN+x}}" = x ]; then
    echo present >> "$FAKE_FLATPAK_CREDENTIAL_STATE"
else
    echo absent >> "$FAKE_FLATPAK_CREDENTIAL_STATE"
fi
for argument in "$@"; do
    if [ "$argument" = --pair-token-stdin ]; then
        cat > "$FAKE_FLATPAK_STDIN"
{pairing_body}
    fi
done
exit 0
""",
    )
    make_executable(
        fake_bin / "setsid",
        """#!/bin/sh
if [ "$1" = --fork ]; then
    shift
fi
exec "$@"
""",
    )


def run_auto_pair(tmp_path: Path, *, pairing_body: str, overrides: str = ""):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    write_fake_pairing_flatpak(fake_bin, pairing_body=pairing_body)
    args_file = tmp_path / "flatpak.args"
    stdin_file = tmp_path / "flatpak.stdin"
    credential_file = tmp_path / "flatpak.credentials"
    args_file.touch()
    credential_file.touch()
    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{fake_bin}:{env['PATH']}",
            "FAKE_FLATPAK_ARGS": str(args_file),
            "FAKE_FLATPAK_STDIN": str(stdin_file),
            "FAKE_FLATPAK_CREDENTIAL_STATE": str(credential_file),
        }
    )
    result = run_bash(auto_pair_script(overrides), env=env)
    return result, args_file, stdin_file, credential_file


def test_auto_pair_pipes_token_only_via_stdin_and_launches_tray(tmp_path):
    result, args_file, stdin_file, credential_file = run_auto_pair(
        tmp_path,
        pairing_body=(
            '        printf \'%s\\n\' \'{"detail_code":"saved_securely",'
            '"message":"Saved.","ok":true,"paired":true,"saved":true}\'\n'
            "        exit 0"
        ),
    )

    assert result.returncode == 0, result.stderr
    assert "health-polled" in result.stdout
    assert "paired=1" in result.stdout
    assert "tray=1" in result.stdout
    assert "authentication saved" in result.stdout
    assert "tray companion launched" in result.stdout.lower()
    assert "deterministic-test-pairing-token" not in result.stdout
    assert "deterministic-test-pairing-token" not in result.stderr

    # The token reached the pairing mode through stdin only.
    assert stdin_file.read_text(encoding="utf-8") == (
        "deterministic-test-pairing-token\n"
    )
    argv_lines = args_file.read_text(encoding="utf-8").splitlines()
    assert "deterministic-test-pairing-token" not in argv_lines
    assert not any("deterministic-test-pairing-token" in arg for arg in argv_lines)
    assert "--pair-token-stdin" in argv_lines
    assert "--save" in argv_lines
    assert "--tray" in argv_lines

    # Credential environment variables were scrubbed for every invocation.
    states = set(credential_file.read_text(encoding="utf-8").split())
    assert states == {"absent"}


def test_auto_pair_nonzero_exit_falls_back_without_tray_launch(tmp_path):
    result, args_file, stdin_file, _credential_file = run_auto_pair(
        tmp_path,
        pairing_body=(
            '        printf \'%s\\n\' \'{"detail_code":"token_rejected",'
            '"message":"Rejected.","ok":false,"paired":false,"saved":false}\'\n'
            "        exit 1"
        ),
    )

    assert result.returncode == 0, result.stderr
    assert "paired=0" in result.stdout
    assert "tray=0" in result.stdout
    assert "did not succeed (exit 1)" in result.stdout
    assert "manual Web UI authentication steps" in result.stdout
    assert "deterministic-test-pairing-token" not in result.stdout
    argv_lines = args_file.read_text(encoding="utf-8").splitlines()
    assert "--tray" not in argv_lines
    assert stdin_file.read_text(encoding="utf-8") == (
        "deterministic-test-pairing-token\n"
    )


def test_auto_pair_requires_desktop_session_bus(tmp_path):
    result, args_file, stdin_file, _credential_file = run_auto_pair(
        tmp_path,
        pairing_body="        exit 0",
        overrides="""
        flatpak_companion_session_bus_available() { return 1; }
        wait_for_daemon_health() { echo forbidden-health-poll; }
        """,
    )

    assert result.returncode == 0, result.stderr
    assert "no desktop session bus" in result.stdout
    assert "paired=0" in result.stdout
    assert "tray=0" in result.stdout
    assert "forbidden-health-poll" not in result.stdout
    assert args_file.read_text(encoding="utf-8") == ""
    assert not stdin_file.exists()


def test_auto_pair_skips_when_companion_not_installed(tmp_path):
    result, args_file, stdin_file, _credential_file = run_auto_pair(
        tmp_path,
        pairing_body="        exit 0",
        overrides="""
        FLATPAK_COMPANION_INSTALLED=0
        flatpak_companion_session_bus_available() { echo forbidden-bus-check; }
        """,
    )

    assert result.returncode == 0, result.stderr
    assert "paired=0" in result.stdout
    assert "tray=0" in result.stdout
    assert "forbidden-bus-check" not in result.stdout
    assert args_file.read_text(encoding="utf-8") == ""
    assert not stdin_file.exists()


def test_auto_pair_daemon_health_timeout_falls_back(tmp_path):
    result, args_file, stdin_file, _credential_file = run_auto_pair(
        tmp_path,
        pairing_body="        exit 0",
        overrides="wait_for_daemon_health() { return 1; }",
    )

    assert result.returncode == 0, result.stderr
    assert "did not become healthy" in result.stdout
    assert "paired=0" in result.stdout
    assert args_file.read_text(encoding="utf-8") == ""
    assert not stdin_file.exists()


def completion_script(setup: str) -> str:
    return f"""
    source {shlex.quote(str(INSTALLER))}
    clear() {{ :; }}
    eval 'ip() {{ echo "1.1.1.1 via 192.0.2.1 dev eth0 src 192.0.2.10 uid 0"; }}'
    ENABLE_REMOTE=n
    API_TOKEN=completion-test-secret-token
    {setup}
    show_completion
    """


def test_completion_screen_omits_token_after_successful_auto_pair():
    result = run_bash(
        completion_script(
            """
            FLATPAK_COMPANION_PAIRED=1
            FLATPAK_COMPANION_TRAY_LAUNCHED=1
            """
        )
    )

    assert result.returncode == 0, result.stderr
    assert "completion-test-secret-token" not in result.stdout
    assert "No token copy/paste is needed on this desktop" in result.stdout
    assert "Your API Token" not in result.stdout


def test_completion_screen_mentions_remote_manual_auth_after_auto_pair():
    result = run_bash(
        completion_script(
            """
            FLATPAK_COMPANION_PAIRED=1
            FLATPAK_COMPANION_TRAY_LAUNCHED=1
            ENABLE_REMOTE=y
            """
        )
    )

    assert result.returncode == 0, result.stderr
    assert "completion-test-secret-token" not in result.stdout
    assert "Remote browsers still require manual authentication" in result.stdout
    assert "sudo grep VR_HOTSPOTD_API_TOKEN" in result.stdout


@pytest.mark.parametrize(
    "setup",
    (
        "FLATPAK_COMPANION_PAIRED=0\nFLATPAK_COMPANION_TRAY_LAUNCHED=0",
        "FLATPAK_COMPANION_PAIRED=1\nFLATPAK_COMPANION_TRAY_LAUNCHED=0",
        "",
    ),
)
def test_completion_screen_keeps_manual_token_fallback(setup):
    result = run_bash(completion_script(setup))

    assert result.returncode == 0, result.stderr
    assert "Your API Token" in result.stdout
    assert "completion-test-secret-token" in result.stdout
    assert "Paste the token when prompted" in result.stdout


def test_main_runs_auto_pair_after_companion_install():
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
        install_flatpak_companion_if_requested() {{ echo install-step; }}
        pair_flatpak_companion_if_installed() {{ echo pair-step; }}
        show_completion() {{ echo completion-step; }}
        main --non-interactive --install-flatpak-companion --no-clear
        """
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.index("install-step") < result.stdout.index("pair-step")
    assert result.stdout.index("pair-step") < result.stdout.index("completion-step")


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


def test_backend_daemon_uninstaller_does_not_touch_flatpak():
    uninstaller = BACKEND_UNINSTALLER.read_text(encoding="utf-8")
    assert "flatpak" not in uninstaller
    assert "io.github.josethevrtech.VRhotspot" not in uninstaller


def test_companion_cleanup_flatpak_use_is_scoped_to_the_app_id():
    for path in (TOP_UNINSTALLER, INSTALLER):
        text = path.read_text(encoding="utf-8")
        assert "flatpak remote" not in text
        assert "--unused" not in text
        assert "--delete-data" not in text
        for line in text.splitlines():
            if "flatpak uninstall" not in line:
                continue
            assert (
                "$FLATPAK_COMPANION_APP_ID" in line
                or "io.github.josethevrtech.VRhotspot" in line
            ), line
            assert "--all" not in line
            assert "runtime" not in line

"""Installer source cleanup must never adopt or delete a user's checkout."""

from pathlib import Path
import shlex
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "install.sh"


def run_bash(script: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["/bin/bash", "-c", f"source {shlex.quote(str(INSTALLER))}\n{script}"],
        cwd=cwd,
        text=True,
        capture_output=True,
        check=False,
    )


@pytest.mark.parametrize("companion_only", [False, True])
def test_main_preserves_user_provided_temporary_checkout(tmp_path, companion_only):
    source_dir = tmp_path / "user-checkout"
    source_dir.mkdir()
    (source_dir / "backend").mkdir()
    marker = source_dir / "pyproject.toml"
    marker.write_text("# user-owned source\n", encoding="utf-8")

    result = run_bash(
        f"""
        check_root() {{ :; }}
        cleanup_previous_install() {{ :; }}
        detect_os() {{ OS_ID=ubuntu; OS_ID_LIKE=; PKG_MANAGER=apt; }}
        install_dependencies() {{ :; }}
        validate_endeavouros_runtime_dependencies() {{ :; }}
        configure_install() {{ :; }}
        install_daemon() {{ :; }}
        install_flatpak_companion_if_requested() {{
            FLATPAK_COMPANION_REQUEST_SUCCEEDED=1
        }}
        pair_flatpak_companion_if_installed() {{ :; }}
        show_completion() {{ :; }}
        flatpak_companion_recovery_steps() {{ :; }}
        main --non-interactive --no-clear {"--flatpak-companion-only" if companion_only else ""}
        echo "source-owned=$TEMP_INSTALL_DIR_OWNED"
        """,
        source_dir,
    )

    assert result.returncode == 0, result.stderr
    assert "Using local files" in result.stdout
    assert "source-owned=0" in result.stdout
    assert marker.read_text(encoding="utf-8") == "# user-owned source\n"


def test_owned_clone_cleanup_removes_only_clone_and_retains_diagnostic_log(tmp_path):
    unrelated = tmp_path / "unrelated.txt"
    unrelated.write_text("keep", encoding="utf-8")
    result = run_bash(
        """
        git() { touch "$TEMP_INSTALL_DIR/clone-marker"; }
        get_source_files
        echo "clone-path=$TEMP_INSTALL_DIR"
        test -f "$TEMP_INSTALL_DIR/clone-marker"
        create_flatpak_companion_log
        echo "diagnostic-log=$FLATPAK_COMPANION_LOG"
        cleanup_installer_source
        test ! -e "$TEMP_INSTALL_DIR"
        test -f "$FLATPAK_COMPANION_LOG"
        echo "source-owned=$TEMP_INSTALL_DIR_OWNED"
        """,
        tmp_path,
    )

    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    clone_path = Path(next(line.removeprefix("clone-path=") for line in lines if line.startswith("clone-path=")))
    log_path = Path(next(line.removeprefix("diagnostic-log=") for line in lines if line.startswith("diagnostic-log=")))
    try:
        assert not clone_path.exists()
        assert log_path.is_file()
        assert unrelated.read_text(encoding="utf-8") == "keep"
        assert "source-owned=0" in result.stdout
    finally:
        log_path.unlink(missing_ok=True)


def test_cleanup_rejects_other_path_even_with_ownership_flag(tmp_path):
    marker = tmp_path / "keep.txt"
    marker.write_text("keep", encoding="utf-8")
    result = run_bash(
        f"""
        TEMP_INSTALL_DIR={shlex.quote(str(tmp_path))}
        TEMP_INSTALL_DIR_OWNED=1
        cleanup_installer_source
        """,
        tmp_path,
    )

    assert result.returncode == 1, result.stderr
    assert "Refusing to clean unexpected installer source path" in result.stdout
    assert marker.read_text(encoding="utf-8") == "keep"


def test_clone_does_not_adopt_preexisting_symlink_as_owned_source(tmp_path):
    marker = tmp_path / "keep.txt"
    marker.write_text("keep", encoding="utf-8")
    result = run_bash(
        f"""
        clone_path="/tmp/vr-hotspot-install-$$"
        ln -s {shlex.quote(str(tmp_path))} "$clone_path"
        git() {{ echo forbidden-clone; return 99; }}
        if get_source_files; then echo forbidden-success; fi
        cleanup_installer_source
        test -L "$clone_path"
        rm -- "$clone_path"
        echo "source-owned=$TEMP_INSTALL_DIR_OWNED"
        """,
        tmp_path,
    )

    assert result.returncode == 0, result.stderr
    assert "Cannot create a new installer source directory" in result.stdout
    assert "forbidden-" not in result.stdout
    assert "source-owned=0" in result.stdout
    assert marker.read_text(encoding="utf-8") == "keep"


def test_failed_clone_cleans_only_newly_owned_source(tmp_path):
    marker = tmp_path / "keep.txt"
    marker.write_text("keep", encoding="utf-8")
    result = run_bash(
        """
        git() { touch "$TEMP_INSTALL_DIR/partial-clone"; return 23; }
        if get_source_files; then echo forbidden-success; fi
        test ! -e "$TEMP_INSTALL_DIR"
        echo "source-owned=$TEMP_INSTALL_DIR_OWNED"
        """,
        tmp_path,
    )

    assert result.returncode == 0, result.stderr
    assert "Source clone failed" in result.stdout
    assert "forbidden-success" not in result.stdout
    assert "source-owned=0" in result.stdout
    assert marker.read_text(encoding="utf-8") == "keep"

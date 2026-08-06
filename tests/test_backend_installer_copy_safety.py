from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "backend" / "scripts" / "install.sh"


def test_copy_helper_excludes_dev_artifacts_and_handles_stale_node_modules(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    destination = tmp_path / "destination"

    (source / "backend" / "vr_hotspotd").mkdir(parents=True)
    (source / "backend" / "vr_hotspotd" / "main.py").write_text(
        "print('installed')\n", encoding="utf-8"
    )
    (source / "assets").mkdir()
    (source / "assets" / "ui.js").write_text("// ui\n", encoding="utf-8")
    (source / "node_modules" / "jsdom").mkdir(parents=True)
    (source / "node_modules" / "jsdom" / "package.json").write_text(
        "{}\n", encoding="utf-8"
    )
    (source / ".venv" / "bin").mkdir(parents=True)
    (source / ".venv" / "bin" / "python").write_text("dev-only\n", encoding="utf-8")
    (source / ".pytest_cache").mkdir()
    (source / ".pytest_cache" / "README.md").write_text("cache\n", encoding="utf-8")

    destination.mkdir()
    # Reproduce the hardware-test failure: an old install has node_modules
    # as a non-directory while the source has it as a directory.
    (destination / "node_modules").write_text("stale\n", encoding="utf-8")

    subprocess.run(
        [
            "bash",
            "-c",
            'source "$1"; copy_application_files "$2" "$3"',
            "bash",
            str(INSTALLER),
            str(source),
            str(destination),
        ],
        check=True,
    )

    assert (destination / "backend" / "vr_hotspotd" / "main.py").is_file()
    assert (destination / "assets" / "ui.js").is_file()
    assert not (destination / "node_modules").exists()
    assert not (destination / ".venv").exists()
    assert not (destination / ".pytest_cache").exists()


def test_installer_restores_previously_active_services_after_failure() -> None:
    source = INSTALLER.read_text(encoding="utf-8")

    assert 'trap restore_active_services_on_failure EXIT' in source
    assert 'systemctl is-active --quiet "$DAEMON_UNIT"' in source
    assert 'systemctl is-active --quiet "$AUTOSTART_UNIT"' in source
    assert 'systemctl start "$DAEMON_UNIT"' in source
    assert 'systemctl start "$AUTOSTART_UNIT"' in source
    assert 'SERVICES_STOPPED="1"' in source
    assert 'SERVICES_STOPPED="0"' in source


def test_installer_no_longer_recursively_copies_the_worktree() -> None:
    source = INSTALLER.read_text(encoding="utf-8")

    assert 'cp -r "$BACKEND_SRC/../." "$INSTALL_DIR/"' not in source
    assert 'copy_application_files "$REPO_ROOT" "$INSTALL_DIR"' in source
    assert '"--exclude=./node_modules"' in source
    assert '"--exclude=./.git"' in source

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "backend" / "scripts" / "install.sh"


def test_installer_stops_runtime_before_overwriting_bundled_executables() -> None:
    source = INSTALLER.read_text(encoding="utf-8")

    stop_marker = 'systemctl stop "$AUTOSTART_UNIT" "$DAEMON_UNIT"'
    copy_marker = 'copy_application_files "$REPO_ROOT" "$INSTALL_DIR"'

    assert stop_marker in source
    assert copy_marker in source
    assert source.index(stop_marker) < source.index(copy_marker)
    assert 'Stopping active VRhotspot services before file sync' in source
    assert 'cp -r "$BACKEND_SRC/../." "$INSTALL_DIR/"' not in source

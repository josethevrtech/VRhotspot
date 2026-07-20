import os
from pathlib import Path
import stat
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "install.sh"


def make_executable(path: Path, contents: str = "#!/bin/sh\nexit 0\n") -> Path:
    path.write_text(contents, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def run_bash(
    script: str,
    *,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "-c", script],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def detect_endeavouros() -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.update(
        {
            "VR_HOTSPOT_OS_ID": "endeavouros",
            "VR_HOTSPOT_OS_NAME": "EndeavourOS",
            "VR_HOTSPOT_OS_ID_LIKE": "arch",
        }
    )
    return run_bash(
        f"""
        source {INSTALLER}
        detect_os
        printf 'os_id=%s\n' "$OS_ID"
        printf 'package_manager=%s\n' "$PKG_MANAGER"
        """,
        env=env,
    )


def test_endeavouros_os_detection_is_supported():
    result = detect_endeavouros()

    assert result.returncode == 0
    assert "os_id=endeavouros" in result.stdout
    assert "Detected EndeavourOS" in result.stdout


def test_endeavouros_selects_pacman():
    result = detect_endeavouros()

    assert result.returncode == 0
    assert "package_manager=pacman" in result.stdout


@pytest.mark.parametrize(
    ("os_id", "package_manager"),
    [
        ("steamos", "pacman"),
        ("cachyos", "pacman"),
        ("arch", "pacman"),
        ("endeavouros", "pacman"),
        ("ubuntu", "apt"),
        ("debian", "apt"),
        ("pop", "apt"),
        ("fedora", "dnf"),
        ("bazzite", "rpm-ostree"),
    ],
)
def test_package_manager_detection_matrix_is_unchanged(os_id, package_manager):
    env = os.environ.copy()
    env.update(
        {
            "VR_HOTSPOT_OS_ID": os_id,
            "VR_HOTSPOT_OS_NAME": os_id,
            "VR_HOTSPOT_OS_ID_LIKE": "",
        }
    )

    result = run_bash(
        f"""
        source {INSTALLER}
        detect_os
        printf 'package_manager=%s\n' "$PKG_MANAGER"
        """,
        env=env,
    )

    assert result.returncode == 0
    assert f"package_manager={package_manager}" in result.stdout


def test_endeavouros_dependency_plan_keeps_vendor_hostapd_and_system_dnsmasq():
    result = run_bash(
        f"""
        source {INSTALLER}
        OS_ID=endeavouros
        PKG_MANAGER=pacman
        calculate_dependency_list
        printf '%s\n' "${{DEPENDENCIES[*]}}"
        """
    )

    assert result.returncode == 0
    dependencies = result.stdout.strip().split()
    assert {"python", "python-pip", "iw", "iproute2", "dnsmasq", "iptables"} <= set(
        dependencies
    )
    assert "hostapd" not in dependencies


def test_endeavouros_runtime_validation_accepts_bundled_hostapd_and_system_dnsmasq(
    tmp_path,
):
    vendor_bin = tmp_path / "source" / "backend" / "vendor" / "bin"
    vendor_bin.mkdir(parents=True)
    make_executable(vendor_bin / "hostapd")

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    make_executable(fake_bin / "dnsmasq")

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    result = run_bash(
        f"""
        source {INSTALLER}
        OS_ID=endeavouros
        TEMP_INSTALL_DIR={tmp_path / "source"}
        validate_endeavouros_runtime_dependencies
        """,
        env=env,
    )

    assert result.returncode == 0
    assert "runtime dependencies found (hostapd and dnsmasq)" in result.stdout


def test_endeavouros_pacman_install_includes_system_dnsmasq_fallback(tmp_path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    pacman_log = tmp_path / "pacman.log"
    make_executable(
        fake_bin / "pacman",
        (
            "#!/bin/sh\n"
            "printf '%s\\n' \"$*\" >> \"$PACMAN_LOG\"\n"
            "[ \"$1\" = '-Sy' ]\n"
        ),
    )
    make_executable(fake_bin / "pacman-key")
    make_executable(fake_bin / "install")

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    env["PACMAN_LOG"] = str(pacman_log)
    result = run_bash(
        f"""
        source {INSTALLER}
        OS_ID=endeavouros
        PKG_MANAGER=pacman
        install_dependencies
        """,
        env=env,
    )

    assert result.returncode == 0
    install_args = next(
        line.split()
        for line in pacman_log.read_text(encoding="utf-8").splitlines()
        if line.startswith("-Sy ")
    )
    assert "dnsmasq" in install_args


def test_steamos_dependency_plan_remains_separate_from_mutable_pacman_distros():
    result = run_bash(
        f"""
        source {INSTALLER}
        OS_ID=steamos
        PKG_MANAGER=pacman
        calculate_dependency_list
        printf '%s\n' "${{DEPENDENCIES[*]}}"
        """
    )

    assert result.returncode == 0
    dependencies = result.stdout.strip().split()
    assert "hostapd" not in dependencies
    assert "dnsmasq" not in dependencies
    assert "iptables" not in dependencies


def test_endeavouros_full_install_flow_uses_firewalld_forwarding_path():
    result = run_bash(
        f"""
        source {INSTALLER}
        check_root() {{ :; }}
        cleanup_previous_install() {{ :; }}
        detect_os() {{
            OS_ID=endeavouros
            OS_NAME=EndeavourOS
            OS_ID_LIKE=arch
            PKG_MANAGER=pacman
        }}
        install_dependencies() {{ :; }}
        get_source_files() {{ TEMP_INSTALL_DIR="$PWD"; }}
        validate_endeavouros_runtime_dependencies() {{ :; }}
        configure_install() {{ :; }}
        install_daemon() {{ :; }}
        enable_firewalld_uplink_forwarding() {{ echo firewalld-forwarding-called; }}
        show_completion() {{ :; }}
        main --non-interactive --no-clear
        """
    )

    assert result.returncode == 0
    assert result.stdout.count("firewalld-forwarding-called") == 1


def test_firewalld_forwarding_enables_runtime_and_permanent_rules(tmp_path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    firewalld_log = tmp_path / "firewalld.log"
    make_executable(
        fake_bin / "firewall-cmd",
        (
            "#!/bin/sh\n"
            "printf '%s\\n' \"$*\" >> \"$FIREWALLD_LOG\"\n"
            "case \"$1\" in\n"
            "  --state) exit 0 ;;\n"
            "  --get-zone-of-interface=*) echo public ;;\n"
            "esac\n"
            "exit 0\n"
        ),
    )
    make_executable(
        fake_bin / "ip",
        (
            "#!/bin/sh\n"
            "if [ \"$1 $2 $3\" = 'route show default' ]; then\n"
            "  echo 'default via 192.0.2.1 dev enp1s0'\n"
            "fi\n"
        ),
    )

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    env["FIREWALLD_LOG"] = str(firewalld_log)
    result = run_bash(
        f"""
        source {INSTALLER}
        enable_firewalld_uplink_forwarding
        """,
        env=env,
    )

    assert result.returncode == 0
    calls = firewalld_log.read_text(encoding="utf-8").splitlines()
    assert "--zone public --add-masquerade" in calls
    assert "--zone public --add-forward" in calls
    assert "--permanent --zone public --add-masquerade" in calls
    assert "--permanent --zone public --add-forward" in calls

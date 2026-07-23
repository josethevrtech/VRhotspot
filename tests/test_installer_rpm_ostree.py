import os
import stat
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "install.sh"
BACKEND_INSTALLER = ROOT / "backend" / "scripts" / "install.sh"
README = ROOT / "README.md"
PLATFORM_COMPATIBILITY = ROOT / "docs" / "PLATFORM_COMPATIBILITY.md"


def make_executable(path: Path, contents: str = "#!/bin/sh\nexit 0\n") -> Path:
    path.write_text(contents, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def run_bash(script: str, *, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "-c", script],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def test_rpm_ostree_already_requested_detector_requires_expected_terms():
    positive = run_bash(
        f"""
        source {INSTALLER}
        if _rpm_ostree_already_requested_output 'error: Package/capability python3 is already requested by rpm-ostree'; then
          echo matched
        else
          echo missed
        fi
        """
    )

    negative = run_bash(
        f"""
        source {INSTALLER}
        if _rpm_ostree_already_requested_output 'error: Package/capability python3 is already requested'; then
          echo matched
        else
          echo missed
        fi
        """
    )

    assert positive.returncode == 0
    assert positive.stdout.strip() == "matched"
    assert negative.returncode == 0
    assert negative.stdout.strip() == "missed"


def test_rpm_ostree_install_wrapper_reports_reboot_guidance(tmp_path):
    fake_rpm_ostree = tmp_path / "rpm-ostree"
    make_executable(
        fake_rpm_ostree,
        "#!/usr/bin/env bash\n"
        "echo 'error: Package/capability iw is already requested by rpm-ostree' >&2\n"
        "exit 1\n",
    )

    env = os.environ.copy()
    env["PATH"] = f"{tmp_path}:{env['PATH']}"

    result = run_bash(
        f"""
        source {INSTALLER}
        set +e
        _run_rpm_ostree_install --apply-live iw
        rc=$?
        echo "rc=$rc"
        """,
        env=env,
    )

    assert result.returncode == 0
    assert "rc=2" in result.stdout
    assert (
        "rpm-ostree reports that this package is already requested. "
        "Reboot your system, then rerun the VR Hotspot installer."
    ) in result.stdout
    assert "Package/capability iw is already requested" in result.stderr


def test_bazzite_dependency_plan_uses_bundled_network_stack():
    result = run_bash(
        f"""
        source {INSTALLER}
        OS_ID=bazzite
        PKG_MANAGER=rpm-ostree
        calculate_dependency_list
        printf '%s\n' "${{DEPENDENCIES[*]}}"
        """
    )

    assert result.returncode == 0
    assert result.stdout.strip().split() == [
        "python3",
        "python3-pip",
        "iw",
        "iproute",
        "iptables",
    ]


def test_bazzite_rpm_ostree_install_does_not_layer_bundled_network_stack(tmp_path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    rpm_ostree_log = tmp_path / "rpm-ostree.log"
    make_executable(fake_bin / "rpm", "#!/bin/sh\nexit 1\n")
    make_executable(
        fake_bin / "rpm-ostree",
        "#!/bin/sh\nprintf '%s\\n' \"$*\" >> \"$RPM_OSTREE_LOG\"\n",
    )

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    env["RPM_OSTREE_LOG"] = str(rpm_ostree_log)
    result = run_bash(
        f"""
        source {INSTALLER}
        OS_ID=bazzite
        PKG_MANAGER=rpm-ostree
        install_dependencies
        """,
        env=env,
    )

    assert result.returncode == 0
    assert "Bazzite uses bundled hostapd/dnsmasq" in result.stdout
    assert rpm_ostree_log.read_text(encoding="utf-8").splitlines() == [
        "install --apply-live python3 python3-pip iw iproute iptables"
    ]


def test_bazzite_check_os_explains_support_layering_and_reboot_policy():
    env = os.environ.copy()
    env.update(
        {
            "VR_HOTSPOT_OS_ID": "bazzite",
            "VR_HOTSPOT_OS_NAME": "Bazzite",
            "VR_HOTSPOT_OS_ID_LIKE": "fedora",
        }
    )

    result = subprocess.run(
        ["bash", str(INSTALLER), "--check-os", "--no-clear"],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0
    assert "Detected Bazzite (rpm-ostree)." in result.stdout
    assert (
        "Bazzite support policy: supported through the rpm-ostree path with "
        "bundled hostapd/dnsmasq."
    ) in result.stdout
    assert "reboot and rerun the installer" in result.stdout
    assert (
        "Dependency plan for Bazzite: python3 python3-pip iw iproute iptables"
        in result.stdout
    )


def test_bazzite_configuration_forces_bundled_network_stack(tmp_path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    make_executable(fake_bin / "openssl", "#!/bin/sh\necho test-api-token\n")
    config_dir = tmp_path / "etc-vr-hotspot"
    env_file = config_dir / "env"

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    result = run_bash(
        f"""
        source {INSTALLER}
        CONFIG_DIR={config_dir}
        ENV_FILE={env_file}
        OS_ID=bazzite
        INTERACTIVE=0
        configure_install
        """,
        env=env,
    )

    assert result.returncode == 0
    configured = env_file.read_text(encoding="utf-8").splitlines()
    assert "VR_HOTSPOT_VENDOR_PROFILE=bazzite" in configured
    assert "VR_HOTSPOT_FORCE_VENDOR_BIN=1" in configured
    assert "forcing bundled hostapd/dnsmasq" in result.stdout


def test_backend_bazzite_policy_accepts_tracked_base_bundle(tmp_path):
    vendor_bin = tmp_path / "vendor" / "bin"
    vendor_bin.mkdir(parents=True)
    make_executable(vendor_bin / "hostapd")
    make_executable(vendor_bin / "dnsmasq")

    result = run_bash(
        f"""
        source {BACKEND_INSTALLER}
        validate_bazzite_vendor_stack {vendor_bin}
        """
    )

    assert result.returncode == 0


def test_backend_bazzite_policy_fails_if_bundled_stack_is_incomplete(tmp_path):
    vendor_bin = tmp_path / "vendor" / "bin"
    vendor_bin.mkdir(parents=True)
    make_executable(vendor_bin / "hostapd")

    result = run_bash(
        f"""
        source {BACKEND_INSTALLER}
        validate_bazzite_vendor_stack {vendor_bin}
        """
    )

    assert result.returncode != 0
    assert "Bazzite requires bundled dnsmasq" in result.stderr


def test_bazzite_support_policy_is_consistent_in_user_docs():
    readme = README.read_text(encoding="utf-8")
    compatibility = PLATFORM_COMPATIBILITY.read_text(encoding="utf-8")

    assert "Bazzite is a supported target" in readme
    assert "bundled hostapd/dnsmasq stack" in readme
    assert "reboot and rerun the installer" in readme
    assert "Bazzite is supported" in compatibility
    assert "does not layer system copies" in compatibility
    assert "user-managed reboot" in compatibility


def test_fedora_dependency_plan_remains_dnf_base_tools_only():
    result = run_bash(
        f"""
        source {INSTALLER}
        OS_ID=fedora
        PKG_MANAGER=dnf
        calculate_dependency_list
        printf '%s\n' "${{DEPENDENCIES[*]}}"
        """
    )

    assert result.returncode == 0
    assert result.stdout.strip().split() == [
        "python3",
        "python3-pip",
        "iw",
        "iproute",
        "iptables",
    ]


def test_cachyos_dependency_plan_installs_dnsmasq_fallback():
    result = run_bash(
        f"""
        source {INSTALLER}
        PKG_MANAGER=pacman
        OS_ID=cachyos
        calculate_dependency_list
        printf '%s\n' "${{DEPENDENCIES[*]}}"
        """
    )

    assert result.returncode == 0
    deps = result.stdout.strip().split()
    assert "dnsmasq" in deps
    assert "hostapd" not in deps


def test_installer_auto_mode_is_non_interactive_without_tty():
    result = run_bash(
        f"""
        source {INSTALLER}
        unset CI GITHUB_ACTIONS GITLAB_CI BUILDKITE TF_BUILD
        resolve_interactive_mode auto
        echo "interactive=$INTERACTIVE"
        echo "reason=$NON_INTERACTIVE_REASON"
        """
    )

    assert result.returncode == 0
    assert "interactive=0" in result.stdout
    assert "reason=no usable terminal detected" in result.stdout


def test_installer_non_interactive_flags_disable_prompts():
    result = run_bash(
        f"""
        source {INSTALLER}
        unset CI GITHUB_ACTIONS GITLAB_CI BUILDKITE TF_BUILD
        resolve_interactive_mode non-interactive
        echo "interactive=$INTERACTIVE"
        echo "reason=$NON_INTERACTIVE_REASON"
        """
    )

    assert result.returncode == 0
    assert "interactive=0" in result.stdout
    assert "reason=requested by command-line flag" in result.stdout


def test_installer_ci_overrides_requested_interactive_mode():
    env = os.environ.copy()
    env["CI"] = "true"

    result = run_bash(
        f"""
        source {INSTALLER}
        resolve_interactive_mode interactive
        echo "interactive=$INTERACTIVE"
        echo "reason=$NON_INTERACTIVE_REASON"
        """,
        env=env,
    )

    assert result.returncode == 0
    assert "interactive=0" in result.stdout
    assert "reason=CI environment detected" in result.stdout


def test_installer_help_documents_interactivity_flags():
    result = subprocess.run(
        ["bash", str(INSTALLER), "--help"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0
    assert "--interactive" in result.stdout
    assert "--non-interactive" in result.stdout
    assert "--yes" in result.stdout

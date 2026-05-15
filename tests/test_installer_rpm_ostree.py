import os
import stat
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "install.sh"


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
    fake_rpm_ostree.write_text(
        "#!/usr/bin/env bash\n"
        "echo 'error: Package/capability iw is already requested by rpm-ostree' >&2\n"
        "exit 1\n"
    )
    fake_rpm_ostree.chmod(fake_rpm_ostree.stat().st_mode | stat.S_IXUSR)

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

from vr_hotspotd.diagnostics import platform
from tests.host_facts_snapshot_factory import make_host_facts_snapshot


def test_platform_os_probe_preserves_public_matrix_shape(monkeypatch):
    monkeypatch.setattr(
        platform,
        "_read_os_release",
        lambda: {
            "pretty_name": "Fedora Linux 42 (Kinoite)",
            "name": "Fedora Linux",
            "id": "fedora",
            "version_id": "42",
            "variant_id": "kinoite",
            "id_like": "rhel centos",
        },
    )

    assert platform._probe_os() == {
        "pretty_name": "Fedora Linux 42 (Kinoite)",
        "id": "fedora",
        "version_id": "42",
        "variant_id": "kinoite",
        "id_like": ["rhel", "centos"],
    }


def test_platform_integration_probe_preserves_presence_and_service_semantics(
    monkeypatch,
):
    present = {
        "systemctl": "/usr/bin/systemctl",
        "nmcli": "/usr/bin/nmcli",
        "firewall-cmd": "/usr/bin/firewall-cmd",
        "ufw": "/usr/sbin/ufw",
        "nft": "/usr/sbin/nft",
    }
    active = {
        "systemd-journald": True,
        "NetworkManager": True,
        "firewalld": False,
        "ufw": True,
    }
    monkeypatch.setattr(platform.shutil, "which", lambda name: present.get(name))
    monkeypatch.setattr(
        platform,
        "_systemctl_is_active",
        lambda service: active[service],
    )

    assert platform._probe_integration() == {
        "systemd": {"present": True, "active": True},
        "network_manager": {
            "present": True,
            "active": True,
            "nmcli": True,
        },
        "firewall": {
            "firewalld": {"present": True, "active": False},
            "ufw": {"present": True, "active": True},
            "nft": {"present": True},
            "iptables": {"present": False},
        },
    }


def test_platform_snapshot_bypasses_duplicate_os_and_service_probes(monkeypatch):
    snapshot = make_host_facts_snapshot(operation_kind="diagnostics_preflight")
    which_calls = []
    service_calls = []

    monkeypatch.setattr(
        platform,
        "_read_os_release",
        lambda: (_ for _ in ()).throw(AssertionError("os-release was re-read")),
    )

    def which(name):
        which_calls.append(name)
        if name == "systemctl":
            return "/usr/bin/systemctl"
        if name in {
            "nmcli",
            "NetworkManager",
            "firewall-cmd",
            "ufw",
            "nft",
            "iptables",
        }:
            raise AssertionError(f"snapshot-owned tool presence was re-probed: {name}")
        return None

    def systemctl_is_active(service):
        service_calls.append(service)
        if service != "systemd-journald":
            raise AssertionError(f"snapshot-owned service was re-probed: {service}")
        return True

    monkeypatch.setattr(platform.shutil, "which", which)
    monkeypatch.setattr(platform, "_systemctl_is_active", systemctl_is_active)
    monkeypatch.setattr(platform, "_path_is_writable", lambda _path: True)

    result = platform.collect_platform_matrix(host_facts_snapshot=snapshot)

    assert result["os"] == {
        "pretty_name": "Ubuntu",
        "id": "ubuntu",
        "version_id": "24.04",
        "variant_id": "",
        "id_like": ["debian"],
    }
    assert result["integration"] == {
        "systemd": {"present": True, "active": True},
        "network_manager": {
            "present": True,
            "active": True,
            "nmcli": True,
        },
        "firewall": {
            "firewalld": {"present": False, "active": False},
            "ufw": {"present": False, "active": False},
            "nft": {"present": True},
            "iptables": {"present": True},
        },
    }
    assert result["notes"] == ["network_manager_active"]
    assert service_calls == ["systemd-journald"]
    assert which_calls == ["rpm-ostree", "steamos-readonly", "mount", "systemctl"]


def test_platform_immutability_keeps_rpm_ostree_priority(monkeypatch):
    monkeypatch.setattr(
        platform.shutil,
        "which",
        lambda name: "/usr/bin/rpm-ostree" if name == "rpm-ostree" else None,
    )
    monkeypatch.setattr(
        platform,
        "_path_is_writable",
        lambda path: path != "/var/lib/vr-hotspot",
    )

    assert platform._probe_immutability() == {
        "is_immutable": True,
        "signal": "rpm-ostree",
        "writable_paths": {
            "/var": True,
            "/var/lib": True,
            "/var/lib/vr-hotspot": False,
        },
    }

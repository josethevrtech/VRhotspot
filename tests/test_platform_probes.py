from vr_hotspotd.diagnostics import platform


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

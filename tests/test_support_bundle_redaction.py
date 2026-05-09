from vr_hotspotd.diagnostics.support_bundle import (
    AUTHORIZATION_PLACEHOLDER,
    PRIVATE_KEY_PLACEHOLDER,
    SECRET_PLACEHOLDER,
    redact_support_bundle_data,
    redact_support_bundle_text,
)


def test_redacts_api_tokens_and_authorization_headers():
    text = "\n".join(
        [
            "VR_HOTSPOTD_API_TOKEN=super-secret-token",
            "curl http://127.0.0.1/v1/status?token=copied-token",
            "Authorization: Bearer copied-token",
        ]
    )

    redacted = redact_support_bundle_text(text)

    assert "super-secret-token" not in redacted
    assert "copied-token" not in redacted
    assert f"VR_HOTSPOTD_API_TOKEN={SECRET_PLACEHOLDER}" in redacted
    assert f"?token={SECRET_PLACEHOLDER}" in redacted
    assert f"Authorization: {AUTHORIZATION_PLACEHOLDER}" in redacted


def test_redacts_wifi_passphrases_and_psks_in_text_and_dicts():
    text = "\n".join(
        [
            "wpa2_passphrase=quest-hotspot-secret",
            "wpa_passphrase=legacy-secret",
            "psk=0123456789abcdef",
            "sae_password=wpa3-secret",
        ]
    )
    data = {
        "ssid": "VRHotspot",
        "wpa2_passphrase": "quest-hotspot-secret",
        "nested": {
            "psk": "0123456789abcdef",
            "sae_password": "wpa3-secret",
        },
    }

    redacted_text = redact_support_bundle_text(text)
    redacted_data = redact_support_bundle_data(data)

    for secret in ("quest-hotspot-secret", "legacy-secret", "0123456789abcdef", "wpa3-secret"):
        assert secret not in redacted_text
    assert redacted_text.count(SECRET_PLACEHOLDER) == 4
    assert redacted_data == {
        "ssid": "VRHotspot",
        "wpa2_passphrase": SECRET_PLACEHOLDER,
        "nested": {
            "psk": SECRET_PLACEHOLDER,
            "sae_password": SECRET_PLACEHOLDER,
        },
    }


def test_redacts_private_key_blocks():
    text = """before
-----BEGIN OPENSSH PRIVATE KEY-----
b3BlbnNzaC1rZXktdjEAAAAABG5vbmUAAAA
-----END OPENSSH PRIVATE KEY-----
after"""

    redacted = redact_support_bundle_text(text)

    assert "b3BlbnNzaC1rZXktdjE" not in redacted
    assert PRIVATE_KEY_PLACEHOLDER in redacted
    assert redacted.startswith("before")
    assert redacted.endswith("after")


def test_redacts_emails_home_users_public_ipv4_and_macs_with_stable_placeholders():
    text = "\n".join(
        [
            "alice@example.com opened /home/alice/.config/vr-hotspot/config.json",
            "client aa:bb:cc:dd:ee:ff connected from 8.8.8.8",
            "repeat alice@example.com /home/alice aa-bb-cc-dd-ee-ff 8.8.8.8",
            "other bob@example.com /home/bob 1.1.1.1 11:22:33:44:55:66",
        ]
    )

    redacted = redact_support_bundle_text(text)

    assert "alice@example.com" not in redacted
    assert "bob@example.com" not in redacted
    assert "/home/alice" not in redacted
    assert "/home/bob" not in redacted
    assert "aa:bb:cc:dd:ee:ff" not in redacted
    assert "aa-bb-cc-dd-ee-ff" not in redacted
    assert "11:22:33:44:55:66" not in redacted
    assert "8.8.8.8" not in redacted
    assert "1.1.1.1" not in redacted
    assert redacted.count("<redacted-email-1>") == 2
    assert redacted.count("<redacted-user-1>") == 2
    assert redacted.count("<redacted-mac-1>") == 2
    assert redacted.count("<redacted-ipv4-1>") == 2
    assert "<redacted-email-2>" in redacted
    assert "<redacted-user-2>" in redacted
    assert "<redacted-mac-2>" in redacted
    assert "<redacted-ipv4-2>" in redacted


def test_preserves_useful_non_sensitive_diagnostic_values():
    data = {
        "interfaces": ["wlan0", "wlan1"],
        "driver": "ath11k_pci",
        "local_ips": ["192.168.50.1", "10.42.0.1", "172.16.0.5", "127.0.0.1"],
        "readiness": {
            "ready": False,
            "reason_code": "adapter_missing_ap_mode",
            "recommendation": "Use wlan1",
        },
        "log": "iface wlan0 driver ath11k_pci local 192.168.50.1 public 8.8.4.4",
    }

    redacted = redact_support_bundle_data(data)

    assert redacted["interfaces"] == ["wlan0", "wlan1"]
    assert redacted["driver"] == "ath11k_pci"
    assert redacted["local_ips"] == ["192.168.50.1", "10.42.0.1", "172.16.0.5", "127.0.0.1"]
    assert redacted["readiness"]["reason_code"] == "adapter_missing_ap_mode"
    assert "iface wlan0" in redacted["log"]
    assert "driver ath11k_pci" in redacted["log"]
    assert "192.168.50.1" in redacted["log"]
    assert "8.8.4.4" not in redacted["log"]
    assert "<redacted-ipv4-1>" in redacted["log"]

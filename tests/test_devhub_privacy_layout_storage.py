from pathlib import Path

from vr_hotspotd.devtools.device_overview import _parse_storage


ROOT = Path(__file__).resolve().parents[1]
IDENTITY_CSS = ROOT / "assets" / "devhub_device_identity.css"


def test_android_storage_parser_accepts_standard_data_mount() -> None:
    result = _parse_storage(
        "\n".join(
            [
                "Filesystem 1K-blocks Used Available Use% Mounted on",
                "/dev/block/dm-10 104857600 52428800 52428800 50% /data",
            ]
        )
    )

    assert result == {
        "total_bytes": 104857600 * 1024,
        "used_bytes": 52428800 * 1024,
        "available_bytes": 52428800 * 1024,
        "used_percent": 50,
    }


def test_android_storage_parser_accepts_wrapped_device_mapper_output() -> None:
    result = _parse_storage(
        "\n".join(
            [
                "Filesystem 1K-blocks Used Available Use% Mounted on",
                "/dev/block/mapper/userdata-long-device-name",
                "122070312 42000000 80070312 35% /data/user/0",
            ]
        )
    )

    assert result["total_bytes"] == 122070312 * 1024
    assert result["available_bytes"] == 80070312 * 1024
    assert result["used_percent"] == 35


def test_device_workspace_removes_redundant_top_bar_and_groups_refresh_help() -> None:
    source = IDENTITY_CSS.read_text(encoding="utf-8")

    assert ".devhub-device-bar" in source
    assert "display: none !important" in source
    assert "#devhubWorkspaceUpdated" in source
    assert ".devhub-refresh-help" in source
    assert "margin-right: 0 !important" in source


def test_privacy_mode_masks_network_app_and_operation_details() -> None:
    source = IDENTITY_CSS.read_text(encoding="utf-8")

    assert ".devhub-privacy-active #devhubOverviewWifi" in source
    assert ".devhub-privacy-active #devhubPackageList .devhub-list-title" in source
    assert ".devhub-privacy-active #devhubSelectedPackage" in source
    assert ".devhub-privacy-active #devhubPackageName" in source
    assert ".devhub-privacy-active .devhub-activity.visible" in source
    assert "Hidden by Privacy Mode" in source
    assert "Operation completed." in source


def test_app_install_uses_plain_language_and_never_shows_raw_target_serial() -> None:
    source = IDENTITY_CSS.read_text(encoding="utf-8")

    assert "#devhubPackageSerial.devhub-raw-device-serial" in source
    assert '.card:has(#devhubInstallForm) .card-header h2::after' in source
    assert 'content: "Install App"' in source


def test_privacy_mode_explains_that_app_names_are_intentionally_hidden() -> None:
    source = IDENTITY_CSS.read_text(encoding="utf-8")

    assert ".devhub-privacy-active #devhubPackageList::before" in source
    assert 'content: "App names hidden by Privacy Mode"' in source
    assert 'content: "App name hidden"' in source

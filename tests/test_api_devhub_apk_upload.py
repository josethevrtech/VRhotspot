import io
import json
from email.message import Message
from pathlib import Path

import vr_hotspotd.devtools.apk_upload as apk_upload
from vr_hotspotd.devtools.devhub_api import DevHubAPIHandler


SERIAL = "192.168.68.24:5555"
UPLOAD_PATH = "/v1/devbridge/adb/install-upload"


def _make_handler(raw: bytes, *, authorized: bool = True):
    handler = DevHubAPIHandler.__new__(DevHubAPIHandler)
    handler.rfile = io.BytesIO(raw)
    handler.wfile = io.BytesIO()
    handler.headers = Message()
    handler.headers["Content-Length"] = str(len(raw))
    handler.headers["Content-Type"] = "application/vnd.android.package-archive"
    handler.headers["X-VRhotspot-Serial"] = SERIAL
    handler.headers["X-VRhotspot-Apk-Name"] = "training.apk"
    handler.headers["X-VRhotspot-Reinstall"] = "1"
    handler.headers["X-VRhotspot-Grant-Permissions"] = "1"
    if authorized:
        handler.headers["X-Api-Token"] = "secret"
    handler.command = "POST"
    handler.request_version = "HTTP/1.1"
    handler.requestline = f"POST {UPLOAD_PATH} HTTP/1.1"
    handler.path = UPLOAD_PATH
    handler._last_code = None

    def send_response(code, _message=None):
        handler._last_code = code

    handler.send_response = send_response
    handler.send_header = lambda _key, _value: None
    handler.end_headers = lambda: None
    return handler


def _response_json(handler):
    return json.loads(handler.wfile.getvalue().decode("utf-8"))


def test_apk_upload_streams_to_temporary_file_and_classifies_new_install(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("VR_HOTSPOTD_API_TOKEN", "secret")
    monkeypatch.setattr(apk_upload, "APK_UPLOAD_DIR", tmp_path)
    apk_bytes = b"PK\x03\x04test-apk"
    seen = {"operations": [], "package_calls": 0}

    def fake_execute(operation, request=None):
        payload = dict(request or {})
        seen["operations"].append(operation)
        if operation == "packages":
            seen["package_calls"] += 1
            packages = ["com.example.existing"]
            if seen["package_calls"] == 2:
                packages.append("com.example.training")
            return {
                "schema_version": 1,
                "operation": operation,
                "success": True,
                "result_code": "ok",
                "returncode": 0,
                "stdout": "",
                "stderr": "",
                "data": {
                    "serial": payload["serial"],
                    "third_party_only": True,
                    "packages": packages,
                },
            }

        upload_path = Path(payload["apk_path"])
        seen["request"] = payload
        seen["bytes"] = upload_path.read_bytes()
        seen["temporary_path"] = upload_path
        return {
            "schema_version": 1,
            "operation": operation,
            "success": True,
            "result_code": "ok",
            "returncode": 0,
            "stdout": "Success",
            "stderr": "",
            "data": {
                "serial": payload["serial"],
                "apk_path": payload["apk_path"],
                "reinstall": payload["reinstall"],
                "grant_permissions": payload["grant_permissions"],
            },
        }

    monkeypatch.setattr(apk_upload, "execute_adb_operation", fake_execute)
    handler = _make_handler(apk_bytes)

    handler.do_POST()
    payload = _response_json(handler)

    assert handler._last_code == 200
    assert seen["operations"] == ["packages", "install", "packages"]
    assert seen["request"]["serial"] == SERIAL
    assert seen["request"]["reinstall"] is True
    assert seen["request"]["grant_permissions"] is True
    assert seen["bytes"] == apk_bytes
    assert not seen["temporary_path"].exists()
    assert "apk_path" not in payload["data"]["data"]
    assert payload["data"]["data"]["apk_name"] == "training.apk"
    assert payload["data"]["data"]["apk_size_bytes"] == len(apk_bytes)
    assert payload["data"]["data"]["deployment_action"] == "installed"
    assert payload["data"]["data"]["deployment_package"] == "com.example.training"


def test_deployment_details_distinguish_update_and_unknown_inventory() -> None:
    assert apk_upload._deployment_details(
        install_succeeded=True,
        before_packages={"com.example.training"},
        after_packages={"com.example.training"},
    ) == {"deployment_action": "updated"}
    assert apk_upload._deployment_details(
        install_succeeded=True,
        before_packages=None,
        after_packages=None,
    ) == {"deployment_action": "installed_or_updated"}
    assert apk_upload._deployment_details(
        install_succeeded=False,
        before_packages={"com.example.training"},
        after_packages={"com.example.training"},
    ) == {}


def test_apk_upload_requires_auth_before_reading_body(monkeypatch):
    monkeypatch.setenv("VR_HOTSPOTD_API_TOKEN", "secret")
    monkeypatch.setattr(
        apk_upload,
        "execute_adb_operation",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("unauthorized upload executed ADB")
        ),
    )
    handler = _make_handler(b"apk", authorized=False)

    handler.do_POST()

    assert handler._last_code == 401
    assert handler.rfile.tell() == 0
    assert _response_json(handler)["result_code"] == "unauthorized"


def test_apk_upload_rejects_oversized_body(monkeypatch, tmp_path):
    monkeypatch.setenv("VR_HOTSPOTD_API_TOKEN", "secret")
    monkeypatch.setattr(apk_upload, "APK_UPLOAD_DIR", tmp_path)
    monkeypatch.setattr(apk_upload, "APK_MAX_BYTES", 3)
    monkeypatch.setattr(
        apk_upload,
        "execute_adb_operation",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("oversized upload executed ADB")
        ),
    )
    handler = _make_handler(b"four")

    handler.do_POST()
    payload = _response_json(handler)

    assert handler._last_code == 413
    assert payload["result_code"] == "invalid_request"
    assert payload["warnings"] == ["body_too_large"]
    assert not list(tmp_path.iterdir())


def test_apk_upload_requires_apk_filename(monkeypatch, tmp_path):
    monkeypatch.setenv("VR_HOTSPOTD_API_TOKEN", "secret")
    monkeypatch.setattr(apk_upload, "APK_UPLOAD_DIR", tmp_path)
    handler = _make_handler(b"apk")
    handler.headers.replace_header("X-VRhotspot-Apk-Name", "notes.txt")

    handler.do_POST()
    payload = _response_json(handler)

    assert handler._last_code == 400
    assert payload["warnings"] == ["apk_upload_filename_invalid"]
    assert not list(tmp_path.iterdir())

from vr_hotspotd.api import APIHandler
from vr_hotspotd.config import DEFAULT_CONFIG, validate_network_config


def _auth_handler(request_token):
    handler = APIHandler.__new__(APIHandler)
    handler._get_req_token = lambda: request_token
    return handler


def _network_config(**updates):
    config = DEFAULT_CONFIG.copy()
    config.update(updates)
    return config


def test_non_ascii_tokens_compare_without_exception(monkeypatch):
    monkeypatch.setenv("VR_HOTSPOTD_API_TOKEN", "sëcret")

    assert _auth_handler("sëcret")._is_authorized() is True
    assert _auth_handler("different-ë")._is_authorized() is False
    assert _auth_handler("crafted-ÿ-token")._is_authorized() is False


def test_surrogate_tokens_compare_without_exception(monkeypatch):
    configured_token = "secret-\udcff"
    monkeypatch.setenv("VR_HOTSPOTD_API_TOKEN", configured_token)

    assert _auth_handler(configured_token)._is_authorized() is True
    assert _auth_handler("secret-\udcfe")._is_authorized() is False


def test_ascii_token_behavior_is_unchanged(monkeypatch):
    monkeypatch.setenv("VR_HOTSPOTD_API_TOKEN", "configured-secret")

    assert _auth_handler("configured-secret")._is_authorized() is True
    assert _auth_handler("wrong-secret")._is_authorized() is False


def test_dot_only_adapter_names_are_rejected():
    for adapter in (".", ".."):
        assert "invalid_ap_adapter" in validate_network_config(
            _network_config(ap_adapter=adapter)
        )


def test_valid_dotted_adapter_name_is_accepted():
    assert validate_network_config(_network_config(ap_adapter="wlp2s0.1")) == []

import json

import pytest

from flatpak_client import HttpResponse, InvalidResponseError, LocalApiClient, LocalApiClientError
from flatpak_client.client import ResponseTooLargeError

BASE = "/v1/diagnostics/streaming"
CAPTURE_ID = "69f10238-01a6-4de6-83ce-53fc4b7ca52c"


class Transport:
    def __init__(self, response=None):
        self.requests = []
        self.response = response or HttpResponse(status=200, body=b'{"result_code":"ok","data":{}}')

    def send(self, request):
        self.requests.append(request)
        return self.response


@pytest.mark.parametrize("path, method, body", [
    (BASE, "GET", None), (BASE, "POST", {"duration_s": 120}),
    (BASE + "/mark", "POST", {"capture_id": CAPTURE_ID}),
    (BASE + "/stop", "POST", {"capture_id": CAPTURE_ID}),
    (BASE + "/report?capture_id=" + CAPTURE_ID, "GET", None),
])
def test_streaming_broker_reuses_loopback_transport_and_keeps_token_native(path, method, body):
    transport = Transport()
    client = LocalApiClient(token="private-native-token", transport=transport)
    client.portal_request(path, method=method, body=body)
    assert len(transport.requests) == 1
    request = transport.requests[0]
    assert request.url == "http://127.0.0.1:8732" + path
    assert request.method == method
    assert request.headers["X-Api-Token"] == "private-native-token"
    assert "private-native-token" not in request.url
    assert "private-native-token" not in repr(request)
    if body is not None:
        assert json.loads(request.body) == body


@pytest.mark.parametrize("path", [
    BASE + "/report?capture_id=bad", BASE + "/report?capture_id=",
    BASE + "/report?capture_id=" + CAPTURE_ID + "&extra=1",
    BASE + "/report?capture_id=" + CAPTURE_ID + "&capture_id=" + CAPTURE_ID,
    BASE + "/report?capture_id=" + CAPTURE_ID + "#fragment",
    BASE + "/report?capture_id=../../etc/passwd",
    BASE + "/report?capture_id=" + CAPTURE_ID.upper(),
    BASE + "/report-extra?capture_id=" + CAPTURE_ID,
    BASE + "/mark?capture_id=" + CAPTURE_ID,
    "https://example.invalid" + BASE + "/report?capture_id=" + CAPTURE_ID,
])
def test_broker_rejects_noncanonical_ambiguous_and_external_report_routes(path):
    transport = Transport()
    client = LocalApiClient(token="private-native-token", transport=transport)
    with pytest.raises(LocalApiClientError):
        client.portal_request(path, method="GET")
    assert transport.requests == []


@pytest.mark.parametrize("path, method, body", [
    (BASE + "/mark", "GET", None), (BASE + "/stop", "GET", None),
    (BASE, "DELETE", None), (BASE + "/report?capture_id=" + CAPTURE_ID, "POST", {}),
    (BASE, "GET", {"duration_s": 120}),
])
def test_broker_refuses_wrong_methods_and_get_bodies(path, method, body):
    transport = Transport()
    with pytest.raises(LocalApiClientError):
        LocalApiClient(token="private-native-token", transport=transport).portal_request(path, method=method, body=body)
    assert transport.requests == []


def test_broker_keeps_existing_response_size_guard():
    transport = Transport(HttpResponse(status=200, body=b"partial", body_truncated=True))
    with pytest.raises(ResponseTooLargeError):
        LocalApiClient(token="private-native-token", transport=transport).portal_request(
            BASE + "/report?capture_id=" + CAPTURE_ID, method="GET")


def test_broker_never_exposes_reflected_token_to_web_portal():
    transport = Transport(HttpResponse(status=200, body=b'{"secret":"private-native-token"}'))
    with pytest.raises(InvalidResponseError):
        LocalApiClient(token="private-native-token", transport=transport).portal_request(BASE, method="GET")

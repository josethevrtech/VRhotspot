from email.message import Message

from vr_hotspotd.api import APIHandler


def _handler_with_headers(headers):
    handler = APIHandler.__new__(APIHandler)
    msg = Message()
    for key, value in headers.items():
        msg[key] = value
    handler.headers = msg
    return handler


def test_get_req_token_prefers_x_api_token():
    handler = _handler_with_headers(
        {
            "X-Api-Token": "token-x",
            "Authorization": "Bearer token-bearer",
        }
    )
    assert handler._get_req_token() == "token-x"


def test_get_req_token_bearer():
    handler = _handler_with_headers({"Authorization": "Bearer token-bearer"})
    assert handler._get_req_token() == "token-bearer"

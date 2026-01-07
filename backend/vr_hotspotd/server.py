import ipaddress
import logging
import os
from http.server import ThreadingHTTPServer

from vr_hotspotd.api import APIHandler

log = logging.getLogger("vr_hotspotd.server")


def run_server():
    # Use the env vars that systemd sets for vr-hotspotd
    host = (os.environ.get("VR_HOTSPOTD_HOST") or "127.0.0.1").strip() or "127.0.0.1"
    port_raw = (os.environ.get("VR_HOTSPOTD_PORT") or "8732").strip() or "8732"
    try:
        port = int(port_raw)
    except Exception:
        port = 8732

    def _is_loopback(h: str) -> bool:
        h = (h or "").strip().lower()
        if h in ("127.0.0.1", "localhost", "::1"):
            return True
        try:
            return ipaddress.ip_address(h).is_loopback
        except Exception:
            return False

    if not _is_loopback(host) and not (os.environ.get("VR_HOTSPOTD_API_TOKEN") or "").strip():
        log.error("refusing to bind non-loopback without VR_HOTSPOTD_API_TOKEN")
        raise SystemExit(1)

    server = ThreadingHTTPServer((host, port), APIHandler)
    server.daemon_threads = True
    log.info("listening", extra={"bind": f"http://{host}:{port}"})
    server.serve_forever()

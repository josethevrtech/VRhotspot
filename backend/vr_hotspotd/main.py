import sys
import logging
import signal
import threading

from vr_hotspotd.server import build_server
from vr_hotspotd.logging import setup_logging
from vr_hotspotd.config import ensure_config_file
from vr_hotspotd.lifecycle import repair, stop_hotspot

log = logging.getLogger("vr_hotspotd.main")


def _install_signal_handlers(stop_event: threading.Event) -> None:
    def _handler(signum, _frame):
        if stop_event.is_set():
            return
        try:
            sig_name = signal.Signals(signum).name
        except Exception:
            sig_name = str(signum)
        log.info("shutdown_signal:%s", sig_name)
        stop_event.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, _handler)


def main():
    setup_logging()
    ensure_config_file()

    # Conservative crash recovery: if state says lnxrouter is running, ensure coherence.
    try:
        repair(correlation_id="boot")
    except Exception:
        log.exception("repair_on_boot_failed")

    server = build_server()
    stop_event = threading.Event()
    _install_signal_handlers(stop_event)

    server_thread = threading.Thread(
        target=server.serve_forever,
        name="vr-hotspotd-http",
        daemon=True,
    )
    server_thread.start()

    try:
        while server_thread.is_alive() and not stop_event.wait(0.5):
            pass
    finally:
        stop_event.set()
        try:
            server.shutdown()
        except Exception:
            log.exception("server_shutdown_failed")
        try:
            server.server_close()
        except Exception:
            log.exception("server_close_failed")
        try:
            server_thread.join(timeout=5)
        except Exception:
            log.exception("server_thread_join_failed")
        try:
            stop_hotspot(correlation_id="shutdown")
        except Exception:
            log.exception("stop_on_shutdown_failed")


if __name__ == "__main__":
    sys.exit(main())

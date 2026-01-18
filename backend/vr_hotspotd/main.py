import os
import sys
import logging
import signal
import threading

from vr_hotspotd.server import build_server
from vr_hotspotd.logging import setup_logging
from vr_hotspotd.config import ensure_config_file
from vr_hotspotd.lifecycle import repair, stop_hotspot
from vr_hotspotd import vendor_paths

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

    try:
        install_dir = os.environ.get("VR_HOTSPOT_INSTALL_DIR", "")
        vendor_root = str(vendor_paths._vendor_root())
        vendor_bins = ",".join(str(p) for p in vendor_paths.vendor_bin_dirs())
        vendor_libs = ",".join(str(p) for p in vendor_paths.vendor_lib_dirs())
        log.info(
            "vendor_root=%s vendor_bins=%s vendor_libs=%s install_dir=%s",
            vendor_root,
            vendor_bins or "none",
            vendor_libs or "none",
            install_dir or "none",
        )
    except Exception:
        log.exception("vendor_path_log_failed")

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

    # Autostart / Persistence Logic (Async)
    def _do_autostart():
        try:
            # Short sleep to let server settle / logs verify boot
            import time
            time.sleep(1.0)
            
            from vr_hotspotd.config import load_config
            from vr_hotspotd.lifecycle import start_hotspot
            cfg = load_config()
            if cfg.get("autostart"):
                log.info("autostart_enabled_starting_hotspot")
                start_hotspot(correlation_id="autostart")
        except Exception:
            log.exception("autostart_failed")

    try:
        autostart_thread = threading.Thread(target=_do_autostart, name="autostart-worker", daemon=True)
        autostart_thread.start()
    except Exception:
        log.exception("autostart_thread_launch_failed")

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

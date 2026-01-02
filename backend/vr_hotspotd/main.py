import sys
import logging

from vr_hotspotd.server import run_server
from vr_hotspotd.logging import setup_logging
from vr_hotspotd.config import ensure_config_file
from vr_hotspotd.lifecycle import repair

log = logging.getLogger("vr_hotspotd.main")


def main():
    setup_logging()
    ensure_config_file()

    # Conservative crash recovery: if state says lnxrouter is running, ensure coherence.
    try:
        repair(correlation_id="boot")
    except Exception:
        log.exception("repair_on_boot_failed")

    run_server()


if __name__ == "__main__":
    sys.exit(main())

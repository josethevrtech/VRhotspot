"""Canonical hotspot-at-boot configuration and systemd synchronization."""

from __future__ import annotations

import os
import subprocess
from typing import Callable, Sequence

from vr_hotspotd.config import load_config_snapshot, write_config_file


AUTOSTART_UNIT = "vr-hotspot-autostart.service"
AUTOSTART_ROLLBACK_FAILED = "autostart_rollback_failed_state_inconsistent"
_SYSTEMCTL_TIMEOUT_SECONDS = 15.0


class AutostartControlError(RuntimeError):
    """A fixed, non-command-output failure from autostart synchronization."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)

    def __repr__(self) -> str:
        return f"AutostartControlError(code={self.code!r})"


def _run_systemctl(argv: Sequence[str]):
    child_environment = os.environ.copy()
    child_environment.pop("VR_HOTSPOTD_API_TOKEN", None)
    return subprocess.run(
        list(argv),
        capture_output=True,
        check=False,
        env=child_environment,
        text=True,
        timeout=_SYSTEMCTL_TIMEOUT_SECONDS,
    )


def _systemctl_command(enabled: bool) -> tuple[str, ...]:
    if enabled:
        return ("systemctl", "enable", AUTOSTART_UNIT)
    return ("systemctl", "disable", "--now", AUTOSTART_UNIT)


def set_hotspot_autostart(
    enabled: bool,
    *,
    runner: Callable[[Sequence[str]], object] = _run_systemctl,
    config_loader: Callable[[], dict] = load_config_snapshot,
    config_writer: Callable[[dict], dict] = write_config_file,
) -> bool:
    """Synchronize the existing config key and installer-owned systemd unit."""

    if type(enabled) is not bool:
        raise AutostartControlError("invalid_autostart_value")

    try:
        previous = bool(config_loader().get("autostart", False))
    except Exception:
        raise AutostartControlError("autostart_config_read_failed") from None
    try:
        result = runner(_systemctl_command(enabled))
    except Exception:
        raise AutostartControlError("autostart_service_update_failed") from None

    if getattr(result, "returncode", None) != 0:
        raise AutostartControlError("autostart_service_update_failed")

    try:
        config_writer({"autostart": enabled})
    except Exception:
        try:
            rollback_result = runner(_systemctl_command(previous))
        except Exception:
            raise AutostartControlError(AUTOSTART_ROLLBACK_FAILED) from None
        if getattr(rollback_result, "returncode", None) != 0:
            raise AutostartControlError(AUTOSTART_ROLLBACK_FAILED) from None
        raise AutostartControlError("autostart_config_update_failed") from None
    return enabled

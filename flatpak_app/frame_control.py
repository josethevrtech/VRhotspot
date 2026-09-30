"""Optional user-session Frame Control bridge; never handles SSH or input data."""
from __future__ import annotations

from dataclasses import dataclass

SERVICE = "org.mainframeos.FrameControl"
OBJECT = "/Control"
INTERFACE = SERVICE


@dataclass(frozen=True)
class FrameControlState:
    status: str = "unavailable"

    @property
    def available(self) -> bool:
        return self.status in {"idle", "connecting", "controlling-frame"}

    @property
    def label(self) -> str:
        return {
            "idle": "Control Steam Frame",
            "connecting": "Cancel Frame Connection",
            "controlling-frame": "Return Control to Laptop",
        }.get(self.status, "Frame Control Service Unavailable")


class FrameControlBridge:
    """Fixed, bounded D-Bus calls to an already running desktop service.

    No service auto-start, privileged daemon API, host process spawning, headset
    credentials, target addresses or arbitrary D-Bus methods are accepted here.
    """

    def __init__(self, *, Gio, GLib):
        self._Gio = Gio
        self._GLib = GLib
        self.state = FrameControlState()

    def _call(self, method: str, signature: str):
        bus = self._Gio.bus_get_sync(self._Gio.BusType.SESSION, None)
        result = bus.call_sync(
            SERVICE, OBJECT, INTERFACE, method, None,
            self._GLib.VariantType.new(signature),
            self._Gio.DBusCallFlags.NO_AUTO_START, 500, None,
        )
        return result.unpack()

    def refresh(self) -> FrameControlState:
        try:
            value = self._call("Status", "(s)")
            state = FrameControlState(value[0]) if len(value) == 1 else FrameControlState()
            self.state = state if state.available else FrameControlState()
        except Exception:
            self.state = FrameControlState()
        return self.state

    def toggle(self) -> bool:
        if not self.refresh().available:
            return False
        try:
            if self._call("Toggle", "()") != ():
                raise ValueError("Unexpected toggle response")
            self.refresh()
            return True
        except Exception:
            self.state = FrameControlState()
            return False

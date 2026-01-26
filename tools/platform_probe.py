#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    backend_path = root / "backend"
    if str(backend_path) not in sys.path:
        sys.path.insert(0, str(backend_path))
    try:
        from vr_hotspotd.diagnostics.platform import collect_platform_matrix
    except Exception as exc:
        print(f"import_failed: {exc}")
        return 1

    try:
        matrix = collect_platform_matrix()
        os_info = matrix.get("os", {}) if isinstance(matrix, dict) else {}
        immutability = matrix.get("immutability", {}) if isinstance(matrix, dict) else {}
        integration = matrix.get("integration", {}) if isinstance(matrix, dict) else {}
        nm = integration.get("network_manager", {}) if isinstance(integration, dict) else {}
        pretty = os_info.get("pretty_name") or os_info.get("id") or "unknown_os"
        signal = immutability.get("signal") or ""
        active = nm.get("active")
        if isinstance(active, bool):
            active_label = "true" if active else "false"
        else:
            active_label = "unknown"
        print(f"{pretty} | {signal} | nm_active={active_label}")
        return 0
    except Exception as exc:
        print(f"probe_failed: {exc}")
        return 2


if __name__ == "__main__":
    sys.exit(main())

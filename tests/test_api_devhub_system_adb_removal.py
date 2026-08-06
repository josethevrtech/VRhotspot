from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEVHUB_API = ROOT / "backend/vr_hotspotd/devtools/devhub_api.py"


def test_tools_remove_dispatches_system_source_to_system_package_removal() -> None:
    source = DEVHUB_API.read_text(encoding="utf-8")

    assert 'request.get("source") == "system"' in source
    assert 'remove_system_platform_tools(adb_path=request.get("path"))' in source
    assert "remove_managed_platform_tools()" in source

import ast
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
FILES = (
    ROOT / "backend" / "vr_hotspotd" / "devtools" / "platform_tools.py",
    ROOT / "backend" / "vr_hotspotd" / "devtools" / "platform_tools_manager.py",
    ROOT / "backend" / "vr_hotspotd" / "devtools" / "devhub_api.py",
    ROOT / "backend" / "vr_hotspotd" / "devtools" / "apk_upload.py",
    ROOT / "backend" / "vr_hotspotd" / "devtools" / "adb_cli.py",
)


@pytest.mark.parametrize("path", FILES, ids=lambda path: path.name)
def test_developer_hub_modules_parse_as_python39(path):
    source = path.read_text(encoding="utf-8")

    ast.parse(source, filename=str(path), feature_version=(3, 9))

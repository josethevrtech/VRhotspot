"""USB product identity for display, never a substitute for radio capabilities."""
from pathlib import Path
import re

KNOWN_USB_NAMES = {"28de:2432": "Steam Frame USB"}


def read_attribute(path):
    try:
        return Path(path).read_text()[:256].strip()
    except (OSError, UnicodeError):
        return None


def usb_identity(device, reader=read_attribute):
    for parent in (Path(device), *Path(device).parents):
        try:
            vendor = str(reader(str(parent / "idVendor")) or "").strip().lower()
            product = str(reader(str(parent / "idProduct")) or "").strip().lower()
            if not re.fullmatch(r"[0-9a-f]{4}", vendor) or not re.fullmatch(r"[0-9a-f]{4}", product):
                continue
            usb_id = f"{vendor}:{product}"
            name = KNOWN_USB_NAMES.get(usb_id)
            if not name:
                # USB descriptors may be generic. Display what the device reports;
                # never infer a retail model from its chipset or vendor alone.
                value = str(reader(str(parent / "product")) or "")
                name = " ".join("".join(c for c in value if c.isprintable()).split())[:80] or None
            return {"usb_id": usb_id, "display_name": name}
        except (OSError, UnicodeError):
            continue
    return {"usb_id": None, "display_name": None}

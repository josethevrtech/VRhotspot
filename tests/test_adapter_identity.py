from vr_hotspotd.adapters.identity import usb_identity


def test_verified_id_wins_over_generic_product_descriptor():
    attrs = {"/usb/idVendor": "28de", "/usb/idProduct": "2432", "/usb/product": "802.11ax WLAN Adapter"}
    assert usb_identity("/usb/1-1:1.0", attrs.get) == {"usb_id": "28de:2432", "display_name": "Steam Frame USB"}


def test_other_devices_use_bounded_product_description_not_guessed_retail_model():
    attrs = {"/usb/idVendor": "0e8d", "/usb/idProduct": "7961", "/usb/product": "  Example\nUSB Adapter\x00 "}
    assert usb_identity("/usb", attrs.get)["display_name"] == "ExampleUSB Adapter"
    attrs["/usb/product"] = "x" * 300
    assert len(usb_identity("/usb", attrs.get)["display_name"]) == 80
    assert usb_identity("/internal", {}.get) == {"usb_id": None, "display_name": None}

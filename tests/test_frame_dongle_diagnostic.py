import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('dongle', ROOT / 'tools/diagnose_frame_dongle.py')
dongle = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dongle)


def device(tmp_path, vendor='28de', product='2432'):
    root = tmp_path / 'bus/usb/devices'
    dev = root / '3-2'
    dev.mkdir(parents=True)
    for key, value in [('idVendor', vendor), ('idProduct', product), ('speed', '5000')]:
        (dev / key).write_text(value)
    return root


def test_missing_driver_is_visible_without_netdev(tmp_path):
    device(tmp_path)
    result = dongle.inspect(tmp_path)
    assert result['devices'][0]['status'] == 'driver_not_ready'
    assert result['devices'][0]['interfaces'] == []


def test_exact_id_only(tmp_path):
    device(tmp_path, product='0001')
    assert dongle.inspect(tmp_path)['devices'] == []


def test_bound_device_preserves_dynamic_name_without_personal_data(tmp_path):
    root = device(tmp_path)
    iface = root / '3-2:1.0'
    (iface / 'net/wlx-test').mkdir(parents=True)
    driver = tmp_path / 'bus/usb/drivers/rtw89_8852cu'
    driver.mkdir(parents=True)
    (iface / 'driver').symlink_to(driver, target_is_directory=True)
    (root / '3-2/serial').write_text('PRIVATE-SERIAL')
    result = dongle.inspect(tmp_path)
    assert result['devices'][0]['drivers'] == ['rtw89_8852cu']
    assert result['devices'][0]['interfaces'] == ['wlx-test']
    assert result['devices'][0]['status'] == 'interface_available'
    assert 'PRIVATE-SERIAL' not in str(result)

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


def test_direct_link_reports_width_without_private_identifiers():
    result = dongle.parse_link(
        'Interface wlx-test\n type managed\n ssid PRIVATE-SSID\n'
        ' channel 37 (6135 MHz), width: 160 MHz, center1: 6185 MHz\n',
        'Connected to aa:bb:cc:dd:ee:ff (on wlx-test)\n SSID: PRIVATE-SSID\n'
        ' freq: 6135.0\n tx bitrate: 1921.5 MBit/s 160MHz HE-MCS 9\n')
    assert result['associated'] is True
    assert result['channel_width_mhz'] == 160
    assert result['tx_phy_mbps'] == 1921.5
    assert result['performance_qualified'] is False
    assert 'PRIVATE' not in str(result)
    assert 'aa:bb' not in str(result)


def test_ap_is_not_misreported_as_associated_client():
    result = dongle.parse_link(' type AP\n channel 36 (5180 MHz), width: 80 MHz\n',
                               'Not connected.\n')
    assert result['role'] == 'AP'
    assert result['associated'] is False
    assert result['frequency_mhz'] == 5180


def test_invalid_interface_cannot_become_command_option():
    assert dongle.inspect_link('--version') == {'error': 'invalid_interface_name'}

import os
import time
from pathlib import Path

from vr_hotspotd.lifecycle import collect_capture_logs


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_collect_capture_logs_prefers_config_dir(tmp_path: Path) -> None:
    capture_dir = tmp_path / "capture"
    conf_root = capture_dir / "lnxrouter_tmp"
    conf_a = conf_root / "lnxrouter.wlan0.conf.aaa"
    conf_b = conf_root / "lnxrouter.wlan0.conf.bbb"
    conf_a.mkdir(parents=True)
    conf_b.mkdir(parents=True)

    _write(conf_a / "hostapd.log", "a1\na2\n")
    _write(conf_b / "dnsmasq.log", "b1\n")

    logs = collect_capture_logs(
        capture_dir=str(capture_dir),
        lnxrouter_config_dir="/dev/shm/lnxrouter_tmp/lnxrouter.wlan0.conf.bbb",
        max_lines=20,
    )

    assert any("[dnsmasq.log] b1" in line for line in logs)
    assert not any("[hostapd.log] a1" in line for line in logs)


def test_collect_capture_logs_uses_newest(tmp_path: Path) -> None:
    capture_dir = tmp_path / "capture"
    conf_root = capture_dir / "lnxrouter_tmp"
    conf_old = conf_root / "lnxrouter.wlan0.conf.old"
    conf_new = conf_root / "lnxrouter.wlan0.conf.new"
    conf_old.mkdir(parents=True)
    conf_new.mkdir(parents=True)

    _write(conf_old / "hostapd.log", "old\n")
    _write(conf_new / "hostapd.log", "new\n")

    now = time.time()
    os.utime(conf_old, (now - 10, now - 10))
    os.utime(conf_new, (now, now))

    logs = collect_capture_logs(
        capture_dir=str(capture_dir),
        lnxrouter_config_dir=None,
        max_lines=20,
    )

    assert any("[hostapd.log] new" in line for line in logs)
    assert not any("[hostapd.log] old" in line for line in logs)

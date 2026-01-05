import vr_hotspotd.lifecycle as lifecycle


def test_cleanup_kills_dnsmasq_and_removes_conf_dir(tmp_path, monkeypatch):
    lnx_tmp = tmp_path / "lnxrouter_tmp"
    conf_dir = lnx_tmp / "lnxrouter.wlan0.conf.TEST"
    conf_dir.mkdir(parents=True)
    (conf_dir / "dnsmasq.pid").write_text("111")
    (conf_dir / "hostapd.pid").write_text("222")

    monkeypatch.setattr(lifecycle, "_LNXROUTER_TMP", lnx_tmp)
    monkeypatch.setattr(lifecycle, "_find_our_lnxrouter_pids", lambda: [333])
    monkeypatch.setattr(lifecycle, "_pid_running", lambda pid: pid in (111, 222, 333))

    def _fake_cmdline(pid: int) -> str:
        if pid == 111:
            return "dnsmasq --conf-file"
        if pid == 222:
            return "hostapd -c /dev/shm/lnxrouter_tmp/hostapd.conf"
        if pid == 333:
            return "lnxrouter --ap wlan0"
        return ""

    monkeypatch.setattr(lifecycle, "_pid_cmdline", _fake_cmdline)

    killed = []

    def _record_kill(pid: int, timeout_s: float = 3.0) -> None:
        killed.append(pid)

    monkeypatch.setattr(lifecycle, "_kill_pid", _record_kill)

    lifecycle._kill_runtime_processes("wlan0", stop_engine_first=False)
    assert sorted(killed) == [111, 222, 333]

    removed = lifecycle._remove_conf_dirs("wlan0")
    assert conf_dir.name in removed
    assert not conf_dir.exists()

from types import SimpleNamespace

from vr_hotspotd.diagnostics import clients


def test_clients_parsing_with_leases(tmp_path, monkeypatch):
    lease_file = tmp_path / "dnsmasq.leases"
    lease_file.write_text("123456 aa:bb:cc:dd:ee:ff 192.168.1.10 headset *\n")

    monkeypatch.setattr(clients, "load_config", lambda: {"dnsmasq_leases_file": str(lease_file)})

    output = (
        "192.168.1.10 dev wlan0 lladdr aa:bb:cc:dd:ee:ff REACHABLE\n"
        "192.168.1.20 dev wlan0 lladdr 11:22:33:44:55:66 STALE\n"
    )
    monkeypatch.setattr(
        clients,
        "subprocess",
        SimpleNamespace(
            run=lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout=output, stderr="")
        ),
    )

    res = clients.list_clients("wlan0")
    assert len(res) == 2
    assert res[0]["ip"] == "192.168.1.10"
    assert res[0]["mac"] == "aa:bb:cc:dd:ee:ff"
    assert res[0]["state"] == "REACHABLE"
    assert res[0]["hostname"] == "headset"
    assert res[1]["ip"] == "192.168.1.20"
    assert res[1]["state"] == "STALE"

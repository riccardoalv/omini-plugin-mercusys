import json

import pytest
from omini_sdk import PluginError

from omini_mercusys.collect import collect, mac, text
from omini_mercusys.collect import test as connection_test


def test_one_access_point_per_unit(halo, cfg):
    main, sat = collect(cfg)
    assert (main.key, main.role, main.vendor, main.model, main.host) == (
        "aa:bb:cc:00:00:01",
        "ap",
        "Mercusys",
        "Halo H60XS",
        "192.168.1.121",
    )
    assert main.name == "Living Room" and sat.name == "Escritório"  # nickname / custom name
    assert main.os_version == "1.2.0 Build 20260119"
    # CPU and memory are the main unit's.
    assert (main.cpu_pct, main.mem_pct) == (7.0, 46.0)
    assert sat.cpu_pct is None


def test_clients_on_the_unit_they_use(halo, cfg):
    main, sat = collect(cfg)
    [phone] = main.wireless_clients  # the offline one is left out
    assert (phone.mac, phone.band, phone.interface) == ("11:22:33:44:55:01", "5ghz", "5 GHz")
    assert phone.tx_rate_mbps == 2.0 and phone.rx_rate_mbps == 0.1
    assert [w.band for w in sat.wireless_clients] == ["2.4ghz"]
    # Wired clients: behind the unit's LAN port.
    assert [(f.mac, f.port) for f in sat.fdb] == [("11:22:33:44:55:03", "LAN")]
    # The names given in the app.
    names = {h.ip: h.hostnames for h in main.hosts + sat.hosts}
    assert names["192.168.1.185"] == ["moto-g86-5G"] and names["192.168.1.71"] == ["Robson"]


def test_session_is_reused_between_collections(halo, cfg):
    collect(cfg)
    collect(cfg)
    assert halo.logins == 1
    saved = json.loads((cfg.state_dir / "session.json").read_text())
    assert saved["session"]["stok"] == "stok1"
    assert "secret" not in json.dumps(saved)  # never the password itself


def test_signs_in_again_when_the_session_is_rejected(halo, cfg):
    collect(cfg)
    halo.stok = "rotated"  # e.g. the unit rebooted
    [main, _] = collect(cfg)
    assert halo.logins == 2 and main.model == "Halo H60XS"


def test_wrong_password(halo, cfg):
    cfg["password"] = "nope"
    with pytest.raises(PluginError, match=r"wrong password \(9 attempts left"):
        collect(cfg)


def test_only_reads(halo, cfg):
    collect(cfg)
    assert set(halo.calls) <= {"keys", "auth", "login", "device_list", "client_list", "performance"}


def test_connection_test(halo, cfg):
    assert connection_test(cfg) == "Connected: 2 unit(s) — Living Room, Escritório"


def test_keeps_the_last_answers(halo, cfg):
    collect(cfg)
    kept = sorted(p.name for p in (cfg.state_dir / "pages").iterdir())
    assert kept == ["client_list.json", "device_list.json", "performance.json"]


def test_helpers():
    assert mac("AA-BB-CC-00-00-01") == "aa:bb:cc:00:00:01" and mac("x") is None
    assert text("UGxheVN0YXRpb24=") == "PlayStation"
    assert text("plain name") == "plain name"

import json

import pytest
from omini_sdk import PluginError

from omini_mercusys.collect import collect, mac, text
from omini_mercusys.collect import test as connection_test


def test_one_access_point_per_unit(halo, cfg):
    main, sat = collect(cfg)
    assert (main.key, main.role, main.vendor, main.model, main.host) == (
        "30:16:9d:91:de:7f",
        "ap",
        "Mercusys",
        "Halo H60XR",
        "192.168.1.81",
    )
    assert (main.name, sat.name) == ("Living Room", "Bedroom")
    assert sat.os_version == "1.2.0 Build 20260119 Rel. 51031"
    # CPU and memory are the unit Omini talks to (cfg: 192.168.1.121).
    assert (sat.cpu_pct, sat.mem_pct) == (3.0, 35.0)
    assert main.cpu_pct is None
    # Wired backhaul: found through the switches, no mesh link drawn.
    assert sat.neighbors is None


def test_clients_on_the_unit_they_use(halo, cfg):
    main, sat = collect(cfg)
    assert len(main.wireless_clients) == 3 and len(sat.wireless_clients) == 6
    assert {w.band for w in sat.wireless_clients} == {"2.4ghz", "5ghz"}
    labels = {w.interface for w in main.wireless_clients + sat.wireless_clients}
    assert "Casa-Visitantes · 2.4 GHz" in labels  # the guest network by its name
    assert all(":" in w.mac for w in sat.wireless_clients)
    # Traffic (bytes/s from the unit → bits/s); no link rate, the unit has none.
    assert all(w.rx_bps is not None and w.tx_rate_mbps is None for w in sat.wireless_clients)
    # The names given in the app, for the map.
    assert all(h.hostnames and h.sources == ["mercusys"] for h in sat.hosts)


def test_wireless_backhaul_links_the_satellite_to_its_unit(halo, cfg):
    units = halo.answers["device_list"]["device_list"]
    units[1]["connection_type"] = ["band5"]
    _, sat = collect(cfg)
    [link] = sat.neighbors
    assert (link.remote_mac, link.remote_port, link.protocol) == (
        "30:16:9d:91:de:7f",
        "5 GHz",
        "other",
    )


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
    assert halo.logins == 2 and main.model == "Halo H60XR"


def test_wrong_password(halo, cfg):
    cfg["password"] = "nope"
    with pytest.raises(PluginError, match=r"wrong password \(9 attempts left"):
        collect(cfg)


def test_only_reads(halo, cfg):
    collect(cfg)
    assert set(halo.calls) <= {
        "keys",
        "auth",
        "login",
        "device_list",
        "client_list",
        "wlan",
        "performance",
    }
    assert halo.calls.count("client_list") == 3  # one per unit, and the full list


def test_connection_test(halo, cfg):
    assert connection_test(cfg) == "Connected: 2 unit(s) — Living Room, Bedroom"


def test_keeps_the_last_answers(halo, cfg):
    collect(cfg)
    kept = sorted(p.name for p in (cfg.state_dir / "pages").iterdir())
    assert kept == [
        "client_list_30169d91de7f.json",
        "client_list_30169da6b324.json",
        "device_list.json",
        "login.json",
        "performance.json",
        "wlan_names.json",
    ]
    assert "stok" not in (cfg.state_dir / "pages" / "login.json").read_text()


def test_helpers():
    assert mac("AA-BB-CC-00-00-01") == "aa:bb:cc:00:00:01" and mac("x") is None
    assert text("UGxheVN0YXRpb24=") == "PlayStation"
    assert text("plain name") == "plain name"


def test_waits_after_a_refused_login(halo, cfg):
    cfg["password"] = "nope"
    with pytest.raises(PluginError, match="wrong password"):
        collect(cfg)
    with pytest.raises(PluginError, match="waiting a few minutes"):
        collect(cfg)
    assert halo.calls.count("login") == 1  # the unit is not asked again
    # The right password is tried at once.
    cfg["password"] = "secret"
    assert collect(cfg)


def test_unknown_band_has_no_label(halo, cfg):
    unit = next(k for k in halo.answers if k.startswith("client_list_"))
    halo.answers[unit]["client_list"][0]["connection_type"] = "unknown"
    devices = collect(cfg)
    clients = [w for d in devices for w in d.wireless_clients or []]
    assert all(w.interface != "unknown" for w in clients)
    assert any(w.band is None and w.interface is None for w in clients)


def test_client_traffic_in_bits_per_second(halo, cfg):
    unit = next(k for k in halo.answers if k.startswith("client_list_"))
    first = halo.answers[unit]["client_list"][0]
    first["down_speed"], first["up_speed"] = 250_000, 12_500
    devices = collect(cfg)
    w = next(w for d in devices for w in d.wireless_clients or [] if w.rx_bps == 2_000_000)
    assert w.tx_bps == 100_000


def test_wifi_network_names_never_passwords(halo, cfg):
    devices = collect(cfg)
    clients = [w for d in devices for w in d.wireless_clients or []]
    labels = {w.interface for w in clients}
    # "Casa · 5 GHz", and the guest network by its own name.
    assert "Casa · 5 GHz" in labels and "Casa-Visitantes · 2.4 GHz" in labels
    assert {w.ssid for w in clients} <= {"Casa", "Casa-Visitantes"}
    # The answer carries the Wi-Fi passwords: none is ever written.
    for f in cfg.state_dir.rglob("*"):
        if f.is_file():
            body = f.read_text()
            assert "segredo" not in body and "c2VncmVkbw" not in body, f


def test_without_network_names_the_band_is_enough(halo, cfg):
    del halo.answers["wlan"]
    devices = collect(cfg)
    labels = {w.interface for d in devices for w in d.wireless_clients or []}
    assert labels <= {"2.4 GHz", "5 GHz", "guest", None}


def test_a_client_no_unit_lists_goes_to_the_main_unit(halo, cfg):
    halo.unlisted = [
        {
            "mac": "F4-B3-01-00-00-01",
            "ip": "192.168.1.71",
            "name": "Um9ic29u",
            "online": True,
            "wire_type": "wireless",
            "connection_type": "band5",
            "interface": "main",
            "access_host": "1",
        }
    ]
    main, sat = collect(cfg)
    [robson] = [w for w in main.wireless_clients if w.mac == "f4:b3:01:00:00:01"]
    assert robson.interface == "Casa · 5 GHz"
    assert not any(w.mac == "f4:b3:01:00:00:01" for w in sat.wireless_clients)

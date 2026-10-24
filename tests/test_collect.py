import json

import httpx
import pytest
from omini_sdk import PluginError

from omini_mercusys.collect import collect, mac, pct, text
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
    assert saved["data"]["session"]["stok"] == "stok1"
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


def test_usage_is_a_fraction():
    # The unit reports CPU and memory as fractions (0.35 is 35 %), so 1 is a
    # full unit; a value out of that scale is not guessed as a percentage.
    assert pct(0.35) == 35.0 and pct(0.01) == 1.0 and pct(1) == 100.0
    assert pct(35) is None and pct(-0.1) is None
    assert pct(None) is None and pct("x") is None


def test_usage_out_of_scale_is_left_out(halo, cfg):
    halo.answers["performance"] = {"cpu_usage": 1, "mem_usage": 40}
    _, sat = collect(cfg)
    assert (sat.cpu_pct, sat.mem_pct) == (100.0, None)


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


def test_a_client_without_a_band_is_not_reported(halo, cfg):
    unit = next(k for k in halo.answers if k.startswith("client_list_"))
    first = halo.answers[unit]["client_list"][0]
    first["connection_type"] = "unknown"
    devices = collect(cfg)
    wifi = [w for d in devices for w in d.wireless_clients or []]
    assert all(w.band for w in wifi)
    gone = first["mac"].lower().replace("-", ":")
    assert gone not in {w.mac for w in wifi}
    # Not reported at all: neither Wi-Fi nor cable (a name would place it
    # under the unit).
    assert gone not in {h.mac for d in devices for h in d.hosts or []}


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


def test_the_wifi_networks_on_the_unit_at_the_address(halo, cfg):
    main, sat = collect(cfg)
    # One per SSID, whatever its bands; the guest network is on in 2.4 GHz.
    assert [(w.ssid, w.enabled, w.vlan, w.security) for w in sat.wifi_networks] == [
        ("Casa", True, None, None),
        ("Casa-Visitantes", True, None, None),
    ]
    assert main.wifi_networks is None  # the mesh's networks, reported once
    # Read once an hour too: the next round has them from the state folder.
    assert [w.ssid for w in collect(cfg)[1].wifi_networks] == ["Casa", "Casa-Visitantes"]
    assert halo.calls.count("wlan") == 1


def test_a_wifi_network_off_in_every_band_is_off(halo, cfg):
    halo.answers["wlan"]["band2_4"]["guest"]["enable"] = False
    halo.answers["wlan"]["band5_1"]["host"].pop("enable")
    [casa, guest] = collect(cfg)[1].wifi_networks
    assert (casa.enabled, guest.enabled) == (True, False)
    halo.answers["wlan"]["band2_4"]["host"].pop("enable")
    (cfg.state_dir / "networks.json").unlink()
    assert collect(cfg)[1].wifi_networks[0].enabled is None  # never said


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


def test_network_names_are_read_once_an_hour(halo, cfg):
    collect(cfg)
    collect(cfg)
    assert halo.calls.count("wlan") == 1


def test_a_read_that_times_out_is_tried_again(halo, cfg):
    import httpx

    real = halo.handler
    slow = {"left": 1}

    def handler(request):
        if request.url.params.get("form") == "device_list" and slow["left"]:
            slow["left"] -= 1
            raise httpx.ReadTimeout("slow", request=request)
        return real(request)

    halo.handler = handler
    assert collect(cfg)


def test_an_answer_cut_short_is_asked_again(halo, cfg, monkeypatch):
    import httpx

    import omini_mercusys.client as client_module

    monkeypatch.setattr(client_module, "RETRY_PAUSE_S", 0)

    real = halo.handler
    cut = {"left": 2}

    def handler(request):
        if request.url.params.get("form") == "device_list" and cut["left"]:
            cut["left"] -= 1
            raise httpx.RemoteProtocolError(
                "peer closed connection without sending complete message body", request=request
            )
        return real(request)

    halo.handler = handler
    assert collect(cfg)
    assert cut["left"] == 0

    cut["left"] = 3  # three times in a row: the round fails, with the reason
    with pytest.raises(PluginError, match="complete message body"):
        collect(cfg)


def test_a_client_of_unknown_connection_is_not_reported(halo, cfg):
    unit = next(k for k in halo.answers if k.startswith("client_list_"))
    first = halo.answers[unit]["client_list"][0]
    first["wire_type"], first["connection_type"] = "unknown", None
    devices = collect(cfg)
    gone = first["mac"].lower().replace("-", ":")
    seen = {w.mac for d in devices for w in d.wireless_clients or []}
    seen |= {f.mac for d in devices for f in d.fdb or []}
    seen |= {h.mac for d in devices for h in d.hosts or []}
    assert gone not in seen


def test_a_slow_answer_is_asked_again(halo, cfg, monkeypatch):
    from functools import partial

    import httpx

    import omini_mercusys.client as client_module
    import omini_mercusys.collect as collect_module

    monkeypatch.setattr(client_module, "RETRY_PAUSE_S", 0)
    timeouts = {"left": 2}  # two reads in a row time out: the third try answers

    def handler(request):
        if request.url.params.get("form") != "login" and timeouts["left"]:
            timeouts["left"] -= 1
            raise httpx.ReadTimeout("slow", request=request)
        return halo.handler(request)

    monkeypatch.setattr(
        collect_module,
        "Client",
        partial(client_module.Client, transport=httpx.MockTransport(handler)),
    )
    assert collect(cfg)
    assert timeouts["left"] == 0


def test_a_refused_connection_is_tried_again(halo, cfg, monkeypatch):
    from functools import partial

    import httpx

    import omini_mercusys.client as client_module
    import omini_mercusys.collect as collect_module

    monkeypatch.setattr(client_module, "RETRY_PAUSE_S", 0)
    refused = {"login": 1, "read": 1}  # the login and one read are refused once each

    def handler(request):
        kind = "login" if request.url.params.get("form") == "login" else "read"
        if refused[kind]:
            refused[kind] -= 1
            raise httpx.ConnectError("Connection refused", request=request)
        return halo.handler(request)

    monkeypatch.setattr(
        collect_module,
        "Client",
        partial(client_module.Client, transport=httpx.MockTransport(handler)),
    )
    assert collect(cfg)
    assert refused == {"login": 0, "read": 0}


def test_a_unit_that_keeps_refusing_fails(cfg, monkeypatch):
    from functools import partial

    import httpx

    import omini_mercusys.client as client_module
    import omini_mercusys.collect as collect_module

    monkeypatch.setattr(client_module, "RETRY_PAUSE_S", 0)

    def handler(request):
        raise httpx.ConnectError("Connection refused", request=request)

    monkeypatch.setattr(
        collect_module,
        "Client",
        partial(client_module.Client, transport=httpx.MockTransport(handler)),
    )
    with pytest.raises(PluginError, match="cannot connect"):
        collect(cfg)


def test_waits_as_long_as_asked(cfg, tmp_path):
    from omini_sdk import Config

    from omini_mercusys.collect import wait_s

    assert wait_s(cfg) == 60  # the default
    assert wait_s(Config({"timeout_s": 90}, tmp_path)) == 90
    assert wait_s(Config({"timeout_s": 2}, tmp_path)) == 5
    assert wait_s(Config({"timeout_s": 600}, tmp_path)) == 120


@pytest.mark.parametrize(
    ("reply", "error"),
    [
        (lambda form: httpx.Response(403) if form == "login" else None, "another device"),
        (lambda form: httpx.Response(200, json={}) if form == "keys" else None, "does not look"),
        (lambda form: httpx.Response(500) if form == "login" else None, "answered 500"),
    ],
)
def test_only_a_refused_password_waits_before_signing_in_again(halo, cfg, reply, error):
    # A unit that is busy, not a Mercusys or has another admin signed in did
    # not refuse the credentials: the next collection signs in at once.
    real = halo.handler
    broken = {"on": True}

    def handler(request):
        answer = reply(request.url.params.get("form")) if broken["on"] else None
        return answer or real(request)

    halo.handler = handler
    with pytest.raises(PluginError, match=error):
        collect(cfg)
    broken["on"] = False
    assert collect(cfg)


def test_wired_clients_are_on_the_units_lan_port(halo, cfg):
    unit = next(k for k in halo.answers if k.startswith("client_list_"))
    halo.answers[unit]["client_list"][0]["wire_type"] = "wired"
    devices = collect(cfg)
    [wired] = [d for d in devices if d.fdb]
    assert {f.port for f in wired.fdb} == {"LAN"}
    # The port the MAC table names exists, with nothing else said about it.
    [lan] = wired.interfaces
    assert lan.model_dump(exclude_none=True) == {"name": "LAN", "type": "ethernet"}
    # A unit without wired clients gets no port it cannot describe.
    assert all(d.interfaces is None for d in devices if not d.fdb)


def test_manifest_matches_what_is_read():
    from pathlib import Path

    manifest = (Path(__file__).parent.parent / "plugin.yaml").read_text()
    # Every field but the address and password is advanced.
    fields = manifest.split("\nfields:", 1)[1].split("\n  - key: ")[1:]
    groups = {f.split("\n", 1)[0]: "group: Advanced" in f for f in fields}
    assert groups == {
        "host": False,
        "password": False,
        "username": True,
        "verify_tls": True,
        "timeout_s": True,
    }
    # The unit reports each client's traffic, not its link rate.
    assert "rates" not in manifest.split("\nfields:", 1)[0]


def test_the_mesh_s_radios_on_the_main_unit(halo, cfg):
    main, sat = collect(cfg)
    assert [r.model_dump(exclude_none=True) for r in main.radios] == [
        {"name": "2.4 GHz", "band": "2.4ghz", "channel": 6},
        {"name": "5 GHz", "band": "5ghz", "channel": 36},
    ]
    assert sat.radios is None  # the mesh's channels, reported once
    # Kept with the network names: the next round has them too.
    assert [r.channel for r in collect(cfg)[0].radios] == [6, 36]
    assert halo.calls.count("wlan") == 1


@pytest.mark.parametrize("value", [0, "0", "auto"])
def test_an_automatic_channel(halo, cfg, value):
    halo.answers["wlan"]["band5_1"]["host"]["channel"] = value
    radio = collect(cfg)[0].radios[1]
    assert (radio.name, radio.channel, radio.auto) == ("5 GHz", None, True)


def test_a_channel_on_the_band_and_one_unknown(halo, cfg):
    wlan = halo.answers["wlan"]
    wlan["band2_4"]["channel"] = 11
    wlan["band5_1"]["host"]["channel"] = "dfs?"
    wlan["band5_2"] = {"host": {"ssid": "Q2FzYQ==", "enable": True, "channel": 149}}
    assert [r.model_dump(exclude_none=True) for r in collect(cfg)[0].radios] == [
        {"name": "2.4 GHz", "band": "2.4ghz", "channel": 11},
        {"name": "5 GHz", "band": "5ghz"},
        {"name": "5 GHz-2", "band": "5ghz", "channel": 149},
    ]


def test_a_satellite_on_wifi_has_a_wireless_uplink(halo, cfg):
    main, sat = collect(cfg)
    assert (main.wireless_uplink, sat.wireless_uplink) == (None, False)
    halo.answers["device_list"]["device_list"][1]["connection_type"] = ["band5"]
    assert collect(cfg)[1].wireless_uplink is True


def test_an_older_cache_is_read_again(halo, cfg):
    collect(cfg)
    saved = json.loads((cfg.state_dir / "networks.json").read_text())
    del saved["radios"]
    (cfg.state_dir / "networks.json").write_text(json.dumps(saved))
    assert collect(cfg)[0].radios
    assert halo.calls.count("wlan") == 2


def test_declares_no_change():
    from pathlib import Path

    assert "changes" not in (Path(__file__).parent.parent / "plugin.yaml").read_text()


def test_a_busy_unit_is_asked_again(halo, cfg, monkeypatch):
    import omini_mercusys.client as client_module

    monkeypatch.setattr(client_module, "RETRY_PAUSE_S", 0)
    real = halo.handler
    busy = {"left": 1}

    def handler(request):
        if request.url.params.get("form") == "device_list" and busy["left"]:
            busy["left"] -= 1
            return httpx.Response(503)
        return real(request)

    halo.handler = handler
    assert collect(cfg) and busy["left"] == 0


def test_a_refused_password_is_kept_apart_from_the_session(halo, cfg):
    collect(cfg)
    cfg["password"] = "nope"
    with pytest.raises(PluginError, match="wrong password"):
        collect(cfg)
    refused = json.loads((cfg.state_dir / "login-refused.json").read_text())
    assert "nope" not in json.dumps(refused) and "wrong password" in refused["error"]
    cfg["password"] = "secret"
    collect(cfg)  # a good login forgets it
    assert not (cfg.state_dir / "login-refused.json").exists()


def test_a_session_saved_by_an_older_version_is_replaced(halo, cfg):
    cfg.state_dir.mkdir(parents=True, exist_ok=True)
    (cfg.state_dir / "session.json").write_text(
        json.dumps({"password_hash": "x", "session": {"stok": "old"}})
    )
    collect(cfg)
    assert halo.logins == 1


def test_an_optional_read_the_unit_lacks_is_skipped(halo, cfg):
    del halo.answers["performance"]  # answered "no such callback"
    main, sat = collect(cfg)
    assert main.cpu_pct is None and sat.cpu_pct is None

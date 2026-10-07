"""Turns the mesh's answers into Omini devices: one access point per unit."""

from __future__ import annotations

import base64
import re
from typing import Any

from omini_sdk import Config, Device, FdbEntry, Neighbor, PluginError, WirelessClient

from omini_mercusys.client import Client

try:  # Host (client names) is in every SDK that runs this plugin; kept optional
    from omini_sdk.models import Host
except ImportError:  # pragma: no cover
    Host = None  # type: ignore[assignment,misc]

BANDS = {
    "band2_4": "2.4ghz",
    "band5": "5ghz",
    "band5_1": "5ghz",
    "band5_2": "5ghz",
    "band6": "6ghz",
}
BAND_NAMES = {"2.4ghz": "2.4 GHz", "5ghz": "5 GHz", "6ghz": "6 GHz"}
HEX12 = re.compile(r"^[0-9a-f]{12}$")


def client_from(cfg: Config) -> Client:
    host, password = cfg.str("host"), cfg.str("password")
    if not host or not password:
        raise PluginError("the address and password are required")
    return Client(
        host,
        cfg.str("username", "admin") or "admin",
        password,
        verify_tls=cfg.bool("verify_tls", False),
        state_dir=cfg.state_dir,
    )


def mac(value: Any) -> str | None:
    raw = re.sub(r"[^0-9a-f]", "", str(value or "").lower())
    return ":".join(raw[i : i + 2] for i in range(0, 12, 2)) if HEX12.match(raw) else None


def text(value: Any) -> str | None:
    """Names come base64-encoded (UTF-8); older firmware sends them plain."""
    if not value or not isinstance(value, str):
        return None
    try:
        decoded = base64.b64decode(value, validate=True).decode("utf-8")
        if decoded.isprintable():
            return decoded.strip() or None
    except ValueError:
        pass
    return value.strip() or None


def pct(value: Any) -> float | None:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return round(v * 100 if v <= 1 else v, 1)


def bps(value: Any) -> int | None:
    """Client traffic comes in bytes per second."""
    try:
        return round(float(value) * 8)
    except (TypeError, ValueError):
        return None


# Current traffic per client (rx_bps/tx_bps) is in the SDK of Omini 0.4 and later.
TRAFFIC = "rx_bps" in WirelessClient.model_fields


def unit_name(d: dict[str, Any]) -> str | None:
    return (
        text(d.get("custom_nickname"))
        or (d.get("nickname") or "").replace("_", " ").title()
        or None
    )


def build(
    units: list[dict[str, Any]],
    clients_by_unit: dict[str, list[dict[str, Any]]],
    perf: dict[str, Any],
    host: str,
) -> list[Device]:
    """``clients_by_unit``: each unit's clients (by its MAC); ``perf`` is the
    CPU and memory of the unit Omini talks to (``host``)."""
    devices = []
    for u in units:
        m = mac(u.get("mac"))
        if not m:
            continue
        model = u.get("device_model") or u.get("model")
        if model and not model.lower().startswith(("halo", "deco")):
            model = f"Halo {model}" if str(u.get("device_type", "")).startswith("MER") else model
        mine = [c for c in clients_by_unit.get(m, []) if c.get("online", True) is not False]
        wifi, fdb, hosts = [], [], []
        for c in mine:
            cm = mac(c.get("mac"))
            if not cm:
                continue
            kind = str(c.get("wire_type", "")).lower()
            if kind == "wired":
                fdb.append(FdbEntry(mac=cm, port="LAN"))
            elif kind == "wireless":
                band = BANDS.get(str(c.get("connection_type") or ""))
                label = BAND_NAMES.get(band or "", "")  # nothing when the band is unknown
                if c.get("interface") and c["interface"] != "main":
                    label = f"{label} ({c['interface']})".strip()
                # The unit reports each client's traffic, not its link rate.
                traffic = (
                    {"rx_bps": bps(c.get("down_speed")), "tx_bps": bps(c.get("up_speed"))}
                    if TRAFFIC
                    else {}
                )
                wifi.append(WirelessClient(mac=cm, interface=label or None, band=band, **traffic))
            name = text(c.get("name"))
            if Host is not None and c.get("ip") and name:
                hosts.append(Host(ip=c["ip"], mac=cm, hostnames=[name], sources=["mercusys"]))
        # A satellite linked by Wi-Fi hangs from the unit it uses; one linked
        # by cable is found through the switches like any wired device.
        neighbors = None
        backhaul = u.get("connection_type") or []
        parent = mac(u.get("previous"))
        if parent and parent != m and backhaul and "wired" not in backhaul:
            band = BANDS.get(str(backhaul[0]), "")
            neighbors = [
                Neighbor(
                    local_port="Wi-Fi backhaul",
                    protocol="other",
                    remote_mac=parent,
                    remote_port=BAND_NAMES.get(band),
                )
            ]
        here = u.get("device_ip") == host
        devices.append(
            Device(
                key=m,
                name=unit_name(u) or model or m,
                host=u.get("device_ip") or None,
                role="ap",
                vendor="Mercusys" if str(u.get("device_type", "")).startswith("MER") else None,
                model=model,
                os_version=u.get("software_ver") or None,
                cpu_pct=pct(perf.get("cpu_usage")) if here else None,
                mem_pct=pct(perf.get("mem_usage")) if here else None,
                macs=[m],
                ips=[u["device_ip"]] if u.get("device_ip") else None,
                wireless_clients=wifi or None,
                fdb=fdb or None,
                hosts=hosts or None,
                neighbors=neighbors,
            )
        )
    return devices


def collect(cfg: Config) -> list[Device]:
    c = client_from(cfg)
    try:
        units = c.read("/admin/device", "device_list").get("device_list") or []
        c.keep("device_list", units)
        if not units:
            raise PluginError("signed in, but the unit listed no mesh units")
        # Each unit's clients ("default" lists them all without saying where).
        by_unit: dict[str, list[dict[str, Any]]] = {}
        for u in units:
            m = mac(u.get("mac"))
            if not m:
                continue
            answer = c.read("/admin/client", "client_list", {"device_mac": u["mac"]})
            by_unit[m] = answer.get("client_list") or []
            c.keep(f"client_list_{m.replace(':', '')}", by_unit[m])
        try:
            perf = c.read("/admin/network", "performance")
            c.keep("performance", perf)
        except PluginError:
            perf = {}  # optional on some firmware
        host = c.base.split("://", 1)[-1].split("/")[0].split(":")[0]
        return build(units, by_unit, perf, host)
    finally:
        c.close()


def test(cfg: Config) -> str:
    c = client_from(cfg)
    try:
        units = c.read("/admin/device", "device_list").get("device_list") or []
        names = [unit_name(u) or u.get("device_model") or "?" for u in units]
        return f"Connected: {len(units)} unit(s) — {', '.join(names)}" if units else "Connected"
    finally:
        c.close()

"""Turns the mesh's answers into Omini devices: one access point per unit."""

from __future__ import annotations

import base64
import re
from typing import Any

from omini_sdk import Config, Device, FdbEntry, PluginError, WirelessClient

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


def rate_mbps(value: Any) -> float | None:
    """Client rates come in bytes per second."""
    try:
        return round(float(value) * 8 / 1e6, 2)
    except (TypeError, ValueError):
        return None


def unit_name(d: dict[str, Any]) -> str | None:
    return (
        text(d.get("custom_nickname"))
        or (d.get("nickname") or "").replace("_", " ").title()
        or None
    )


def build(
    units: list[dict[str, Any]], clients: list[dict[str, Any]], perf: dict[str, Any]
) -> list[Device]:
    by_unit: dict[str, list[dict[str, Any]]] = {}
    main = next((mac(u.get("mac")) for u in units if u.get("role") == "master"), None)
    for c in clients:
        owner = mac(c.get("access_host")) or main
        if owner:
            by_unit.setdefault(owner, []).append(c)

    devices = []
    for u in units:
        m = mac(u.get("mac"))
        if not m:
            continue
        model = u.get("device_model") or u.get("model")
        mine = [c for c in by_unit.get(m, []) if c.get("online", True) is not False]
        wifi, fdb, hosts = [], [], []
        for c in mine:
            cm = mac(c.get("mac"))
            if not cm:
                continue
            if str(c.get("wire_type", "")).lower() == "wired":
                fdb.append(FdbEntry(mac=cm, port="LAN"))
            else:
                band = BANDS.get(str(c.get("connection_type") or ""))
                wifi.append(
                    WirelessClient(
                        mac=cm,
                        interface=BAND_NAMES.get(band or "", c.get("connection_type")) or None,
                        band=band,
                        tx_rate_mbps=rate_mbps(c.get("down_speed")),
                        rx_rate_mbps=rate_mbps(c.get("up_speed")),
                    )
                )
            name = text(c.get("name"))
            if Host is not None and c.get("ip") and name:
                hosts.append(Host(ip=c["ip"], mac=cm, hostnames=[name], sources=["mercusys"]))
        is_main = u.get("role") == "master"
        devices.append(
            Device(
                key=m,
                name=unit_name(u) or model or m,
                host=u.get("device_ip") or None,
                role="ap",
                vendor="Mercusys",
                model=model,
                os_version=u.get("software_ver") or None,
                cpu_pct=pct(perf.get("cpu_usage")) if is_main else None,
                mem_pct=pct(perf.get("mem_usage")) if is_main else None,
                macs=[m],
                ips=[u["device_ip"]] if u.get("device_ip") else None,
                wireless_clients=wifi or None,
                fdb=fdb or None,
                hosts=hosts or None,
            )
        )
    return devices


def collect(cfg: Config) -> list[Device]:
    c = client_from(cfg)
    try:
        units = c.read("/admin/device", "device_list").get("device_list") or []
        c.keep("device_list", units)
        clients = (
            c.read("/admin/client", "client_list", {"device_mac": "default"}).get("client_list")
            or []
        )
        c.keep("client_list", clients)
        try:
            perf = c.read("/admin/network", "performance")
            c.keep("performance", perf)
        except PluginError:
            perf = {}  # optional on some firmware
        if not units:
            raise PluginError("signed in, but the unit listed no mesh units")
        return build(units, clients, perf)
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

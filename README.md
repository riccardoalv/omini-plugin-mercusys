# omini-plugin-mercusys

[Omini](https://github.com/riccardoalv/omini) plugin for **Mercusys Halo** mesh systems (tested with the Halo H60X), through the units' local web interface. TP-Link Deco units use the same interface and may work too.

## What it reads

| Data | Used for |
|---|---|
| Every unit of the mesh: name, model, firmware, IP, MAC | One access point per unit on the map |
| Clients of each unit: Wi-Fi band and current traffic, or wired | Each phone, laptop or TV under the unit it uses |
| Client names given in the Mercusys app | Names on the map |
| Wi-Fi network names (main and guest) | Which network each client uses ("Home · 5 GHz"); the passwords in the same answer are never kept |
| Wi-Fi networks of the mesh: one per SSID (its bands and the main/guest pair merged by name), on or off; no VLAN nor security (the answer gives none). Reported on the unit at the configured address, read once an hour | The Wi-Fi networks of the network |
| CPU and memory of the unit at the configured address | Device panel |
| The mesh's radios: one per band of the Wi-Fi answer (2.4 GHz, 5 GHz, 5 GHz-2, 6 GHz) with the channel it is set to (`channel` of the band or of its main network; `0` or `auto` is automatic). Reported on the main unit, read once an hour with the network names | Wi-Fi channel conflicts (Omini never changes them: the plugin is read-only) |
| Whether a satellite reaches the mesh over Wi-Fi (its backhaul, `connection_type`, is not `wired`); unknown on the main unit | A mesh unit is never moved to another channel |

**Read-only:** besides signing in, the plugin only sends `read` operations. It never changes a setting, reboots or blocks a client.

## Install

In Omini: **Integrations → Add → Plugin store → +** and paste `https://github.com/riccardoalv/omini-plugin-mercusys`. Then add the integration with the main unit's address and the web interface password (the one you use at `https://<address>`).

## Notes

- The web interface uses its own encryption (RSA for the password, AES for every request): the plugin implements it; nothing is sent in clear text besides over HTTPS.
- Signing in from Omini may sign you out of the unit's web interface in your browser. The plugin keeps its session between collections (in its state folder) and only signs in again when the unit asks for it.
- **Not verified on hardware:** the channel's place in the Wi-Fi answer (`channel` on the main network of each band, as the H60X shows it, or on the band itself) and `0`/`auto` for an automatic channel. The width, transmit power and whether a radio is off are not in that answer, so they stay empty; the radios are the whole mesh's, so they are reported once, on the main unit.
- The last answers read are kept in the plugin's state folder (`<data>/plugins/mercusys/state/<integration>/pages`) to help support other models.

## Development

```bash
uv run pytest -q          # tests (the SDK comes from ../omini/sdk/python)
uv run ruff check . && uv run ruff format --check .
```

The protocol follows the community [ha-tplink-deco](https://github.com/amosyuen/ha-tplink-deco) integration (MIT).

## License

MIT

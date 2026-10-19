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

## Requirements

- **Omini 1.7 or later** (the plugin uses the SDK's HTTP client, login state and optional reads).
- A Mercusys Halo (or TP-Link Deco with the same local web interface) reachable over HTTPS.

## Form fields

| Field | Meaning |
|---|---|
| Address | The main unit's IP address (any unit of the mesh usually works), e.g. `192.168.1.121`; `https://` is added |
| Password | The password of the web interface |
| Username (Advanced) | `admin`: Mercusys and Deco sign in with a password only |
| Verify the TLS certificate (Advanced) | Off by default: the units use a self-signed certificate |
| Connection timeout (Advanced) | Seconds to wait for each answer (60 by default, 5 to 120): the units are slow now and then |

## Install

In Omini: **Integrations → Add → Plugin store → +** and paste `https://github.com/riccardoalv/omini-plugin-mercusys`. Then add the integration with the main unit's address and the web interface password (the one you use at `https://<address>`).

## Notes

- The web interface uses its own encryption (RSA for the password, AES for every request): the plugin implements it; nothing is sent in clear text besides over HTTPS.
- Signing in from Omini may sign you out of the unit's web interface in your browser. The plugin keeps its session between collections (`session.json` in its state folder, readable only by Omini, tied to a hash of the address and credentials; a file saved by an older version is ignored, costing one login) and only signs in again when the unit asks for it.
- After the unit refuses the password, the plugin waits 15 minutes before signing in again with the same one (the unit locks the login after a few failed attempts); it is kept in `login-refused.json`, cleared by a good login, and a new password is tried at once. A unit out of reach, busy or with another admin signed in is asked again on the next collection.
- A refused connection, or a busy unit (HTTP 429 or 503), is asked again twice (after 2 then 4 seconds, or the unit's `Retry-After`), the login too; a read that times out or is cut short is asked again twice, the login never.
- An optional answer (the Wi-Fi networks, CPU and memory) the unit lacks or fails to give is skipped and logged once.
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

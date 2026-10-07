# omini-plugin-mercusys

[Omini](https://github.com/riccardoalv/omini) plugin for **Mercusys Halo** mesh systems (tested with the Halo H60X), through the units' local web interface. TP-Link Deco units use the same interface and may work too.

## What it reads

| Data | Used for |
|---|---|
| Every unit of the mesh: name, model, firmware, IP, MAC | One access point per unit on the map |
| Clients of each unit: Wi-Fi band and rates, or wired | Each phone, laptop or TV under the unit it uses |
| Client names given in the Mercusys app | Names on the map |
| CPU and memory of the main unit | Device panel |

**Read-only:** besides signing in, the plugin only sends `read` operations. It never changes a setting, reboots or blocks a client.

## Install

In Omini: **Integrations → Add → Plugin store → +** and paste `https://github.com/riccardoalv/omini-plugin-mercusys`. Then add the integration with the main unit's address and the web interface password (the one you use at `https://<address>`).

## Notes

- The web interface uses its own encryption (RSA for the password, AES for every request): the plugin implements it; nothing is sent in clear text besides over HTTPS.
- Signing in from Omini may sign you out of the unit's web interface in your browser. The plugin keeps its session between collections (in its state folder) and only signs in again when the unit asks for it.
- The last answers read are kept in the plugin's state folder (`<data>/plugins/mercusys/state/<integration>/pages`) to help support other models.

## Development

```bash
uv run pytest -q          # tests (the SDK comes from ../omini/sdk/python)
uv run ruff check . && uv run ruff format --check .
```

The protocol follows the community [ha-tplink-deco](https://github.com/amosyuen/ha-tplink-deco) integration (MIT).

## License

MIT

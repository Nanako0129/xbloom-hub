# xbloom-hub

A self-hosted hub for the xBloom Studio pour-over coffee machine: one service
that holds the machine's Bluetooth link and puts a dashboard, a console, an MCP
endpoint for AI assistants, and Home Assistant on top of it.

> **Status: planning.** Nothing here runs yet. The Bluetooth path described
> below has not been tested against a machine.

## Planned shape

```
Claude Code ── MCP ──┐                      ┌──► Dashboard / console (web)
                     ▼                      │
                 ┌──────────────────────────┴─┐
                 │ xbloom-hub (one container) │──► Home Assistant
                 └─────────────┬──────────────┘
                               │ the machine's single BLE link
                               ▼
                         xBloom Studio
```

- The machine accepts one Bluetooth connection at a time, so one process owns
  it and everything else is a client of that process.
- The container needs no Bluetooth hardware: the radio is an ESPHome Bluetooth
  proxy on the network (an ESP32, or an Android phone running a proxy app).
- An AI assistant writes a recipe for a given coffee and loads it onto the
  machine. Loading never starts a brew; starting is a separate, explicit step.

## Built on

- [xbloom-py](https://github.com/Alshekhi/xbloom-py) — BLE protocol, recipe
  validation, cloud API.
- [bleak-esphome](https://github.com/Bluetooth-Devices/bleak-esphome) — Bleak
  backend for ESPHome Bluetooth proxies.
- [xbloom-agent](https://github.com/denull0/xbloom-agent) — the MCP tool
  surface and brewing reference this project starts from.
- [xbloom-ble](https://github.com/Janczykkkko/xbloom-ble) — protocol
  documentation and the load/start safety model.
- [xbloom-studio](https://github.com/Alshekhi/xbloom-studio) — the Home
  Assistant integration whose event model this follows.

## Disclaimer

Unofficial. Not affiliated with, authorized, or endorsed by xBloom; the name is
used only to say which machine this talks to. It speaks a reverse-engineered
protocol that a firmware update can break, and it controls an appliance that
dispenses near-boiling water. Use at your own risk; see [LICENSE](LICENSE).

## License

MIT

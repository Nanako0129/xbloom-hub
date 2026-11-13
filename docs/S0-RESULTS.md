# S0 results — the radio path works

Run on 2026-11-14 against one xBloom Studio, firmware `V12.0D.500`, through
an Android phone running homeassistant-mobile-ble-proxy v2026.08.3
(reports itself as ESPHome `2026.8.0-dev-android`), Noise-encrypted.

Client versions: xbloom-py 0.5.0, bleak 3.1.0, bleak-esphome 4.1.0,
aioesphomeapi 46.30.0, habluetooth 7.1.2, bleak-retry-connector 4.7.1.

## Acceptance

| | Criterion | Result |
|---|---|---|
| a | Firmware string and idle state through the proxy | `V12.0D.500`, activity `1`, mode `pro` |
| b | Recipe prompt seen on the machine, then back at home | Seen (grinder `--`, dose `5`, start `▶`); exit was the machine's own sleep; next session reported activity `1` |
| c | Gate log: 8100, then 8102, 8104, 8004, nothing else | Exactly that. Each load step was echoed once by the machine |
| d | 10-minute idle hold | No disconnect. 20 windows of 30 s, 281–286 notifications each (last window partial) |
| e | Notes | Below |

No start opcode was sent and no brew started.

## Measurements

- Device found 1.0–4.0 s after the proxy connects; session ready 1.9–3.2 s
  after that (three runs).
- About 9.5 notifications per second while connected: weight frames at
  roughly 5 per second, plus a second periodic frame.
- Activity code: `29` then `1` right after the handshake; `31` once a recipe
  is prepared; `10` after five minutes idle. Notifications continue in `10`.
- The 10-minute hold covers 30-second windows only. Frames carry no sequence
  number that was checked, so this shows no gap that long, not zero loss.

## What S0 changed in the plan's assumptions

1. **The machine has no power button.** The planned exit from the armed
   state does not exist. Exits that do: the machine's own sleep (observed),
   pulling the plug, or the app's quit-recipe command 8017 (still unverified
   on hardware). The hub needs a software cancel, so 8017 gets its own
   attended test in S1.
2. **Entering the recipe screen tares the scale.** Weight read 132 g with
   dripper and cup before the load and 0 g after, with nothing moved, and
   stayed 0 g in the next session. A cup check by weight has to read the
   scale before the load.
3. **Weight frames keep arriving while a recipe is armed.**
4. **`bleak.BleakScanner` finds nothing through the proxy outside Home
   Assistant.** habluetooth's base manager does not feed the scanner
   wrapper (`does not implement _discover_service_info`). The device has to
   be taken from the proxy scanner's own
   `discovered_devices_and_advertisement_data`; connecting through
   `HaBleakClientWrapper` then works.
5. **The phone reports no advertisements while its screen is locked**
   unless the device is a managed target with lock-screen scanning (not yet
   configured or tested). An established link did survive the screen
   turning off during the hold.
6. **The gate sits where planned:** a subclass of
   `habluetooth.wrappers.HaBleakClientWrapper` overriding `write_gatt_char`,
   swapped into `bleak.BleakClient` and `bleak_retry_connector.BleakClient`
   after `BluetoothManager.async_setup()` and before any connection.
7. **Ranges** (xbloom-py `spec.py`): grind 1–80, Omni dose 5–18 g, pour
   temperature 20–98 °C (20 and 98 are the app's RT and BP), ratio 1:5–1:25
   in 0.5 steps, up to 9 pours of 0–240 ml.

## Not tested

Remote start, a real brew, the software cancel, lock-screen scanning,
reconnect after the proxy or the machine restarts, and the Mac's own
Bluetooth as a fallback path.

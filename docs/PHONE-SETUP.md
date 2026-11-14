# Android phone as the Bluetooth radio

The hub reaches the machine through an Android phone running
[homeassistant-mobile-ble-proxy](https://github.com/Zen3515/homeassistant-mobile-ble-proxy)
(v2026.08.3 here). This is the configuration that was set up and checked on
2026-11-14 on an Android 11 phone.

## Settings in the proxy app

| Setting | Value | Why |
|---|---|---|
| ESPHome API encryption key | keep the generated key | Without it anyone on the network can drive the machine through the phone. The hub refuses to run without the key |
| mDNS advertise interface | Disabled | The hub connects by IP address; nothing else should discover the proxy |
| Managed Target Devices | one entry: the machine's MAC address and its exact advertised name, with **Lock-Screen Scanning** on | Android gives an unfiltered scan nothing while the screen is locked; a scan filtered to an exact device keeps working |
| Auto start on boot | On | The proxy comes back after the phone restarts |
| Battery mode | Unrestricted | So Android does not stop the service |

The machine's name and address are shown by any BLE scanner app; the name
looks like `XBLOOM` followed by digits.

## Checked

With the screen off (`mWakefulness=Dozing`) the proxy reported exactly one
device, the machine, for a 30-second scan. A hub started in that state reached
`idle` in 8 seconds. Before the managed target was added, the same scan with
the screen off reported nothing.

Not checked: a restart of the phone, and many hours with the screen off.

## Doing it over adb

`tools/phone_ui.py` lists the elements on the phone's current screen and finds
one by its exact text, so the app's settings pages can be read and driven with
`adb shell input tap`. It only reads; anything shaped like a key is masked.
The app's own key field is already shown as dots.

```console
$ python3 tools/phone_ui.py                  # what is on screen
$ python3 tools/phone_ui.py find "Save"      # centre of that element
$ adb shell input tap 908 210
```

Coordinates depend on the phone, so read them from `phone_ui.py` each time
rather than keeping a list. `adb shell input keyevent 223` turns the screen
off for a lock-screen test.

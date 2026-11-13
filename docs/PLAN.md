# Plan

Status: revision 3 (2026-11-14). Plan review sent back revision 1 (no
load-only mechanism named, exit from the armed state undefined, no security
review) and revision 2 (a prepared recipe can be started from the machine's
own knob, so "no water" had no physical backing; the write gate's layer and
install time were not fixed). All five are addressed here.

Line references are to xbloom-py 0.5.0 (`src/xbloom/`).

## Envelope

**Outcome.** One Python service in one container that

1. holds the machine's only BLE link, through an ESPHome Bluetooth proxy
   (an idle Android phone running
   [homeassistant-mobile-ble-proxy](https://github.com/Zen3515/homeassistant-mobile-ble-proxy)),
2. stores and validates recipes,
3. serves MCP (for Claude Code), and REST + Server-Sent Events (for a web
   dashboard and console),
4. publishes brew state to Home Assistant.

**Stack.** Python 3.12, uv. `xbloom-py` (protocol, validation),
`bleak-esphome` + `aioesphomeapi` (radio), the official `mcp` SDK >= 1.23.0
mounted in one Starlette app with the REST/SSE routes, uvicorn. Recipes are
YAML files, brew history is stdlib `sqlite3`, both on a `/data` volume. The
dashboard is static HTML and one static JS file, `EventSource`, no build step.
All four libraries pinned to exact versions.

**Non-goals.** Multi-user or OAuth, cloud hosting, a custom Android app, a
terminal UI, firmware flashing (`xbloom.ota` is never imported), replacing the
Home Assistant integration, MQTT command topics.

**Owners.** The main session plans, integrates and accepts. The user does the
physical steps at the machine.

**Rollback.** Everything is new code in this repository. S0 is a throwaway
script outside the repository.

**Stops.**
- S0 fails on the proxy path twice: pause, put the alternatives to the user
  (Mac-native bridge, ESP32, own Android bridge).
- Any step that would dispense water with nobody at the machine: stop.

## Safety invariants

These come from the security review below; the IDs point at its findings.

1. **One write gate (P1-1).** Every GATT write goes through one function. It
   decodes the frame's command code (`ble.frame_command_code`, `ble.py:776`)
   and permits only the codes its caller's path needs:
   - load: 8100, 8102, 8104, 8001, 8004
   - safe controls: 40518 (pause), 40519 (stop), 8017, 8012–8014, 8022
   - start 8002 and resume 40524: only behind the start gate (invariant 3)

   Anything else, and any frame it cannot decode, is refused with
   `raise` — never `assert`, which `python -O` removes. Writes go only to the
   command characteristic `FFE1_UUID`.
2. **Loading never starts a brew.** Load uses
   `XBloomModeListener.send_prepare` (`mode_listener.py:211`), which sends
   frames 2–4 of `build_brew_frames` (8102, 8104, 8001 or 8004) over the held
   session. `brew()`, `send_brew()` and `send_start()` are never called on the
   load path; `brew()` is never called at all.
3. **Start gate (P1-5).** A load returns a one-time ticket bound to a hash of
   the recipe. Start must present it; the ticket is consumed on first use,
   expires after 10 minutes (configurable), and is void if since the load the
   machine left the recipe screen, started brewing, or received another load.
   Start is a REST endpoint with a confirm step in the dashboard and is not an
   MCP tool unless an explicit setting enables it.
4. **Every recipe is validated before load (P2-4)** with
   `normalize_recipe` + `validate_recipe`, whatever its source; any error
   rejects it. The encoder clamps out-of-range values instead of refusing
   them, so validation is the only check.
5. **The proxy's Noise key is mandatory (P1-2).** The hub always passes
   `noise_psk` and has no plaintext fallback. Without the key, anyone on the
   LAN can drive the machine through the phone and bypass the hub entirely.
6. **A "release link" control** lets the phone app take the machine.

## Security review (2026-11-14)

Read-only review of this envelope. No P0. Dispositions:

| ID | Finding | Disposition |
|---|---|---|
| P1-1 | The guard listed two forbidden codes, used `assert`, and sat on the load path only; 40524, 8021, 3500, 4506 and slot writes also move water or a motor | FIX — invariant 1; test feeds every `CMD_*` value and a non-`58` frame to each path |
| P1-2 | Plaintext 6053 lets any LAN host control the machine | FIX — invariant 5; from S0 on |
| P1-3 | A web page in a LAN browser, or DNS rebinding, can reach REST and MCP; the MCP SDK's rebinding protection is off when bound to 0.0.0.0 | FIX in S1/S2 — bearer token only in the `Authorization` header (no cookie, no query string, no CORS middleware); `TrustedHostMiddleware` over the whole app; reject a present-but-foreign `Origin`; token required on all of `/mcp`; GET never mutates |
| P1-4 | A recipe name reaching `innerHTML` can read the token and start a brew | FIX in S3 — `textContent` only; links must start with `https://`; CSP `default-src 'self'; script-src 'self'; object-src 'none'; base-uri 'none'` |
| P1-5 | Start executes whatever was prepared last and can fire twice | FIX — invariant 3. Cup check by scale weight: DEFER until S0 shows whether weight is reported on the recipe screen |
| P2-1 | Tokens in env files show in `docker inspect` | FIX in S4 — Compose `secrets` file, mode 600, git-ignored; `hmac.compare_digest`; browser receives the token once in a URL fragment and keeps it in localStorage; SSE is read-only and unauthenticated |
| P2-2 | "Off for MCP" does not bind an agent that can read the token file | ACCEPT — invariant 3 applies to every caller; document that hub calls and the secrets directory stay out of auto-allowed permissions |
| P2-3 | The session auto-disconnects after 300 s idle; Home Assistant may adopt the proxy from mDNS | FIX in S1 — set the idle timeout deliberately; connect by IP, mDNS advertising off in the app |
| P2-4 | Encoder clamps bad values | FIX — invariant 4 |
| P2-5 | YAML and file names | FIX in S1 — `yaml.safe_load`; file names from a `[a-z0-9-]` slug; parameterised SQL |
| P2-6 | Untrusted text in MCP results | FIX in S2 — labelled JSON fields, length cap, control characters stripped |
| P2-7 | Cloud account token | DEFER to S6 — not needed for share links or community recipes |
| P2-8 | MQTT command topics | FIX — publish only (non-goal above) |
| P3/P4 | Plain HTTP on the LAN; hub reachable on other Wi-Fi; BLE open when the hub is not connected; server-side image fetch; token rotation; debug logs with frame hex | ACCEPT / note in docs; never fetch community image URLs server-side; library logs at INFO |

Unverified, to be measured: whether the machine enforces cup presence;
whether weight is reported on the recipe screen; how `aioesphomeapi` behaves
against a plaintext proxy when a key is given.

## Slices

Each ends with an observed check, on the machine unless stated.

| ID | Outcome | Observed by |
|---|---|---|
| S0 | The radio path works with this machine and firmware | script log + user at the machine |
| S1 | Hub core: session manager with the write gate, recipe store, REST + SSE, simulator backend, tests | load on simulator and machine |
| S2 | MCP tools and a Claude Code skill from the ported brewing reference | a recipe written in Claude Code appears on the machine |
| S3 | Dashboard and console page: live curve, event log, manual load / start / cancel | user watches one real brew in a browser |
| S4 | Dockerfile and compose | one brew driven through the container |
| S5 | Home Assistant: state published over MQTT (method reconfirmed with the user then) | entities change during a brew |
| S6 | Optional: xBloom account sync, brew rating fed back into recipe revision | decided later |

## S1 — hub core

Written after S0; see [S0-RESULTS.md](S0-RESULTS.md) for the facts it rests
on.

**Outcome.** A hub process, run natively with `uv run`, that holds the link
through the proxy, keeps a recipe library, loads a stored recipe onto the
machine over REST, and streams machine state over SSE. No start endpoint, no
MCP, no dashboard, no container.

**Files.** `pyproject.toml`; `src/xbloom_hub/` with

| Module | Does |
|---|---|
| `gate.py` | Invariant 1. `make_gated(base)` as proven in S0; allow-list for S1 is load (8100, 8102, 8104, 8001, 8004) plus 8017. Start and resume codes are not in any S1 list |
| `radio.py` | Proxy connection (`noise_psk` required, no plaintext path), device lookup from the proxy scanner's table, reconnect with backoff. A local-Bluetooth option for development |
| `machine.py` | Owns the one `XBloomModeListener`. States `offline / idle / armed / asleep`; reads weight before a load; fans events out to subscribers; `release()` and `acquire()` for invariant 6; idle timeout set explicitly (P2-3) |
| `sim.py` | A fake with the same interface as `machine.py`, for tests and later UI work |
| `recipes.py` | YAML files under the data directory; `yaml.safe_load`; file name from a `[a-z0-9-]` slug; `normalize_recipe` + `validate_recipe` on every write and every load (invariant 4, P2-5) |
| `app.py` | Starlette. `GET /api/state`, `GET /api/events` (SSE), `GET/PUT/DELETE /api/recipes/{slug}`, `POST /api/machine/load`, `POST /api/machine/cancel`, `POST /api/link/release`, `POST /api/link/acquire` |

**Security in this slice (P1-3, P2-1).** Every non-GET request needs
`Authorization: Bearer` matching a token read from a file named by an
environment variable, compared with `hmac.compare_digest`; no cookie, no
query-string token, no CORS middleware. `TrustedHostMiddleware` with a
configured host list. A request whose `Origin` is present and not the hub's
own is refused. GET never changes anything. A load returns the one-time start
ticket of invariant 3 so the contract is fixed now; nothing consumes it until
the start endpoint exists.

**Tests (no hardware).** The gate against every `CMD_*` constant in
`xbloom.ble` and an undecodable frame; recipe slugs that try to leave the data
directory; 401 without the token, 400 for a foreign `Host`, 403 for a foreign
`Origin`; load and cancel against the simulator; an invalid recipe is refused
before any write.

**Observed on the machine (user present).**

1. `curl` loads a stored recipe; the machine shows it; the SSE stream reports
   the armed state and the weight read before the load.
2. **Cancel experiment:** `POST /api/machine/cancel` sends 8017. Same harmless
   recipe and physical precondition as S0 (grinder off, 25 ml, room
   temperature, no beans, dripper and empty cup). Pass: the machine returns to
   home and the next status says so. If 8017 is refused, unanswered, or does
   anything else: record it, let the machine sleep out as in S0, and remove
   8017 from the allow-list.
3. Restart the proxy app; the hub reconnects without being restarted.
4. `release`, connect with the official app, close it, `acquire`.

**Non-goals.** Start, pause, resume, stop; MCP; dashboard; Docker; Home
Assistant; cloud; lock-screen scanning on the phone.

**Rollback.** New files only.

## S0 — prove the radio path

Done 2026-11-14: [S0-RESULTS.md](S0-RESULTS.md). The text below is the slice
as reviewed, kept unedited; its "power button" exit turned out not to exist.

**Outcome.** Evidence that phone proxy → bleak-esphome → xbloom-py → machine
works, and that the load-only path of invariant 2 behaves as read, before any
hub code is written. The script sends no start opcode. Once a recipe is
prepared the machine can still be started from its own knob (`ble.py:706`),
so step 4 is arranged so that an accidental start is harmless.

**Scope.** One throwaway script (about 150 lines), run with `uv run --with`
on the Mac, outside this repository.

**Prerequisites (user).** Proxy app v2026.08.3 installed on the phone,
permissions granted, battery unrestricted, proxy started, phone on the LAN
next to the machine, official xBloom app closed. The phone's address and the
app's API encryption key reach the script through environment variables; the
key is never printed or written to a file in a repository.

**Steps.**

1. Offline, nothing is written to the machine: read `ble.py`,
   `mode_listener.py`, and the installed `bleak-esphome` / `habluetooth`
   sources to settle (i) how a held session is built from an externally
   supplied `BLEDevice` (`ble.py:1125`) and (ii) which class
   `bleak.BleakClient` resolves to on the proxy path. Record the parameter
   ranges from `spec.py`.
2. **Install the write gate before any connection.** xbloom-py looks up
   `bleak.BleakClient` at connect time (`ble.py:1213`) and writes the
   handshake straight to it (`mode_listener.py:286`, `:324`), as does
   `send_live` (`:165`); none of these pass through `write_confirmed`. So the
   gate is a subclass of whatever `bleak.BleakClient` resolves to after
   `habluetooth` is set up, overriding `write_gatt_char`, and it replaces
   `bleak.BleakClient` before the session is created. It permits only
   8100, 8102, 8104, 8001, 8004 on `FFE1_UUID`, raises on anything else or on
   an undecodable frame, and logs every code it passes.
3. Connect to the proxy with the Noise key; the script exits if the key
   variable is empty. List advertisements; find the device named `XBLOOM*`.
4. Open a held session (this sends the handshake, 8100). Print the firmware
   string (`RD_MachineInfo.fw_version`, `ble.py:1020`) and the machine state.
   **The script stops here unless the gate's log shows 8100** — that is the
   proof that the direct-write path goes through the gate.
5. With the user at the machine, load one recipe with `send_prepare`.
   `brew()`, `send_brew()` and `send_start()` do not appear in the script.
   - **Recipe:** grinder off (sent as 8004), the smallest volume and lowest
     temperature validation accepts.
   - **Physical precondition:** no beans in the machine; dripper and an empty
     cup in place. If the knob is pressed by mistake, the worst case is a
     small pour of lukewarm water into the cup.
   - **Exit:** the user switches the machine off with its power button, then
     on again. Power-off cannot start a brew. The script sends nothing; 8017
     is marked "not yet seen on the machine" (`ble.py:410`) and is not tried.
     If the user knows the machine's own back gesture they may use it
     instead, and which one was used is recorded.
   - **If the machine does not return to home** after power-on: stop S0,
     record the screen and the status frames, and do not continue to step 6.
   - **If a brew does start:** the user stops it at the machine; the script
     still sends nothing (40519 is outside S0's gate). Record it.
   - While armed, note whether scale-weight frames arrive.
6. Hold the link idle for 10 minutes with the idle timeout overridden; count
   disconnects and reconnects.

**Fallback.** If step 3 or 4 fails, run the same script on the Mac's own
Bluetooth to separate "library or firmware" from "proxy path".

**Acceptance.**

- (a) Firmware string and idle state printed through the proxy path.
- (b) The user sees the recipe prompt on the machine. The note records which
  exit was used (power button or back gesture), that the machine is back at
  home as seen by the user, and the status frame that follows.
- (c) The gate's log shows 8100 first, then 8102, 8104 and 8004, and nothing
  else.
- (d) Notification frames for the 10-minute hold: drops counted and reported.
- (e) A note with connect time, the settled ranges, whether weight is
  reported while armed, and the class and method the gate wrapped.

**Non-goals.** Hub code, container, dashboard, Home Assistant, remote start.

**Budget.** Two attempts on the proxy path.

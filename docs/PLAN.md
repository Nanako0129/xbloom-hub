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

Done 2026-11-14: [S1-RESULTS.md](S1-RESULTS.md).

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
| `radio.py` | Proxy connection to an IP address from configuration, no mDNS lookup (P2-3); `noise_psk` required, no plaintext path; device lookup from the proxy scanner's table; reconnect with backoff. A local-Bluetooth option for development |
| `machine.py` | Owns the one `XBloomModeListener`. States `offline / idle / armed / asleep / released`; caches weight from `on_raw` so it is known before a load; fans events out to subscribers; idle timeout set explicitly (P2-3). The library does not reconnect, so on lifecycle `failed` it calls `start()` again — except in `released`, which is how `release()` keeps the hub from taking the link back (invariant 6). **Gate check on every connect and reconnect, proxy and local:** the session is not usable (no `idle`, no load, no cancel) unless its client is an instance of the gated class and the gate's first logged code for that session is 8100. Cancel (8017) is accepted only in `armed`, and a refused or unanswered 8017 is returned as an error |
| `sim.py` | A fake with the same interface as `machine.py`, for tests and later UI work |
| `recipes.py` | YAML files under the data directory; `yaml.safe_load`; file name from a `[a-z0-9-]` slug; `normalize_recipe` + `validate_recipe` on every write and every load (invariant 4, P2-5) |
| `app.py` | Starlette. `GET /api/state`, `GET /api/events` (SSE), `GET/PUT/DELETE /api/recipes/{slug}`, `POST /api/machine/load`, `POST /api/machine/cancel`, `POST /api/link/release`, `POST /api/link/acquire` |

**Security in this slice (P1-3, P2-1).** Every non-GET request needs
`Authorization: Bearer` matching a token read from a file named by an
environment variable, compared with `hmac.compare_digest`; no cookie, no
query-string token, no CORS middleware. `TrustedHostMiddleware` with a
configured host list. A request whose `Origin` is present and not the hub's
own is refused. GET never changes anything. **Auth fails closed:** the hub
refuses to start when the token variable is unset or the file is missing or
empty after stripping whitespace, and an empty presented token is always 401.
A load returns the one-time start ticket of invariant 3 so the contract is
fixed now; nothing consumes it until the start endpoint exists, and it never
appears in `GET /api/state` or the SSE stream.

**Tests (no hardware).** The gate against every `CMD_*` constant in
`xbloom.ble` and an undecodable frame; a session whose client is not the gated
class stays out of `idle`/`armed` and refuses load; recipe slugs that try to
leave the data directory; the process exits on a missing or empty token file;
401 without the token and for `Authorization: Bearer ` with an empty token,
400 for a foreign `Host`, 403 for a foreign `Origin`; load and cancel against
the simulator, cancel refused outside `armed`; an invalid recipe is refused
before any write.

**Prerequisites (user).** As S0, plus: mDNS advertising set to disabled in
the proxy app (P2-3); phone screen on for the whole run, since the proxy
reports no advertisements while locked.

**Observed on the machine (user present).** Every step that loads uses the S0
recipe (grinder off, 5 g, one 25 ml pour at 20 °C) and the S0 physical
precondition (no beans, dripper and empty cup in place), so a knob press by
mistake pours 25 ml of unheated water.

1. `curl` loads that recipe; the machine shows it; the SSE stream reports the
   armed state and the weight read before the load. **Exit:** step 2.
2. **Cancel experiment:** `POST /api/machine/cancel` sends 8017. Pass: the
   machine returns to home and the hub reports activity `1`. If 8017 is
   refused, unanswered, or does anything else: record it, remove 8017 from
   the allow-list, and exit the armed state by waiting for the machine's own
   sleep — releasing the link first if it does not sleep while the hub holds
   it — with pulling the plug as the last resort.
3. Only once the hub reports activity `1`: restart the proxy app; the hub
   reconnects without being restarted, and its log shows the gate passing
   8100 on the new session.
4. Only once the hub reports activity `1`: `release`, connect with the
   official app, close it, `acquire`.

The run note records activity `1` before steps 3 and 4, and that mDNS
advertising was off. Reconnecting with the phone screen locked is not covered
and stays unproven.

**Non-goals.** Start, pause, resume, stop; MCP; dashboard; Docker; Home
Assistant; cloud; lock-screen scanning on the phone.

**Rollback.** New files only.

## S2 — Claude Code

Written after S1; see [S1-RESULTS.md](S1-RESULTS.md) and
[OFFICIAL-APP-NOTES.md](OFFICIAL-APP-NOTES.md).

**Outcome.** From Claude Code, an assistant can design a recipe for a given
coffee, save it to the hub's library, and load it onto the machine, using MCP
tools served by the hub and a skill that carries the brewing knowledge and the
machine's real limits. It cannot start a brew.

**MCP endpoint.** The official `mcp` SDK, pinned to an exact version at or
above 1.23.0, serving streamable HTTP at `/mcp` inside the existing Starlette
app. It sits behind the same `TrustedHostMiddleware` and `Guard`, with one
change to `Guard`: **every request under `/mcp` needs the bearer token,
whatever its method** (P1-3; elsewhere GET stays open because it only reads).
The SDK's own DNS-rebinding protection is off when bound to anything but
localhost, so the hub's middleware is the control and a test proves it covers
`/mcp`.

**Tools.** Thin wrappers over what S1 already has:

| Tool | Does |
|---|---|
| `list_recipes`, `get_recipe` | Read the library |
| `validate_recipe` | Run `normalize_recipe` + `validate_recipe` and return the errors by field, without saving |
| `save_recipe` | Validate and store under a slug |
| `delete_recipe` | Remove one |
| `machine_state` | The public snapshot |
| `load_recipe` | `Machine.load` for a stored recipe: arms, never starts |
| `cancel_load` | `Machine.cancel` (8017), only while armed |

**Mounting.** The SDK's streamable-HTTP app does not run its own lifespan
when mounted, so its session manager is entered from the hub's lifespan in
`app.py`; and its internal path is configured so the endpoint is `/mcp`, not
`/mcp/mcp`. The SDK's transport security is set explicitly rather than left
to its host-based default, with the hub's middleware as the control.

There is no start tool and no setting that adds one in this slice. The start
ticket that `Machine.load` returns is **removed from the MCP result**, so an
assistant never holds it (P2-2). Tool descriptions are fixed text; no recipe
content is interpolated into them (P2-6).

**One recipe shape (invariant 4).** xbloom-py validates one set of field
names and encodes another: `validate_recipe` checks `grind_size` and
`grinder_speed_rpm`, while `build_brew_frames` reads `grinder_size`, `rpm`
and `grinder_size_enabled`, and `normalize_recipe` only copies one way. A
recipe in the validator's names passes and then goes out with the grinder
off; a recipe carrying both sets is validated on one and sent with the other.
S1 never met this because its only recipe used the encoder's names. S2 closes
it for every caller, not just MCP:

- `recipes.checked()` accepts exactly one shape and refuses any other key, at
  the top level and inside each pour. Top level: `name`, `cup_type`,
  `dose_g`, `ratio`, `grind_size`, `grinder_speed_rpm`, `grind` (boolean,
  default true), `pours`, `meta`. Per pour: `volume_ml`, `temperature_c`,
  `pause_s`, `pattern`, `flow_rate`, `agitate_before`, `agitate_after`.
  `pour_count` is derived from `pours`. Bypass water is not supported in this
  slice and its keys are refused.
- On top of the library's checks it validates what the library does not:
  `grind` is a boolean, `agitate_before` and `agitate_after` are 1 or 2.
- A new `recipes.to_machine()` builds the dictionary the encoder reads from
  the checked recipe and from nothing else: `grinder_size` from `grind_size`,
  `rpm` from `grinder_speed_rpm`, `grinder_size_enabled` 1 or 2 from `grind`,
  and only the listed pour keys. `Machine.load` is given that dictionary.
- Files already in the library in the old shape are refused on load with the
  field errors; there is one, the S0 test recipe, and it is rewritten.

**Recipe text (P2-6).** `name` is at most 80 characters with control
characters removed. `meta` is an optional mapping whose keys are limited to
`bean`, `roaster`, `process`, `roast`, `notes`, `source` and whose values are
strings of at most 500 characters, likewise cleaned. Because unknown keys are
refused, there is no other place for free text. `meta` is stored and returned
and never reaches `to_machine()`. `RecipeStore.list` returns names through the
same cleaning, so a hand-edited file cannot inject through the list.

**Skill.** `.claude/skills/xbloom-recipe/` in this repository:

- `SKILL.md`: the workflow (ask about the coffee, design, show the recipe,
  save, load only when the user says they are at the machine, never claim a
  brew was started), the field names of the one recipe shape, and the hard
  limits from xbloom-py `spec.py` — grind 1-80, grinder speed 60-120 RPM in
  steps of 10, dose by cup type (Omni 5-18 g; 20 g needs cup type 3,
  "Other", 5-25 g), pattern 1-3, 20-98 °C, flow 3.0-3.5 ml/s, pause a whole
  number of seconds 0-59, at most 9 pours of at most 240 ml, ratio 1:5 to
  1:25 in 0.5 steps, pours summing to dose x ratio within 0.5 ml. The ranges in xbloom-agent's instructions
  (grind 40-120, dose 1-31) are wrong and are not carried over.
- The rule for hand-pour recipes given as start times: pause after a pour =
  next start - this start - volume / flow rate, rounded to the nearest
  second. A recipe that needs a pause over 59 s or a pour over 240 ml is
  split or adjusted, and the skill says so.
- `brewing-reference.md`: ported from xbloom-agent's brewing reference
  (MIT per its README), with the ranges corrected and the source credited.

**Client setup.** `docs/CLAUDE-CODE.md` with an `.mcp.json` example whose
`Authorization` header comes from an environment variable, so the token is
never written into the file or passed on a command line: a `headersHelper`
script that reads the token file and prints the header, as documented at
code.claude.com/docs/en/mcp (read 2026-11-14). The page also carries the P2-2
note: keep direct calls to the hub and reads of the token file out of
auto-allowed permissions, since the token can do more than the MCP tools.

**Tests (no hardware).** `/mcp` returns 401 without the token for POST and
for GET, and 400 for a foreign `Host`; the tool names are exactly the eight above; `load_recipe` on the
simulator arms and its result has no ticket; `save_recipe` returns field
errors for an out-of-range recipe and stores nothing. For the recipe shape: a
recipe with `grinder_size`, `rpm`, `grinder_size_enabled`, `pour_count` or any
other unlisted key is refused; a grinder-on recipe saved through MCP reaches
the encoder with `grinder_size` and `rpm` equal to the validated values and
the simulator's gate log is 8100, 8102, 8104, **8001**, then 8017 after
cancel; the same recipe with `grind: false` gives 8004. A name with control
characters and an over-long `meta` value are cleaned or refused.

**Observed (user present, in the user's own Claude Code session).**

1. The user asks for a recipe for a coffee, using the hand-pour card from
   2026-11-14 (20 g; 80 °C to 60 g at 0:00, 95 °C to 180 g at 0:45, 80 °C to
   300 g at 1:45) as the case. The assistant produces a recipe that validates
   and whose pauses follow the start-time rule.
2. The assistant saves it and, once the user says they are at the machine,
   loads it. The machine's screen shows a grind size, not `--` as it did for
   the grinder-off recipe in S0, and the hub's gate log shows 8001.
3. Either the assistant cancels at the user's request and the machine returns
   to home, or the user starts the brew at the machine by hand. This is a real
   recipe with hot water and the grinder on, so a brew started by the knob is
   the intended way to brew, not an accident. It is loaded only with the user
   at the machine and everything a brew needs in place: the dose of beans in
   the grinder, dripper with filter, water in the tank, and a cup that holds
   the full volume. What the machine does with the cup weight range sent by
   8104 for cup type 3 is unknown; if it refuses to start, that is recorded,
   not worked around.

**Non-goals.** Starting, pausing or stopping a brew over MCP; community or
cloud recipes; brew history and feedback; the dashboard; Docker.

**Rollback.** New files plus additive changes to `app.py` and `recipes.py`.

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

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
YAML files and brew history is one JSON file per brew (S3 explains why not
sqlite), both on a `/data` volume. The
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
3. **Start gate (P1-5).** A load returns a one-time ticket for that load (S3
   dropped the recipe hash: a loaded recipe can be neither saved over nor
   deleted, so the ticket already stands for exactly what was sent). Start must present it; the ticket is consumed on first use,
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
| S3b | Recipes from xBloom's community hub and share links, no account | an official recipe saved from the page and loaded on the machine |
| S3c | The dashboard rebuilt on Vite, Svelte and ECharts, with browser tests | every browser test passes on the new page; the owner looks at it; one start on the machine |
| S4 | Dockerfile and compose, deployed to a home server on the LAN rather than the development Mac | one brew driven through the container |
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

Done 2026-11-14: [S2-RESULTS.md](S2-RESULTS.md).

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

## S3 — dashboard, start, and the brew record

Written after S2; see [S2-RESULTS.md](S2-RESULTS.md) and
[OFFICIAL-APP-NOTES.md](OFFICIAL-APP-NOTES.md).

**Outcome.** A page served by the hub that follows the machine: what it is
doing now, the recipe library, past brews, and a console. From it a person can
load a recipe, **start the brew**, and stop it. Every brew the hub sees is
recorded with its weight and water curves. This is the first slice that can
dispense water.

### Start and stop (safety invariants 1 and 3)

- `gate.py` gets two more sets: `STOP = {40519}` and `START = {8002}`. Load,
  cancel and stop codes stay always allowed on FFE1. **8002 passes only while
  a start permit is held**: a context variable set by `Machine.start()` for
  the single `send_start()` call and cleared after it. Any other write of 8002
  is refused as before, including the library's own `send_brew`/`brew()`.
  Resume (40524), pause, grinder and slot codes stay in no list.
- `Machine.start(ticket)`, under the lock, refuses unless: the state is
  `armed`; the ticket equals the one issued by the load (compared with
  `hmac.compare_digest`) and has not expired; and no cup-off event is the
  latest cradle event (below). The ticket is consumed before anything is
  sent, so a start can never be repeated; if the machine refuses the start,
  the recipe has to be cancelled and loaded again. The ticket is already void
  when the machine leaves the recipe screen or another load happens (S1).
- The start frame goes out through the listener's `send_confirmed`, which
  re-sends only while the machine is asleep, not through `send_start`, whose
  two attempts would write 8002 again if the first echo were lost. So "once"
  means one write while the machine is awake; the firmware refuses a
  duplicate as busy in any case (xbloom-py `spec.py`).
- `Machine.stop()` sends 40519 and is accepted only while a brew record is
  open (below). It has its own lock, so it never waits behind a start's echo,
  and a second Stop while one is in flight is refused rather than sent.
- `POST /api/machine/start` (body: the ticket) and `POST /api/machine/stop`.
  REST only, bearer token required as for every non-GET. **Not MCP tools.**
- The dashboard's start button opens a confirmation that names the recipe and
  shows the dose, ratio and total water as they will be sent; the ticket the
  confirmation uses is the one from the load that the page itself made or, for
  a load made elsewhere (an assistant), the page asks the hub to cancel and
  load again rather than start something it did not load. A ticket is never
  put in `GET /api/state` or the event stream.

### Cup presence

xbloom-py notes two cradle notifications it has seen, 9002 (cup on) and 9008
(cup off). The hub keeps the latest as `cup: true | false | null`. A start is
refused when it is `false`; when it is `null` (no event seen this session) the
start is allowed and the dashboard says the cup is unconfirmed. Whether these
events arrive on this machine is unverified and is recorded in the hardware
run; the scale reading is not used (it stays tared, S1/S2). This is how
disposition P1-5's deferred cup check is settled: the official app has none
at all, and the weight route is closed.

### The brew record

**A brew opens only on what the machine reports**, never because the hub sent
something. With no brew open, one of:

- a pour starting (40510);
- activity **changing into** a brewing value from a value that is not one.
  The brewing values are 35 (measured with the grinder off,
  `docs/S3-RESULTS.md`) and 34 (what xbloom-py and the Home Assistant
  integration give for grinding and brewing; not yet seen here). Activity
  arrives with every status frame, so only the change counts: a machine that
  goes on reporting a brewing value after a brew has closed opens nothing
  until activity has left those values and come back, or a new 40510 arrives;
- for 30 seconds after the hub wrote 8002 only: activity changing to any
  value that is not known to be something else (1, 65, 10, 29, 4, 5, 31, 36).
  Only one brew has been measured, with the grinder off, and a start the
  hub sent must not end up brewing with no record and no Stop because the
  list was incomplete.

These never open one: activity 1 and 65 (home), 10 (asleep), 29 (after the
handshake), 4 and 5 (tare), 31 (recipe ready, also seen after a pause), 36,
and 40511, 40512 and 40513.

**What a start does to the state.** Nothing by itself. After the hub writes
8002 the state is still `armed` until the machine shows a brew:

- accepted, and the machine brews: a record opens from the signals above;
- **refused** (`CommandRefused`): the caller gets the refusal, the state stays
  `armed`, cancel works, the ticket stays consumed;
- **unanswered**: the caller is told so. If the machine did start, the record
  opens from its signals; if it did not, the state is still `armed` and
  cancel works.

**The recipe travels with it.** Today the hub forgets its loaded recipe the
moment the machine leaves the recipe screen. From S3, leaving 31 voids the
start ticket and the "loaded" mark as before, but the recipe (as checked and
as sent) is held until the machine is home or asleep again; a brew that opens
in between takes it. A brew that opens with nothing held has `recipe: null`.

**When it closes.** 40512 (finished), 40513 (abandoned), activity 36 (the
screen after a brew, for a 40512 that was missed), activity 1 or 65
(home), activity 10 (asleep), or the link dropping; a stop the hub sent is
recorded as the reason when one of those follows it. There is no timer.

**State while a record is open** is `brewing`, whatever the activity value,
including 31 during a pause. With no record open the states are as in S2, so
load and cancel are never blocked by a brew that is not happening, plus
`finished`: from a 40512 until the machine's activity next changes, and
while activity is 36. On the machine that is about 25 s. A load waits for
`idle` or `asleep`.

**Readings are corrected where they enter.** Water arrives in thousandths
of a millilitre and is divided by 1000. The machine sets its scale to zero
about 5 s into a brew; a reading that falls by more than 2 g to within
-0.5 to 1.5 g of zero, and is not followed by one below that, is taken as
that reset and the weight before it is
carried over, so the record holds the weight in the cup since the brew
opened. Each reset is listed in the record under `rezeroed`.

Each record is one JSON file under `data/brews/`: when it started, how it
ended, the recipe as checked and as sent (or null), every weight (20501) and
water (40523) reading with its time, and every other decoded notification
with its time. The file is written when the brew opens and again every ten
seconds, so a hub restart mid-brew loses at most that. The id is made by the
hub (`YYYYMMDD-HHMMSS` plus four random characters) and checked against that
pattern on the way in, like a slug. The machine's serial number, which the
status heartbeat carries, is dropped from records, the snapshot and the event
stream: those are readable without the token. The envelope named sqlite for
history; one file per brew needs no schema and no query, and nothing in S3
asks a question across brews that a directory listing cannot answer. With no
SQL there is nothing left of disposition P2-5's "parameterised SQL"; its
file-name rule now covers brew ids too.
`GET /api/brews` lists them, `GET /api/brews/{id}` returns one. The snapshot
and the event stream gain `water_ml`, `cup` and the open brew's id, start time
and current pour.

### The page

Static files in the package, served at `/`: `index.html`, one `app.js`, one
`style.css`. No build step, no framework, no CDN.

- **Home** follows the state. Offline: why. Idle or asleep: last recipe, a
  way to choose one. Armed: the recipe, cup status, Start and Cancel. Brewing:
  pour n of m, elapsed time, water and coffee so far, the live curve, Stop.
  Finished: the curve and the totals.
- **Recipes**: the library; a recipe's pours as the official app lays them out
  (share of the total, volume, temperature, flow), with what will be sent, the
  planned timeline, and Load and Delete. No editor: recipes are written
  through Claude Code.
- **History**: past brews; one brew's curve against its recipe's planned
  water line with each pour's actual start marked; two brews overlaid.
- **Console**: link state and reason, release and acquire, the live event
  log, and the codes the gate passed.

The official app's real screens (its store listing) are the reference for
structure: dark surface, large numerals, the brew screen's "pours n/m",
elapsed time and two-series chart (water, coffee). Its logo, photographs and
artwork are not used. What it lacks and this adds: the planned line under the
curve, actual pour times, comparison between brews, the console. Interface
text comes from one dictionary per language (Traditional Chinese and English
to begin with), chosen from the browser's languages and changeable in the
console; the owner asked for this while the page was being built.

**Security (P1-3, P1-4, P2-1).** Every response carries
`Content-Security-Policy: default-src 'self'; script-src 'self'; style-src
'self'; img-src 'self' data:; object-src 'none'; base-uri 'none';
frame-ancestors 'none'`, so there is no inline script or style. Every string
that comes from a recipe or the machine is put into the page with
`textContent`; nothing is assigned to `innerHTML` from data. A recipe's
`meta.source` becomes a link only when it starts with `https://`. The token arrives
once in the URL fragment (`/#t=…`), is moved to `localStorage` and removed
from the address bar; it is sent only in the `Authorization` header. The
event stream stays unauthenticated and read-only.

**Reaching it from a phone.** The hub binds to `127.0.0.1` by default. For
the LAN, `XBLOOM_BIND=0.0.0.0`, the home network in `XBLOOM_ALLOWED_NETWORKS`
(clients outside it are refused) and the Mac's address in
`XBLOOM_ALLOWED_HOSTS`; `docs/DASHBOARD.md` says how. In a container (S4) the
host's own requests may arrive from the bridge gateway's address. The hub refuses to
start with `*` or an empty entry in that list, so binding wide cannot undo
the host check of P1-3.

### Tests (no hardware)

- Gate: 8002 is refused outside a permit on every path, including through the
  listener's own `send_start`, and passes inside one; 40524 and the other
  movers are still refused inside a permit; an 8002 from another task while a
  permit is open is refused.
- Start: refused with a wrong, reused or expired ticket, after a cancel, after
  another load, when not armed, and when the last cradle event is cup-off;
  accepted once otherwise; not present in the MCP tool list.
- The simulator gains a brew: after a start it reports activity 35, pour
  events, rising water and weight, and finished. Tests check the record for a
  hub start, for a stop, for a knob start after a hub load (recipe present),
  and for a knob start with nothing loaded (recipe null). A refused 8002
  leaves no record, the state `armed` and cancel accepted. An unanswered 8002
  with the simulator staying at 31 leaves no record, the state `armed` and
  cancel accepted; an unanswered 8002 after which the simulator brews opens a
  record. After a hub start, an activity value outside the known list opens a
  record; the same value without a hub start does not. An open record
  followed by activity 10 closes. After 40512, further frames with the
  brewing activity and then activity 1 leave exactly one closed record and none open. Tare,
  handshake, recipe-ready and finished signals open nothing. After each of
  these a load from idle or asleep is accepted. The file exists while the
  brew is still running, and no record, snapshot or stream message contains
  the serial number.
- The page: served with the CSP header; `index.html` contains no inline
  script, inline style or `on…=` attribute; `app.js` assigns no data to
  `innerHTML` (checked by reading the source in the test, and by loading a
  recipe named `<img src=x onerror=alert(1)>` in a real browser against the
  simulator and seeing it as text).

### Observed in a browser (no hardware)

The dashboard is driven against the simulator in a real browser through
load, start, a simulated brew, stop, history and compare, at phone width and
desktop width, and the screens are looked at.

### Observed on the machine (user present)

Steps 1 to 3 use recipes with the grinder off and water at 20 °C (room
temperature, not heated), with no beans in the machine, water in the tank,
and a dripper over an empty cup that holds at least 150 ml.

1. **Harmless start.** Grinder off, 5 g, ratio 1:5: one 25 ml pour at 20 °C.
   Load and start from the dashboard. Expected: 25 ml of unheated water; the
   gate log shows 8002 (more than one would mean the library re-sent it,
   which is recorded, not a failure: the firmware refuses a duplicate as
   busy); the page follows the brew; a record is written.
   Before the load, the cup is lifted and put back, and the run notes whether
   9002 and 9008 arrive. The run also notes every activity value seen.
2. **Stop.** Grinder off, 5 g, ratio 1:20: one 100 ml pour at 20 °C and
   3.0 ml/s, about 33 seconds. Start, then press Stop on the dashboard after
   about ten seconds. Expected: the machine stops and returns to home; the
   record ends as stopped with less than 100 ml of water. If 40519 does not
   stop it: at most 100 ml of unheated water goes into the cup and the pour
   ends by itself; the result is recorded and Stop is removed from the page
   until it is understood.
3. **Started at the knob.** The step-1 recipe, loaded from the dashboard and
   started with the machine's knob. Expected: a record opens that carries the
   recipe, and the page offers Stop. If no record opens, the signals that did
   arrive are recorded and the rule above is corrected before S3 is called
   done.
4. **A real brew**, when the user wants a coffee: a real recipe, everything a
   brew needs in place, started from the dashboard, watched on the page.
   This is the first measurement of pour timing against the plan.

Step 1 is the first time anything in this project sends a start. It is not
attempted with nobody at the machine. What the machine does with the cup
weight range that 8104 sends is unknown; if it refuses a start for that, the
refusal is recorded, not worked around.

**Non-goals.** A recipe editor; pause and resume; start or stop over MCP;
Home Assistant; Docker; anything beyond the one shared token.

**Rollback.** New files, and additive changes to `gate.py`, `machine.py`,
`app.py` and `sim.py`. Without a start permit the gate behaves as in S2.

## S3b — recipes from xBloom's community hub and share links

Asked for by the owner on 2026-11-14: "recipes should connect to xBloom's
web page, and more of the official presets". Not in the original slice list;
it sits between S3 and S4 because it is a feature of the hub, not of its
packaging.

**Outcome.** From the dashboard, and from Claude Code, a person can search
xBloom's community recipe hub (the library behind `collective.xbloom.com`,
which includes the recipes xBloom itself publishes, marked official), look at
one, and save it into the hub's own library as an ordinary recipe. A share
link pasted into the page does the same for one recipe. Nothing is signed in
to, and nothing is ever sent to xBloom except search terms and recipe ids.

**What exists already.** `xbloom-py` 0.5.0 has both clients, each exercised
live by its author without credentials: `XBloomCommunityClient`
(`criteria()`, `search()`, `detail()`, host `collective-api.xbloom.com`) and
`XBloomClient` (`share_id_from_url()`, `get_recipe_by_share_id()`, host
`client-api.xbloom.com`). A hub recipe's own pours lack flow rate and
agitation; its `share_url` resolves through the second client to the complete
recipe. Both are written for an `aiohttp.ClientSession`, which this project
does not install; `client.py` imports aiohttp when it is loaded, `community.py`
only uses the session it is handed.

**Scope.**

- No new dependency. `httpx` is already installed (the `mcp` SDK needs it)
  and is added to this project's own dependencies at the installed version.
- `src/xbloom_hub/community.py`: one module that owns one `httpx.AsyncClient`
  for the life of the hub and offers `search`, `detail` and `fetch_share`.
  All outbound requests go through one function in it, which
  - posts only to these four URLs and refuses any other before a request
    is made:
    `https://collective-api.xbloom.com/communityRecipe/recipe/criteria`,
    `https://collective-api.xbloom.com/communityRecipe/index/page`,
    `https://collective-api.xbloom.com/communityRecipe/recipe/detail`,
    `https://client-api.xbloom.com/RecipeDetail.html`;
  - sets `follow_redirects=False`, `trust_env=False`, no cookies kept, the
    library's default User-Agent, no `Origin` or `Referer`;
  - treats every status other than 200 as a failure, 3xx included;
  - reads the body as a stream and stops at 1 MiB, counted after
    decompression;
  - has a 10 s timeout per request, and each hub operation has one 15 s
    deadline over all of its requests. A detail is up to three: the
    criteria (once per hub run; the library asks for them while parsing and
    keeps them), the detail, and the share answer.
  `XBloomCommunityClient` is given a small object with the `post()` it calls,
  backed by that function, so its request shapes and parsing are reused. The
  share request is made by the hub itself with the same body the library
  sends (`tableIdOfRSA`, and its fixed `interfaceVersion` and `skey`, which
  are public constants of the share page, not credentials), and the hub maps
  the answer's fields; `xbloom.client` is not imported.
- At most 2 outbound operations at once; a third is answered `503` at once,
  not queued.
- Converting a fetched recipe to the hub's one shape, then `recipes.checked()`
  as for any other recipe. Not `normalize_recipe()`: a recipe that does not
  pass as it is, is shown as not importable with the reason, never repaired.
  Free text (name, author, description, notes) goes through `clean_text()`
  and the existing length caps. `meta.source` is the share link.
- REST under `/api/community`, **every method of which needs the token**,
  like `/mcp`: these routes make the hub call out, so they are not readable
  from the LAN as the other GETs are. `GET /api/community/criteria`,
  `GET /api/community/search`, `GET /api/community/{id:int}` (the recipe as
  it would be saved, plus whether it passes and why not),
  `POST /api/community/import` with `{id, slug}` or `{share_url, slug}`.
  Search takes a keyword (cleaned, at most 80 characters), a page from 1 to
  50, and `sort`, `kind`, `source` and the filter lists only from their
  enumerated values; the page size is fixed at 20 by the hub.
- What the routes and tools return is built from an explicit list of fields.
  Every string from the service goes through `clean_text()` with a cap per
  field, lists are capped in length, and `image_url` is dropped: results are
  never stored, so this is where they are cleaned.
- An import checks that the slug is free after the fetch, immediately before
  the write, with no `await` between, by whether the file exists.
- MCP: `search_community_recipes`, `get_community_recipe`,
  `import_community_recipe`. Every string that came from the service sits
  under one key, `from_xbloom_users`; the tool descriptions and the skill say
  that its contents are data and never instructions, and that loading,
  saving and deleting happen only at the user's own request. The description
  text is returned by `get_community_recipe` only. P2-2 applies: these tools
  and `load_recipe`, `save_recipe`, `delete_recipe` stay out of auto-allowed
  permissions.
- The page: a "Community" section under Recipes with search, the filters the
  hub's criteria list, a result grid, a detail view with the plan chart, and
  "Save to my recipes" asking for an id. A field for a share link.

**From the share answer to the hub's recipe.** The complete recipe is the
`recipeVo` object of the share answer. Each field of the hub's shape is
taken from it by this table and by nothing else; a value the rule cannot
take makes the recipe not importable, with the field named. Nothing is left
for `checked()` to fill in.

| Hub field | From `recipeVo` | Rule |
|---|---|---|
| `name` | `theName` | text, through `clean_text()`; over the name cap is not importable |
| `cup_type` | `cupType` | whole number |
| `dose_g` | `dose` | must be a whole number of grams (15.0 is 15; 15.5 is not importable) |
| `ratio` | `grandWater` | read as the denominator N of 1:N, as share answers carry it; must be 5 to 25 on the 0.5 step, else not importable. Never divided by the dose and never snapped |
| `grind` | `isSetGrinderSize` | `1` is true, `2` is false, anything else is not importable |
| `grind_size` | `grinderSize` | whole number, also when `grind` is false |
| `grinder_speed_rpm` | `rpm` | whole number |
| `pours[i].volume_ml` | `pourList[i].volume` | whole number |
| `pours[i].temperature_c` | `pourList[i].temperature` | whole number |
| `pours[i].pause_s` | `pourList[i].pausing` | whole number |
| `pours[i].pattern` | `pourList[i].pattern` | as given |
| `pours[i].flow_rate` | `pourList[i].flowRate` | as given, one decimal |
| `pours[i].agitate_before` | `pourList[i].isEnableVibrationBefore` | as given (1 or 2); a missing value is not importable |
| `pours[i].agitate_after` | `pourList[i].isEnableVibrationAfter` | the same |
| `meta.source` | the share id | the share link rebuilt from the id |
| `meta.notes` | the community detail's `description`, when imported by id | through `clean_text()`, cut at the meta cap |
| `meta.roast`, `meta.process`, `meta.bean` | the community detail's `roast`, `process`, `origin` and `varietal`, when imported by id | joined with ", ", through `clean_text()` |

Not importable, whatever else holds: `isEnableBypassWater` equal to 1, a
`bypassVolume` or `bypassTemp` present, a non-empty `podsVo`, `pourCount`
different from the length of `pourList`. The result then goes through
`checked()` like any recipe, and what `checked()` refuses is not importable
either.

**Non-goals.** Signing in to an xBloom account, the account's own recipes,
uploading or liking, the app's Trending and For You lists (they live on the
account API), images (never fetched by the hub and never shown: P3/P4 above),
tea recipes, caching search results on disk. A recipe that uses bypass water
or a pod is shown as not importable, with that reason: saving it without them
would be a different recipe from the one its author wrote.

**Security dispositions for this slice.**

Reviewed read-only before any code (2026-11-14): no P0 or P1; fifteen
findings, all folded into this section.

- The hub makes requests to exactly the four fixed HTTPS URLs listed under
  Scope, on two hosts. No
  URL from a caller or from a response is ever fetched. A share link, pasted
  or taken from a search result, is parsed by the hub: scheme `https`, host
  exactly `share-h5.xbloom.com`, no user info, port absent or 443, exactly
  one `id`, the id at most 64 base64 characters. `meta.source` is the link
  rebuilt from that id, not the text received.
- Search terms, recipe ids, the home's IP address and the HTTP library's
  User-Agent leave the home network. The docs say so. No token, no serial
  number, no machine state is sent; no cookie is kept between requests.
- A failure is a `502` whose `detail` is one of four fixed words (`timeout`,
  `upstream_status`, `upstream_refused`, `malformed_response`), never text
  from the service. Caught: transport errors, timeouts, the library's
  `XBloomAPIError`, and `KeyError`, `TypeError` and `ValueError` from
  parsing. Logs carry the exception class and the status code only.
- Everything received is untrusted text: inserted with `textContent` on the
  page, cleaned and capped before it is stored, labelled in MCP results.
- An import never loads the machine. It writes a recipe file; loading it is
  the same deliberate step as for any recipe.
- The calls are slow and can fail: a failure is a `502` with a named error,
  the hub's own work (the BLE session, brew records) is not blocked by one,
  and at most a small number run at once.

**Acceptance.**

- Tests with a fake transport, no network: each of the four URLs passes the
  outbound function and a fifth is refused before any request; a recorded-
  shape `recipeVo` with agitation on before the first pour, the grinder off
  and a flow rate of 3.2 is imported and the saved file is compared field by
  field with the table above (it fails if `grind`, `ratio`, a `flow_rate` or
  an agitation field differs); a fractional dose, a `grandWater` of 16.2 and
  one of 240 are each not importable; search and detail return the listed
  fields; a recipe that fails `checked()` is reported, not saved;
  a share link on another host, or with no id, is refused before any
  request; an import refuses a slug in use and one that is not a slug; a
  timeout, a non-200, a 302, a 200 of the wrong shape and a body over the
  cap each become `502` with a fixed word; hostile text (markup, control
  characters, a 10 000 character name) is cleaned or refused; every
  `/api/community` route answers `401` without the token; a third operation
  at once gets `503`; a recipe with bypass water or a pod is not importable;
  a share id containing `+` survives parsing.
- Observed against the live service, by hand, once: a search returns
  recipes; one official recipe is saved and then opens in the hub with a
  plan; a share link imports. This is reading a public service the library's
  author documented; it is rate-limited to a handful of calls.
- Observed in a browser: search, open, save, the saved recipe in the list.
- On the machine (owner present): an imported recipe is loaded and shows on
  the display. Not started from this slice.

**Rollback.** One new module, three routes groups, three MCP tools, one page
section, one dependency. Removing them leaves S3 as it is.

**Stops.** The live service answers differently from what the library
expects, or asks for a login: stop, record what it answered, and put it to
the owner; do not imitate another client or add credentials.
If the live check shows that one rule of the table refuses most recipes (a
ratio off the 0.5 step is the likely one: the library's own notes mention
1:16.2), the rule is not loosened in this slice: the count is recorded and
the choice between refusing and rounding is put to the owner.

## S3c — the dashboard on Vite, Svelte and ECharts

Decided by the owner on 2026-11-14, after two independent advisers compared
staying hand-written, Preact with htm and no build, and a build toolchain.
The owner chose the toolchain. The hand-written page rebuilds a whole view
on every state change, so forms lose their typing, live readings flicker and
animations replay unless each is worked around by hand, and every chart
feature (zoom, replay, annotations, a calendar) has to be drawn from
nothing. What the owner asked for next is mostly those.

**Status (2026-11-14).** Steps 1 to 6 done (pull requests #12 to #18 and
the switch). `/` serves the built page; `static/` is gone. Left: the
owner's look at 2560 px and on the phone, and one load and start of the
25 ml test recipe on the machine.

**Outcome.** The same dashboard, at the same address, built from Svelte 5
components in TypeScript with ECharts for the charts, served by the hub as
static files under the same Content-Security-Policy, and covered by browser
tests that run against the simulator. Nothing it can do to the machine
changes.

**Stack, pinned exactly in `frontend/package.json` with the lock file
committed:** Vite, Svelte 5, `@sveltejs/vite-plugin-svelte`, TypeScript,
`svelte-check`, ECharts (imported per module: line, custom, calendar, the
dataZoom, markLine, markPoint, tooltip and legend components, the canvas
renderer). No router library (the hash routes stay), no state library, no
UI kit, no motion library (Svelte's own transitions, CSS, and the Web
Animations API). Browser tests: `pytest-playwright` in the project's own
Python environment, with Chromium installed by Playwright into its own
cache; the owner agreed to that install.

**Supply chain.** Every npm command runs in a throwaway container (OrbStack
on the Mac), never on the host: see the dispositions below. Node runs only
to build; the hub at run time is Python serving files. S4's image builds in
a separate stage with the same script and copies only the output.

Code in the page shares the origin, and so the token, with everything the
page can do: a compromised package in the bundle could load and start a
brew without the dialog. The confirmation and the one-time ticket guard
against mistakes and other callers, not against code in the page. This is
new with this slice; the hand-written page had no third-party code.

**Layout.**

- `frontend/` holds the sources: `src/` (components, one module per page),
  `src/lib/hub.ts` (the token, `api()`, the event stream, the one-request
  lock), `src/lib/i18n.ts` (the two dictionaries, moved as they are).
- `vite build` writes to `src/xbloom_hub/web/`, which is git-ignored and
  shipped in the sdist and the wheel by
  `[tool.hatch.build] artifacts = ["src/xbloom_hub/web/**"]` (hatchling
  leaves ignored files out otherwise, and `uv build` makes the wheel from
  the sdist, so a wheel-only setting would not reach it). Vite builds with
  `base: "./"`: every asset is referenced relative to the page, so the same
  build works at `/next/` and at `/`.
- **Serving, one rule per phase.**
  - Steps 2 to 5: `/` serves the old `static/`, always. A second
    `StaticFiles` mount at `/next/` serves `web/`, placed before the `/`
    mount, and is added only when `web/index.html` exists at start. A
    checkout with no build starts and serves `/` as today.
  - Step 6: `/` serves `web/` only; the `/next/` mount and `static/` are
    gone. If `web/index.html` is missing at start, the hub refuses to start
    with a message naming the build command (`npm --prefix frontend run
    build`). It never serves an empty site.
- The old `static/` stays, untouched, until the new page passes every
  browser test; then it is removed in the same pull request that makes the
  new one the default.

**What does not change.**

- The CSP string in `app.py`, byte for byte. Vite's output is external
  script and style files; no inline script, no inline style element, no
  `eval`. Dynamic sizes and positions go through the CSSOM (`el.style.x`)
  and the Web Animations API, which the CSP allows. ECharts tooltips are
  drawn on the canvas or built as DOM by the page, never from an HTML
  string; no `{@html}` anywhere; `innerHTML` stays out of the page's own
  code. A test loads every page with a listener for
  `securitypolicyviolation` and fails on any.
- Text from the hub, a recipe or a stranger is a text node, always. A
  recipe source is a link only when it starts with `https://`.
- The token arrives in the address fragment once, lives in localStorage,
  and is sent only in the `Authorization` header.
- Safety invariants 1 and 3: start is a REST call with a confirmation that
  shows the recipe as the hub holds it when the machine armed; the ticket
  stays in memory, Start is offered only while the hub's `load_id` is the
  one the page's own load returned, and a `no_cup` refusal keeps the ticket
  usable; Stop is never disabled or replaced
  by a redraw while a brew runs; one request at a time.

**Security dispositions for this slice.** Reviewed read-only on 2026-11-14:
no P0, two P1, six P2, four P3, one P4. The owner decided the two P1s and the
WebKit download the same day.

| ID | Finding | Disposition |
|---|---|---|
| C1 P1 | Building runs every package's code with the owner's rights on a Mac that holds credentials | FIX (owner: containers). `frontend/npm.sh` runs every npm command (`ci`, `install <pkg>@x.y.z`, `run build`, `run check`) in `docker run --rm` on a Node image pinned by digest, mounting only `frontend/` and `src/xbloom_hub/web/`, no home, ssh agent, env file or `secrets/`; `node_modules` in a named volume, not the host tree. Stop: an npm command about to run on the host. Step 2 |
| C2 P1 | A package in the bundle can start a brew with the page's token | FIX + ACCEPT (owner). The build fails if the bundle holds a module from a package outside {`svelte` and its runtime dependencies as resolved, `echarts`, `zrender`, `tslib`}, read from the bundler's module graph in a Vite plugin; a planted extra import is shown to fail it. The residual risk, a compromised package on that list, is accepted by the owner. Applies from step 2 |
| C3 P2 | Adding a package runs its scripts; pins drift | FIX. `frontend/.npmrc`: `ignore-scripts=true`, `save-exact=true`, and a release cooldown of 7 days (`min-release-age`, or `before=<date>` if the container's npm does not know it; checked with `npm config get` in the container). Top level, exactly: `vite`, `svelte`, `@sveltejs/vite-plugin-svelte`, `typescript`, `svelte-check`, `echarts`. Step 2 |
| C4 P3 | Nothing checks the lock file | FIX. On every change to `package-lock.json` a script checks each `resolved` is on `registry.npmjs.org` and each entry has a `sha512` integrity; `npm audit signatures` and `npm audit` run in the container; the pull request lists packages added and removed. An invalid signature fails; missing provenance is recorded. Step 2 |
| C5 P2 | ECharts' HTML tooltip writes strings into `innerHTML` with inline styles | FIX. No ECharts tooltip in HTML mode and no string formatter: the reading under the pointer is a Svelte component fed by the chart's axis-pointer events (`tooltip.show: false`), or `renderMode: 'richText'`. Untrusted names only in data and series names. Step 3, with C7's tests |
| C6 P2 | The CSP test sees only what it triggers, in one engine | FIX. The listener is installed by `add_init_script` and a CSP console message also fails; chart tests hover and zoom, and the dialog and transitions are exercised; the CSP tests run in Chromium and WebKit (owner agreed to the download). Page code sets dynamic styles only per property; no static `style="…"` in templates. Steps 1 to 6 |
| C7 P2 | The text scans for `innerHTML` cannot read a Svelte bundle | FIX. `test_the_page_has_nothing_inline_and_puts_no_data_in_innerhtml` and `test_views_that_fetch_never_paint_directly` are replaced by browser tests that put hostile strings (markup with an event handler, `{a|b}`, 10 000 characters) into every untrusted sink: recipe name, notes and meta, history and compare labels, chart series and legend names, the console log and gate lines; each shows as text with no new element. `meta.source` cases: `javascript:`, `data:`, a leading space and `HTTPS://` are not links; `https://` is, with `rel="noopener noreferrer"`. No `{@html}`, no spread of data onto elements, no element named from data. Step 1 for the old page, then for each new page |
| C8 P2 | Vite's dev server cannot run under the CSP and has had file-read advisories | FIX. The development loop is `vite build --watch` in the container into `web/`, served at `/next/` by a simulator hub with a throwaway token. The dev server is not used against any hub, never with `--host`, never with a proxy to a real hub |
| C9 P2 | Stop is disabled while another request is in flight | FIX. Stop is exempt from the one-request lock (the hub serialises stops itself), on the old page in step 1 and on the new one; a test holds the start request, drives the simulator into brewing and sees Stop enabled and its POST reach the hub |
| C10 P3 | Token handling order and boundaries | FIX. `hub.ts` reads `#t=` and clears it with `history.replaceState` before mounting or routing; `api()` takes only absolute `/api/…` paths; the token and the ticket never go into logs, error text, `import.meta.env` or a `.env`; `/api/community` sends the token on GET too. Test: after opening with `#t=`, the hash is clean and no text holds the token. Step 2 |
| C11 P3 | Ordering: the step-2 console and the step-5 brewing view would hold controls before step 6 | FIX. Until step 6 `/next/` has no action buttons (the console is read-only, the brewing view has no Stop) and `/` is the control surface during any real brew |
| C12 P3 | Build output hygiene | FIX. `.gitignore` gains `frontend/node_modules` and `src/xbloom_hub/web`; Vite `emptyOutDir: true` |
| C13 P4 | Inlined assets as `data:` URIs | FIX. `build.assetsInlineLimit: 0` |

**Order.** Each step is one pull request, through the usual gate.

1. **Browser tests against the current page**, before anything else:
   `tests/browser/`, a fixture that starts a simulator hub on a free port
   with a throwaway token and data directory. Flows: open with the token;
   load, confirm, start, follow the brew, finished view; stop during a
   brew; a `no_cup` refusal keeps the ticket; a load made elsewhere shows
   "load again" instead of Start; cancel; a recipe named with markup shows
   as text; and four that hold the safety behaviour itself:
   - one request at a time: the start (and the load) request is held with
     `page.route`, the button is pressed again, and exactly one request
     reaches the hub; Stop is the exception (C9): with the start request
     held and the simulator brewing, Stop is enabled and its POST arrives;
   - Stop is not replaced: during a brew a handle is taken on the Stop
     element, several snapshots pass, and it is the same connected node,
     still enabled;
   - Start only for the page's own load: the page loads (so it holds a
     ticket), then the same slug is cancelled and loaded again through
     REST, and the page shows "load again" and no Start. The page tells
     its own load by the hub's public `load_id` (one per load, random),
     not by arrival order, which two channels do not guarantee;
   - the start confirmation shows the recipe as the hub returns it: the
     dialog's name, dose, total water, pour count with end time, and grind
     (what the old dialog shows) equal the hub's `plan` answer for that
     slug.
   Each of these four is shown to fail when the line it guards is removed
   from a copy of the old `app.js` (for one request at a time, the whole
   guard: the `busy` early return in `act()` and the `disabled: busy` on
   Start and Load, so that two POSTs reach the hub; the stable Stop node, the load-id comparison,
   `recipeFacts(info)` in the confirmation), and to pass on the old page as
   it is. The rest of the list: the recipe editor saves, refuses a taken id, keeps typing after
   a refusal; history, a record, compare; the console; both languages; no
   CSP violation and no script error on any page. These must pass on the
   old page as they stand.
2. **The toolchain and the shell**: `frontend/`, the build, the hub serving
   `web/`, the layout (sidebar, tab bar, status, the ground), the hash
   routes, `hub.ts`, `i18n.ts`, and the Console page. The Python tests that
   read `app.js` as text are moved to read the built files, or replaced by
   browser tests where they stood in for one.
3. **Charts**: one `Chart` component on ECharts, kept alive across updates
   (data replaced, zoom and pointer kept). History, a record, compare.
4. **Recipes**: the list, one recipe with its plan, the editor.
5. **Home at rest and finished**, and the brewing view's readouts and chart.
6. **Last, the safety-relevant parts**: loading, the armed view, the start
   confirmation, Stop, cancel, release and acquire. Then the switch, and
   the old `static/` removed.

During steps 2 to 5 the new page is served at `/next/` and the old one at
`/`; the browser tests run against both. At step 6 the new page moves to
`/` and every test runs against it.

**Acceptance.** Every flow of step 1 passes against the new page; no CSP
violation; `svelte-check` and the build have no errors; the Python suite
passes; during steps 2 to 5, with `web/` built, `GET /` is the old page,
`GET /next/` is the new one and every asset it references answers 200 with
the CSP header; after `npm --prefix frontend run build` and `uv build`, both
`unzip -l dist/*.whl` and `tar -tzf dist/*.tar.gz` list
`xbloom_hub/web/index.html` and every asset it references; after step 6, starting
with no `web/` stops with the message; the owner looks at it at 2560 px and on the phone. Then one load
and one start of the 25 ml test recipe on the machine, owner present, as
in S3 step 1.

**Non-goals.** New features (they follow, on the new page); server-side
rendering, SvelteKit, a second server process; changing any REST or MCP
contract.

**Rollback.** Until step 6 the old page is what `/` serves. After it, the
pull request of step 6 is reverted.

**Stops.** A package needs its install script and it is not one named
here: stop and ask. An npm command is about to run on the host: stop. The CSP would have to change: stop and ask. A browser
test of a safety flow fails on the new page: the switch waits.

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

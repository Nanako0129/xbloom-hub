# The dashboard

The hub serves a page at `/`: what the machine is doing now, the recipe
library, past brews, and a console. It is built from `frontend/` (Svelte 5,
TypeScript, ECharts, Vite) into `src/xbloom_hub/web/`, and nothing is loaded
from elsewhere.

## From a phone on the home network

By default the hub answers only this host. To open it to a home network,
listen on every interface and name the networks and the host's address
(example values):

```sh
XBLOOM_BIND=0.0.0.0 \
XBLOOM_ALLOWED_NETWORKS=127.0.0.0/8,::1/128,192.168.1.0/24 \
XBLOOM_ALLOWED_HOSTS=localhost,127.0.0.1,192.168.1.20 \
uv run xbloom-hub
```

A client outside the listed networks gets 403 before anything else is
looked at, whichever network the host is on; a network of every address is
refused. The address bar must use a listed host name. The connection is
plain HTTP on the LAN.

There is no pairing and no token (docs/PLAN.md, "No pairing", 2026-11-22):
any browser on a listed network opens the plain address and can do
everything. What keeps a web page on another site out, when it is open in a
browser on the same network, is that the hub refuses a foreign `Origin`, and
that every request that changes something (and every MCP or community
request) must carry the header `X-Hub-Request: 1`, which such a page cannot
add. The network list is therefore the only access control: list only
networks whose every device you trust with the machine.

## Building it

Every npm command runs in a throwaway container, never on the host
(docs/PLAN.md, S3c): it needs Docker (OrbStack on the Mac).

```sh
frontend/npm.sh ci            # once, and after the lock file changes
frontend/npm.sh run build     # into src/xbloom_hub/web/
frontend/npm.sh run check     # type-check
frontend/npm.sh run watch     # rebuild on every change, for a simulator hub
```

The hub refuses to start without a build and says how to make one. A build
made while the hub runs is served at once.

## Opening it

Open `http://<hub address>:8377/` on any device in an allowed network.
Nothing is stored and nothing needs pairing.

To try it with no machine:

```sh
XBLOOM_RADIO=sim uv run xbloom-hub
```

`XBLOOM_SIM_BREW_S` sets how long a simulated brew takes (default 45 s).
`XBLOOM_SIM_DONE_S` sets how long the simulator stays on the screen after a
brew (default 0.05 s, which is too short to see; the machine stays about
20 s).

`XBLOOM_ALLOWED_HOSTS` must be exact: the hub refuses to start with a
wildcard or an empty entry, because that check is what stops a web page
elsewhere from reaching the hub through a DNS trick.

## What each part does

- **Now** follows the machine. Ready: pick a recipe. Loaded: the same three
  things the machine's own display shows (grind, ratio, start), the recipe as
  it will be sent, and Start and Cancel. Brewing: elapsed time, water poured,
  coffee in the cup, the curve against the recipe's planned water line, and
  Stop.
- **Recipes** shows each pour's share of the water, volume, temperature, flow
  and pause, and when it should start and end. Recipes are written through
  Claude Code; the page loads and deletes them.
- **History** keeps every brew the hub saw: the curve, when each pour was
  planned to start and when it did, and two brews overlaid.
- **Console** shows the link, lets the official app have the machine
  (release) and takes it back (acquire), lists state changes and every command
  the hub sent to the machine, and sets the language.

## Starting a brew

Start is offered only for a recipe this page loaded. The hub gives each load a
one-time ticket; the page that loaded holds it, and Start sends it back. A
recipe loaded by something else (an assistant, another browser) shows "Load
again" instead: starting something the page did not load would mean confirming
a recipe on the strength of a name.

The confirmation shows the dose, total water and grind **as they will be
sent**. After it, the hub writes the start command once. What happens next is
the machine's: the page shows a brew when the machine reports one.

A start is refused if the machine's last cradle event says the cup was taken
off. If the machine has reported nothing about the cup, the page says so and
the start is allowed. The scale is not used for this: it stays tared after a
load and reads zero with a cup on it.

## Languages

Interface text lives in `frontend/src/lib/messages.ts`, one dictionary per language with the
same keys. Traditional Chinese and English are included. The page picks from
the browser's languages and remembers a choice made in the console.

To add one: copy a dictionary block, translate the values, add its name to
`LANGUAGE_NAMES`. A test fails if a language is missing a key or a
placeholder, or if the page uses a key that does not exist. The sources in
`frontend/src` contain no interface text of their own, and "xbloom hub" is a
name: it is not translated.

## Recipes from xBloom's community hub

The one thing the hub does on the internet. Searching xBloom's community
recipe hub, opening a recipe from it, or importing one by id or by share
link makes the hub post to `collective-api.xbloom.com` and
`client-api.xbloom.com`, and to four fixed addresses there only. What leaves
the home network: the search terms and filters, recipe ids and share ids,
the home's IP address, and the HTTP library's User-Agent. No token, no
serial number, no machine state is sent; no cookie is kept; nothing is
signed in to. Images are never fetched.

These routes (`/api/community/...`) need the `X-Hub-Request` header even to
read, since each makes the hub call out. At most two run at once (a third gets 503), each
gives up after 15 seconds, and a failure is a 502 naming one of `timeout`,
`upstream_status`, `upstream_refused` or `malformed_response`. An import
writes a new recipe file and never replaces one or loads the machine. A
recipe that uses bypass water or a pod, or does not pass the hub's check as
its author wrote it, is shown as not importable with the reason.

## How it is kept safe

- Every response carries a Content-Security-Policy that allows scripts and
  styles only from the hub itself, so there is no inline script.
- Text from recipes and from the machine is inserted as text, never as markup.
  A recipe named `<img src=x onerror=...>` shows as exactly that.
- A recipe's source becomes a link only if it starts with `https://`.
- The event stream and the read-only addresses need no header and carry no
  ticket and no serial number.

## Browser tests

`tests/browser/` drives the page in a real browser against a simulator hub
that the test starts in its own process, with a throwaway data directory.
`test_cross_site.py` serves a page from a second origin and checks that
nothing it can send makes the hub act. They are left out of the default `pytest` run; run them with:

```sh
uv run playwright install chromium webkit     # once
uv run pytest tests/browser -m browser --browser chromium --browser webkit
```

Every test fails on a Content-Security-Policy violation, a script error or an
unhandled rejection in the page. Six of them hold the page's safety
behaviour: one request at a time; Stop working while a start is still
waiting for its answer; that answer, arriving mid-brew, leaving Stop alone;
Stop the same element through a brew; Start offered only while the machine
holds this page's own load, told by the hub's public `load_id`; and the start
confirmation showing the recipe as the hub holds it.
`tools/browser_mutations.py` takes each guard out of a copy of the page and
checks that its test then fails.

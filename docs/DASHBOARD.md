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
refused. The address bar must use a listed host name. Changing the machine
still needs the token, which a phone gets once from `/#t=<token>` like any
browser. The connection is plain HTTP on the LAN.

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

The hub prints nothing secret, so the token is handed to a browser once, in
the address:

```
http://<hub address>:8377/#t=<the contents of the token file>
```

The page moves the token into the browser's storage and removes it from the
address bar. After that the plain address is enough on that device. Reading
needs no token; loading, starting, stopping, cancelling and deleting do.

To try it with no machine:

```sh
XBLOOM_RADIO=sim XBLOOM_HUB_TOKEN_FILE=secrets/hub_token uv run xbloom-hub
```

`XBLOOM_SIM_BREW_S` sets how long a simulated brew takes (default 45 s).
`XBLOOM_SIM_DONE_S` sets how long the simulator stays on the screen after a
brew (default 0.05 s, which is too short to see; the machine stays about
20 s).

## From another device on the network

By default the hub listens on `127.0.0.1` only. To use the page from a phone:

```sh
XBLOOM_BIND=0.0.0.0
XBLOOM_ALLOWED_HOSTS=localhost,127.0.0.1,<the hub's LAN address>,<its .local name>
```

`XBLOOM_ALLOWED_HOSTS` is the list of names the hub answers to. It must be
exact: the hub refuses to start with a wildcard or an empty entry, because
that check is what stops a web page elsewhere from reaching the hub through a
DNS trick. The connection is plain HTTP, so the token crosses the network
unencrypted; keep the hub on a network you trust.

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

## How it is kept safe

- Every response carries a Content-Security-Policy that allows scripts and
  styles only from the hub itself, so there is no inline script.
- Text from recipes and from the machine is inserted as text, never as markup.
  A recipe named `<img src=x onerror=...>` shows as exactly that.
- A recipe's source becomes a link only if it starts with `https://`.
- The event stream and the read-only addresses need no token and carry no
  ticket and no serial number.

## Browser tests

`tests/browser/` drives the page in a real browser against a simulator hub
that the test starts in its own process, with a throwaway token and data
directory. They are left out of the default `pytest` run; run them with:

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

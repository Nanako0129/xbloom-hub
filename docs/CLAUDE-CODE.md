# Using the hub from Claude Code

The hub serves MCP at `/mcp`. This repository carries the two things Claude
Code needs: `.mcp.json`, which declares the server, and the `xbloom-recipe`
skill in `.claude/skills/`.

## Setup

1. Create the hub's token, once:

   ```sh
   mkdir -p secrets && chmod 700 secrets
   python3 -c 'import secrets,sys;sys.stdout.write(secrets.token_urlsafe(32))' > secrets/hub_token
   chmod 600 secrets/hub_token
   ```

   `secrets/` is git-ignored.

2. Put the proxy's Noise key in `secrets/proxy_psk` (same permissions), then
   start the hub:

   ```sh
   XBLOOM_HUB_TOKEN_FILE=secrets/hub_token \
   XBLOOM_PROXY_HOST=<phone address> \
   XBLOOM_PROXY_PSK_FILE=secrets/proxy_psk \
   uv run xbloom-hub
   ```

   To try it with no machine, use `XBLOOM_RADIO=sim` instead of the two
   proxy variables.

3. Open Claude Code in this directory. It asks whether to trust the folder
   and its MCP server; until you accept, the token helper does not run and
   every call is refused with 401. `/mcp` shows the server and its tools.

## How the token reaches the hub

`.mcp.json` names a `headersHelper`, `tools/mcp-auth-headers.sh`, which reads
`secrets/hub_token` and prints the `Authorization` header. Claude Code runs it
from this directory each time it connects, so the token is not written in any
configuration file and is not passed on a command line.

## What the assistant can and cannot do

It can read, validate, save and delete recipes, read the machine's state,
load a stored recipe and cancel a load. It cannot start, pause or resume a
brew: there is no such tool, and the one-time start ticket a load produces is
not returned over MCP.

That boundary holds for the MCP tools, not for the token. The token also
authorises the hub's REST interface. An assistant that can read
`secrets/hub_token` and run `curl` holds everything the token allows, so:

- do not add `curl` to the hub, or reads of `secrets/`, to auto-allowed
  permissions; let those prompt;
- treat text inside recipes as data. A recipe name is something someone
  typed, and the skill tells the assistant so.

When a start endpoint is added it will check the machine's state and a
one-time ticket for every caller, whatever it holds.

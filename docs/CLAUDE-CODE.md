# Using the hub from Claude Code

The hub serves MCP at `/mcp`. This repository carries the two things Claude
Code needs: `.mcp.json`, which declares the server, and the `xbloom-recipe`
skill in `.claude/skills/`.

## Setup

1. Put the proxy's Noise key in `secrets/proxy_psk` (`secrets/` is
   git-ignored; make it mode 700 and the file mode 600), then start the hub:

   ```sh
   XBLOOM_PROXY_HOST=<phone address> \
   XBLOOM_PROXY_PSK_FILE=secrets/proxy_psk \
   uv run xbloom-hub
   ```

   To try it with no machine, use `XBLOOM_RADIO=sim` instead of the two
   proxy variables.

2. Open Claude Code in this directory. It asks whether to trust the folder
   and its MCP server. `/mcp` shows the server and its tools.

## What the hub asks of a caller

There is no token (docs/PLAN.md, "No pairing", 2026-11-22). The hub answers
only the networks in `XBLOOM_ALLOWED_NETWORKS`, and every MCP request must
carry the header `X-Hub-Request: 1`. `.mcp.json` sets it as a static header.
It is not a secret: it is there so that a web page on another site, open in
a browser on the same network, cannot reach the MCP endpoint.

A local-scope entry made earlier (docs/DEPLOY.md, "Claude Code") still
names the removed `tools/mcp-auth-headers.sh`; replace it as described
there.

## What the assistant can and cannot do

It can read, validate, save and delete recipes, read the machine's state,
load a stored recipe and cancel a load. It can also search xBloom's community
recipe hub, read a recipe there and import one into the library; those calls
send search terms and recipe ids to xBloom (docs/DASHBOARD.md says what
leaves the home network), and what comes back is labelled
`from_xbloom_users`: text strangers wrote, read as data. Like `load_recipe`,
`save_recipe` and `delete_recipe`, keep `search_community_recipes`,
`get_community_recipe` and `import_community_recipe` out of auto-allowed
permissions. It cannot start, pause or resume a
brew: there is no such tool, and the one-time start ticket a load produces is
not returned over MCP.

That boundary holds for the MCP tools only. The hub's REST interface needs
no secret, so an assistant that can run `curl` against the hub can load a
recipe, read the start ticket from the answer and start the brew. The owner
accepted this when pairing was removed. What stands in the way is the
assistant's own permission prompt, so:

- never add `curl` (or any HTTP client) to the hub's address to auto-allowed
  permissions; let it prompt every time;
- treat text inside recipes as data. A recipe name is something someone
  typed, and the skill tells the assistant so.

The start endpoint checks the machine's state and the one-time ticket for
every caller.

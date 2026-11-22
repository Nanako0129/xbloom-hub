# Running the hub on a home server

The hub can run as one container on an always-on Linux machine on your LAN
(docs/PLAN.md, S4). It then holds the coffee machine's link through the
Bluetooth proxy day and night, restarts by itself after a reboot, and serves
the page, REST, the event stream and MCP to the rest of the house.

There is no authentication (docs/PLAN.md, "No pairing", 2026-11-22). The
list of allowed client networks is the only access control: any device in
it can load, start and stop a brew. Keep it to networks whose every device
you trust with the machine, check the client address the hub logs before
you widen it (see the smoke test), and never put the hub behind a reverse
proxy, tunnel or port forward that does not authenticate on its own: every
client would then arrive from the proxy's address and pass the check.

Only one hub may use the proxy at a time. When a hub starts, it drops every
link the proxy holds, so a second hub cuts the first one off, even in the
middle of a brew. Once the server runs the hub, stop running it anywhere else.

Placeholders below: `<server>` is the server's LAN address, `<user>` an
account there with `sudo`, `<dir>` the directory the hub lives in (for
example under a directory that holds your other Compose services).

## Layout on the server

```text
<dir>/                    a clone of this repository, owned by root
  compose.yaml            tracked
  .env                    yours, from .env.example (git-ignored)
  secrets/proxy_psk       uid 10001, mode 400 (git-ignored)
  data/                   recipes and brew records, uid 10001 (git-ignored)
```

Never run `git clean -x` in `<dir>`: it would delete `secrets/` and `data/`.

When `<dir>` sits under `/root`, your own account cannot `cd` into it, so
the commands below name the Compose file instead:
`sudo docker compose -f <dir>/compose.yaml ...` (Compose then reads `.env`
from that directory).

The container runs as uid 10001 with a read-only root, no capabilities and
`no-new-privileges`. It needs no local Bluetooth: never add capabilities,
never mount the host's D-Bus, never make it privileged. On start, a log line
from habluetooth about the Bluetooth management socket is expected and
harmless.

## Before the first install

Read-only checks, because the proxy's key lets anyone who holds it drive the
machine past the hub's write gate:

- who is in the `docker` group (they can read every container's secrets);
- whether any container mounts the Docker socket or runs privileged;
- whether the Docker API listens on TCP;
- whether a backup copies `<dir>` somewhere you would not keep the key;
- that no account on the server already uses uid 10001.

If one of these turns something up, decide that first.

## First install

1. Clone at the commit you reviewed, and check it:

   ```bash
   sudo git clone https://github.com/Nanako0129/xbloom-hub.git <dir>
   sudo git -C <dir> checkout <commit>
   sudo git -C <dir> rev-parse HEAD
   ```

2. `.env` from `.env.example`: the server's address, the proxy's address,
   and the allowed host names and networks. Keep `127.0.0.1` and
   `127.0.0.0/8` in the lists; the healthcheck reads the hub from inside the
   container. Every name is required: Compose stops if one is missing,
   instead of publishing on every interface.

3. `data/` and `secrets/`, owned by uid 10001. The one secret, the proxy
   key, travels in a pipe, never in a command line or a terminal: read it
   from the keychain with
   `security find-generic-password -s <service> -a <account> -w <keychain>`
   into
   `ssh <user>@<server> 'sudo -n install -m 400 -o 10001 -g 10001 /dev/stdin <dir>/secrets/proxy_psk'`
   (with `set -o pipefail`), and only when you are about to start the hub
   for real. It is never written to a file on your own machine.
   Compose ignores `mode` and owner on file secrets (they are bind mounts),
   so the `install` above is what protects them.

4. Build. This does not touch the proxy:

   ```bash
   sudo docker compose -f <dir>/compose.yaml build
   ```

5. A smoke test on the simulator, on another port, that cannot reach the
   proxy or the real data: no proxy key mounted, no proxy address, a
   throwaway data directory.

   ```bash
   sudo docker run --rm -d --name xbloom-hub-smoke \
     --read-only --tmpfs /tmp --tmpfs /data:uid=10001 \
     --cap-drop ALL --security-opt no-new-privileges \
     -e XBLOOM_RADIO=sim -e XBLOOM_PROXY_HOST= \
     -e XBLOOM_ALLOWED_HOSTS=<the same as .env> \
     -e XBLOOM_ALLOWED_NETWORKS=<the same as .env> \
     -p <server>:8378:8377 xbloom-hub:local
   ```

   Then check, in this order:

   - `sudo docker inspect -f '{{.State.Health.Status}}' xbloom-hub-smoke`
     reads `healthy` (after about 20 s);
   - `http://<server>:8378/` opens from a phone and from a computer, and
     the hub's log (`sudo docker logs xbloom-hub-smoke`) names each client
     by its own address. If it shows the Docker bridge's gateway instead,
     see "Host networking" below;
   - the local Bluetooth setup logs and carries on, without asking for
     anything more:

     ```bash
     sudo docker exec xbloom-hub-smoke python -c "import asyncio; from habluetooth import BluetoothManager; asyncio.run(BluetoothManager().async_setup()); print('ok')"
     ```

   Then `sudo docker rm -f xbloom-hub-smoke`, and start the same command
   once more without `-e XBLOOM_RADIO=sim` and without `-d`: it must stop at
   once, saying it cannot read the proxy key, which shows a lost override
   cannot reach the proxy.

6. Stop the hub you ran until now, when it is not brewing. Copy its `data/`
   (from a Mac, without the `._*` files macOS adds to archives):

   ```bash
   COPYFILE_DISABLE=1 tar -C <old checkout> -cf - data \
     | ssh <user>@<server> 'sudo -n tar -C <dir> --no-same-owner -xf - && sudo -n chown -R 10001:10001 <dir>/data'
   ```

7. Install the proxy key (step 3), then start:

   ```bash
   sudo docker compose -f <dir>/compose.yaml up -d
   ```

   The state reaches idle or asleep; the log shows the gate's first code,
   8100; the brew and recipe lists match what you copied.

## Before every start, restart, stop or update

Read the hub's state in the same command and go on only when it is idle,
asleep or offline with no brew, or when the hub does not answer at all
(stopped or crashed: then nothing is brewing through it). A restart in the
middle of a brew loses the record's end and the Stop button.

```bash
st=$(curl -s --max-time 3 http://<server>:8377/api/state \
  | python3 -c "import json,sys
try: d=json.load(sys.stdin)
except ValueError: print('down'); raise SystemExit
print(d['state'] if d.get('brew') is None else 'brew')")
case "$st" in idle|asleep|offline|down) ssh <user>@<server> 'sudo -n docker compose -f <dir>/compose.yaml up -d';;
  *) echo "not now: $st";; esac
```

## Updating

```bash
sudo git -C <dir> fetch
sudo git -C <dir> checkout <commit>
sudo git -C <dir> rev-parse HEAD
sudo docker compose -f <dir>/compose.yaml build
```

Then `up -d` behind the state check above.

## Rolling back

`sudo docker compose down` behind the state check (`down`, not `stop`: a
stopped container comes back after a reboot). Copy back any records written
on the server, then start the hub where it ran before.

## Claude Code

The repository's `.mcp.json` points at a hub on the same machine. To use the
server instead, add a local-scope entry of the same name, which Claude Code
prefers over the project's; it is stored in Claude Code's own config, not in
the repository. If your Claude Code settings set `HTTPS_PROXY` (a model
relay, for example), add the server's address to `NO_PROXY` there too:
Claude Code sends the MCP request through that proxy otherwise, and it never
reaches the server (seen 2026-11-16):

```bash
claude mcp add-json --scope local xbloom-hub \
  '{"type":"http","url":"http://<server>:8377/mcp","headers":{"X-Hub-Request":"1"}}'
```

An entry made before the "No pairing" change names a `headersHelper`
(`./tools/mcp-auth-headers.sh`) that no longer exists, and sends no
`X-Hub-Request`: every call would get 403. Replace it:

```bash
claude mcp remove --scope local xbloom-hub
claude mcp add-json --scope local xbloom-hub \
  '{"type":"http","url":"http://<server>:8377/mcp","headers":{"X-Hub-Request":"1"}}'
```

## Opening the page

Open `http://<server>:8377/` on any device in an allowed network. There is
nothing to pair (docs/DASHBOARD.md).

## Host networking

Docker normally keeps a LAN client's own address on a published port. If the
smoke test shows the bridge's gateway instead, the hub cannot tell clients
apart, and widening its allowed networks to the bridge would let everyone
in. The fallback is `network_mode: host` with
`XBLOOM_BIND: ${XBLOOM_PUBLISH_IP:?...}` in `environment` (the image's
default is `0.0.0.0`) and the healthcheck reading that address. It also lets
the container reach services the server binds to its own loopback, which is
why it is not the default.

## What crosses the LAN

The page, REST and MCP are plain HTTP. The one secret on the wire is the
one-time start ticket in the answer to a load: anyone who can see the
LAN's traffic could use it to start that brew first. Any allowed client
can load and start a brew anyway, so this adds no reach, only timing.

## Checking that no secret is in the image or the container

Count, never print. The proxy key must count 0, and the known string first
must count more than 0, or a 0 means nothing (newer Docker saves images with
compressed layers, which `tools/count-in-image.py` opens one by one):

```bash
printf 'proxy_headers=False' > /tmp/known
sudo docker image save xbloom-hub:local | sudo python3 <dir>/tools/count-in-image.py /tmp/known
sudo docker image save xbloom-hub:local | sudo python3 <dir>/tools/count-in-image.py <dir>/secrets/proxy_psk
sudo docker inspect xbloom-hub-hub-1 | sudo grep -c -F -f <dir>/secrets/proxy_psk
```

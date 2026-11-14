"""Run the hub. Configuration is environment variables only:

XBLOOM_HUB_TOKEN_FILE   file holding the bearer token (required)
XBLOOM_PROXY_HOST       IP address of the ESPHome Bluetooth proxy
XBLOOM_PROXY_PSK        the proxy's Noise key (or XBLOOM_PROXY_PSK_FILE)
XBLOOM_RADIO            proxy (default) | local | sim
XBLOOM_DATA_DIR         recipes in <dir>/recipes, brew records in <dir>/brews
                        (default ./data)
XBLOOM_ALLOWED_HOSTS    comma-separated Host names the hub answers to, exact,
                        no wildcard (default localhost,127.0.0.1)
XBLOOM_SIM_BREW_S       with XBLOOM_RADIO=sim, how long a simulated brew
                        takes in seconds (default 45)
XBLOOM_ALLOWED_NETWORKS comma-separated client networks the hub answers,
                        e.g. 127.0.0.0/8,192.168.1.0/24 (default: this host only)
XBLOOM_BIND, XBLOOM_PORT   default 127.0.0.1, 8377
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

import uvicorn

from .app import BUILD_COMMAND, WEB_DIR, check_allowed_hosts, create_app, parse_networks, read_token
from .brews import BrewStore
from .machine import Machine
from .recipes import RecipeStore


def _proxy_psk(env) -> str:
    if path := env.get("XBLOOM_PROXY_PSK_FILE"):
        return Path(path).read_text().strip()
    return env.get("XBLOOM_PROXY_PSK", "")


def main() -> None:
    env = os.environ
    if not (WEB_DIR / "index.html").is_file():
        # Never an empty site: say what to do instead.
        raise SystemExit(f"the dashboard is not built ({WEB_DIR} has no index.html); "
                         f"from the repository, run: {BUILD_COMMAND}")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    token = read_token(env)
    data = Path(env.get("XBLOOM_DATA_DIR", "data"))
    brews = BrewStore(data / "brews")
    hosts = check_allowed_hosts(
        [h.strip() for h in env.get("XBLOOM_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")])

    kind = env.get("XBLOOM_RADIO", "proxy")
    if kind == "sim":
        from .sim import SimListener, SimRadio

        radio = SimRadio()
        machine = Machine(radio, SimListener, brews)
    elif kind == "local":
        from .radio import LocalRadio

        radio = LocalRadio()
        machine = Machine(radio, brews=brews)
    elif kind == "proxy":
        from .radio import ProxyRadio

        try:
            radio = ProxyRadio(env.get("XBLOOM_PROXY_HOST", ""), _proxy_psk(env))
        except ValueError as err:
            raise SystemExit(f"XBLOOM_PROXY_HOST and XBLOOM_PROXY_PSK: {err}") from err
        machine = Machine(radio, brews=brews)
    else:
        raise SystemExit(f"unknown XBLOOM_RADIO: {kind}")

    store = RecipeStore(data / "recipes")
    networks = parse_networks(env.get("XBLOOM_ALLOWED_NETWORKS", "127.0.0.0/8,::1/128"))
    app = create_app(machine, store, token, hosts, radio, brews, allowed_networks=networks)
    uvicorn.run(app, host=env.get("XBLOOM_BIND", "127.0.0.1"),
                port=int(env.get("XBLOOM_PORT", "8377")), log_level="info",
                # An open event stream never ends by itself; without this a
                # stop waits for every dashboard to close (seen in the S1 run).
                timeout_graceful_shutdown=3)


if __name__ == "__main__":
    main()

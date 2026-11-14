"""HTTP surface: REST for commands, Server-Sent Events for state."""
from __future__ import annotations

import asyncio
import ipaddress
import contextlib
import hmac
import json
import logging
from collections.abc import Mapping
from pathlib import Path
from urllib.parse import urlsplit

from starlette.applications import Starlette
from starlette.datastructures import Headers
from starlette.middleware import Middleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles
from . import errors
from .machine import Machine
from .errors import BodyTooLarge
from .pairing import LIFETIME_S, Pairing
from . import gate
from .recipes import RecipeInvalid, RecipeStore, as_sent, plan

TOKEN_FILE_VAR = "XBLOOM_HUB_TOKEN_FILE"
MCP_PATH = "/mcp"
# The one write that needs no token: a phone trading a pairing code for it
# (docs/PLAN.md, S3d-1). Exact method and path, nothing near it.
CLAIM = ("POST", "/api/pair/claim")
CLAIM_BODY_CAP = 1024
log = logging.getLogger(__name__)
# The dashboard, built from frontend/ by `frontend/npm.sh run build` (it is
# git-ignored, and shipped in the sdist and the wheel). The hub refuses to
# start without it (__main__); an app made for a test serves what is there.
WEB_DIR = Path(__file__).parent / "web"
BUILD_COMMAND = "frontend/npm.sh ci && frontend/npm.sh run build"

# No inline script or style, nothing from another origin, no framing. The
# page puts every string it did not write itself into the document as text;
# this is the backstop if that is ever got wrong (security review P1-4).
CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
       "connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")


class SecurityHeaders:
    def __init__(self, app) -> None:
        self._app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self._app(scope, receive, send)

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                message["headers"] = [*message.get("headers", []),
                                      (b"content-security-policy", CSP.encode()),
                                      (b"x-content-type-options", b"nosniff"),
                                      (b"referrer-policy", b"no-referrer"),
                                      (b"cache-control", b"no-store")]
            await send(message)

        return await self._app(scope, receive, send_with_headers)


def network_hosts(hosts: list[str]) -> list[str]:
    """The allowed host names a phone could reach: not this host's loopback.
    A page opened at 127.0.0.1 puts one of these in the pairing QR code."""
    def loopback(host: str) -> bool:
        try:
            return ipaddress.ip_address(host.strip("[]")).is_loopback
        except ValueError:
            return host == "localhost"
    return [h for h in hosts if not loopback(h)]


def check_allowed_hosts(hosts: list[str]) -> list[str]:
    """Refuse a list that would switch the Host check off (P1-3)."""
    if not hosts or any(not h or "*" in h for h in hosts):
        raise SystemExit("XBLOOM_ALLOWED_HOSTS must list exact host names; "
                         "no empty entry and no wildcard")
    return hosts


def read_token(env: Mapping[str, str]) -> str:
    """Read the bearer token, or stop the process. Auth fails closed: an unset
    variable, a missing file or an empty one must never mean "no token needed".
    """
    path = env.get(TOKEN_FILE_VAR)
    if not path:
        raise SystemExit(f"{TOKEN_FILE_VAR} is not set")
    try:
        token = Path(path).read_text().strip()
    except OSError as err:
        raise SystemExit(f"cannot read the token file: {err}") from err
    if not token:
        raise SystemExit("the token file is empty")
    return token


class ClientNetworks:
    """Answers only clients whose address is in one of the given networks.

    The hub may listen on every interface so a phone on the home network can
    reach it; this keeps everyone else out, whichever network the host is on
    (a Mac also joins other Wi-Fi). Checked before anything else.
    """

    def __init__(self, app, networks) -> None:
        self._app = app
        self._networks = tuple(networks)

    async def __call__(self, scope, receive, send):
        if scope["type"] in ("http", "websocket"):
            client = (scope.get("client") or ("", 0))[0]
            try:
                address = ipaddress.ip_address(client)
            except ValueError:
                address = None
            # A dual-stack listener reports an IPv4 client as ::ffff:a.b.c.d.
            if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
                address = address.ipv4_mapped
            if address is None or not any(address in network for network in self._networks):
                if scope["type"] == "http":
                    return await JSONResponse({"error": "network_not_allowed"}, 403)(scope, receive, send)
                return await send({"type": "websocket.close", "code": 1008})
        return await self._app(scope, receive, send)


# This host only: what the hub answers unless told otherwise.
LOOPBACK = "127.0.0.0/8,::1/128"


def parse_networks(text: str) -> list:
    """XBLOOM_ALLOWED_NETWORKS: comma-separated networks such as
    127.0.0.0/8,192.168.123.0/24. A network that would let in everyone is refused."""
    networks = []
    for item in (part.strip() for part in text.split(",")):
        if not item:
            continue
        try:
            network = ipaddress.ip_network(item, strict=True)
        except ValueError as err:
            raise SystemExit(f"XBLOOM_ALLOWED_NETWORKS: {item!r} is not a network ({err})") from err
        if network.prefixlen == 0:
            raise SystemExit("XBLOOM_ALLOWED_NETWORKS: a network of every address is refused")
        networks.append(network)
    if not networks:
        raise SystemExit("XBLOOM_ALLOWED_NETWORKS lists no network")
    return networks


class Guard:
    """Token on every request that can change something; no cross-origin calls.

    The token is accepted only in the Authorization header. A page on another
    origin cannot set that header without a CORS preflight, and this app never
    answers one (security review P1-3).
    """

    def __init__(self, app, token: str) -> None:
        if not token:
            raise ValueError("empty token")
        self._app = app
        self._token = token.encode()

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self._app(scope, receive, send)
        headers = Headers(scope=scope)
        origin = headers.get("origin")
        if origin is not None and urlsplit(origin).netloc != headers.get("host"):
            return await JSONResponse({"error": "foreign_origin"}, 403)(scope, receive, send)
        # Reads are open; anything that can change something needs the token.
        # Every MCP request needs it whatever its method: its GET is a stream
        # of tool traffic, not a read of public state.
        if (scope["method"], scope["path"]) == CLAIM:
            return await self._app(scope, receive, send)
        if scope["method"] not in ("GET", "HEAD") or scope["path"].startswith(MCP_PATH):
            scheme, _, presented = headers.get("authorization", "").partition(" ")
            if (scheme != "Bearer" or not presented
                    or not hmac.compare_digest(presented.encode(), self._token)):
                return await JSONResponse({"error": "unauthorized"}, 401)(scope, receive, send)
        return await self._app(scope, receive, send)


def create_app(machine: Machine, store: RecipeStore, token: str,
               allowed_hosts: list[str], radio=None, brews=None, allowed_networks=None) -> Starlette:
    # No networks given: this host only. Never "no check".
    allowed_networks = allowed_networks or parse_networks(LOOPBACK)
    check_allowed_hosts(allowed_hosts)
    pairing = Pairing()

    async def state(request):
        return JSONResponse(machine.snapshot())

    async def events(request):
        queue = machine.subscribe()

        async def stream():
            try:
                yield f"data: {json.dumps(machine.snapshot())}\n\n"
                while True:
                    try:
                        snap = await asyncio.wait_for(queue.get(), 15)
                    except asyncio.TimeoutError:
                        yield ": keepalive\n\n"
                        continue
                    yield f"data: {json.dumps(snap)}\n\n"
            finally:
                machine.unsubscribe(queue)

        return StreamingResponse(stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-store"})

    async def recipe_list(request):
        return JSONResponse(store.list())

    async def library(request):
        return JSONResponse(store.library())

    async def recipe_item(request):
        slug = request.path_params["slug"]
        if request.method == "GET":
            recipe = store.get(slug)
            return JSONResponse(recipe) if recipe else JSONResponse({"error": "not_found"}, 404)
        if request.method == "DELETE":
            errors.guard_loaded(machine, slug)
            return JSONResponse({"deleted": store.delete(slug)})
        body = await _json(request)
        # After the body is read: nothing may await between this check and
        # the write, or a load could finish in the gap.
        errors.guard_loaded(machine, slug)
        # ?create=1: a new recipe, refused if the slug has a file already.
        return JSONResponse(store.put(slug, body, create=request.query_params.get("create") == "1"))

    async def load(request):
        slug = (await _json(request)).get("slug")
        if store.get(slug) is None:
            return JSONResponse({"error": "not_found"}, 404)
        return JSONResponse(await machine.load(slug, lambda: store.for_load(slug)))

    async def cancel(request):
        return JSONResponse(await machine.cancel())

    async def start(request):
        # The ticket came from the load this caller made. It is never in a
        # GET response or the event stream.
        return JSONResponse(await machine.start((await _json(request)).get("ticket")))

    async def stop(request):
        return JSONResponse(await machine.stop())

    async def recipe_plan(request):
        recipe = store.get(request.path_params["slug"])
        if recipe is None:
            return JSONResponse({"error": "not_found"}, 404)
        return JSONResponse({"recipe": recipe, "as_sent": as_sent(recipe), "plan": plan(recipe)})

    async def gate_recent(request):
        return JSONResponse([{"at": at, "code": code} for at, code in gate.RECENT])

    async def brew_list(request):
        return JSONResponse(brews.list() if brews else [])

    async def brew_item(request):
        brew_id = request.path_params["brew_id"]
        # The running brew from memory (its file lags by up to a checkpoint), else the file.
        record = machine.open_record(brew_id) or (brews.get(brew_id) if brews else None)
        if record is None:
            return JSONResponse({"error": "not_found"}, 404)
        # The planned line for the chart, from the recipe the brew carried.
        record["plan"] = plan(record["recipe"]) if record.get("recipe") else None
        return JSONResponse(record)

    async def release(request):
        await machine.release()
        return JSONResponse(machine.snapshot())

    async def acquire(request):
        await machine.acquire()
        return JSONResponse(machine.snapshot())

    async def pair_start(request):
        return JSONResponse({"code": pairing.start(), "expires_in_s": LIFETIME_S,
                             "hosts": network_hosts(allowed_hosts)})

    async def pair_cancel(request):
        pairing.cancel()
        return JSONResponse({"state": pairing.status()})

    async def pair_status(request):
        return JSONResponse({"state": pairing.status()})

    async def pair_claim(request):
        client = (request.client or ("?", 0))[0]
        try:
            # Read under the cap; then spend the code with no await in between.
            try:
                code = (await _json(request, cap=CLAIM_BODY_CAP)).get("code")
            except RecipeInvalid:
                code = None          # not a JSON object: as wrong as a wrong code
            pairing.claim(code)
        except Exception as err:
            # Never the code, never the token: who asked and that it failed.
            log.info("pairing claim from %s: refused (%s)", client, type(err).__name__)
            raise
        log.info("pairing claim from %s: paired", client)
        return JSONResponse({"token": token})

    async def _json(request, cap: int | None = None) -> dict:
        if cap is None:
            raw = await request.body()
        else:
            # Refused before it is read in full: by the length it states (so a
            # client cannot state a large body and hold the request open), or
            # as soon as what arrives passes the cap (a chunked body states none).
            length = request.headers.get("content-length")
            if length is not None and (not length.isdigit() or int(length) > cap):
                raise BodyTooLarge(f"over {cap} bytes")
            buffer = bytearray()
            async for chunk in request.stream():
                buffer += chunk
                if len(buffer) > cap:
                    raise BodyTooLarge(f"over {cap} bytes")
            raw = bytes(buffer)
        try:
            body = json.loads(raw)
        except ValueError:
            body = None
        if not isinstance(body, dict):
            raise RecipeInvalid({"body": "not_a_json_object"})
        return body

    async def failure(request, exc):
        return JSONResponse(errors.describe(exc), errors.lookup(exc)[0])

    from mcp.server.transport_security import TransportSecuritySettings

    from .mcp_tools import build_server

    mcp_server = build_server(machine, store)
    # Stateless JSON responses: one request, one answer, nothing to resume.
    # The SDK's own Host check is tied to a host it is told it binds to; this
    # app is the one that binds, and TrustedHostMiddleware plus Guard below
    # already sit in front of this route, so the SDK's check is turned off
    # rather than left guessing.
    mcp_app = mcp_server.streamable_http_app(
        streamable_http_path=MCP_PATH, json_response=True, stateless_http=True,
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )

    @contextlib.asynccontextmanager
    async def lifespan(app):
        task = asyncio.create_task(machine.run())
        try:
            # A mounted or borrowed MCP route does not run the SDK app's own
            # lifespan, so its session manager is entered here.
            async with mcp_server.session_manager.run():
                yield
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
            if radio is not None:
                await radio.stop()

    return Starlette(
        routes=[
            Route("/api/state", state),
            Route("/api/events", events),
            Route("/api/recipes", recipe_list),
            Route("/api/library", library),
            Route("/api/recipes/{slug}", recipe_item, methods=["GET", "PUT", "DELETE"]),
            Route("/api/recipes/{slug}/plan", recipe_plan),
            Route("/api/gate", gate_recent),
            Route("/api/machine/load", load, methods=["POST"]),
            Route("/api/machine/cancel", cancel, methods=["POST"]),
            Route("/api/machine/start", start, methods=["POST"]),
            Route("/api/machine/stop", stop, methods=["POST"]),
            Route("/api/brews", brew_list),
            Route("/api/brews/{brew_id}", brew_item),
            Route("/api/link/release", release, methods=["POST"]),
            Route("/api/link/acquire", acquire, methods=["POST"]),
            Route("/api/pair/start", pair_start, methods=["POST"]),
            Route("/api/pair/cancel", pair_cancel, methods=["POST"]),
            Route("/api/pair/status", pair_status, methods=["POST"]),
            Route(CLAIM[1], pair_claim, methods=["POST"]),
            # The SDK's route itself, at /mcp, rather than a Mount (which
            # would answer at /mcp/ and redirect /mcp).
            *mcp_app.routes,
            # Last: the dashboard's own files.
            Mount("/", app=StaticFiles(directory=WEB_DIR, html=True, check_dir=False), name="web"),
        ],
        middleware=[
            # Every response, refusals included, carries the security headers.
            Middleware(SecurityHeaders),
            # Then a client outside the allowed networks gets nothing at all.
            Middleware(ClientNetworks, networks=allowed_networks),
            # Refuses a Host header that is not ours: DNS-rebinding protection.
            Middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts),
            Middleware(Guard, token=token),
        ],
        exception_handlers={exc: failure for exc in errors.TABLE},
        lifespan=lifespan,
    )

"""HTTP surface: REST for commands, Server-Sent Events for state."""
from __future__ import annotations

import asyncio
import ipaddress
import contextlib
import json
import logging
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
from .community import LIST_FILTERS, Community
from .machine import Machine
from .errors import BodyTooLarge
from . import gate
from .recipes import RecipeInvalid, RecipeStore, as_sent, plan

MCP_PATH = "/mcp"
# These routes make the hub call xBloom, so even their GETs need the header
# below (docs/PLAN.md, S3b; "No pairing", 2026-11-22).
COMMUNITY_PATH = "/api/community"
IMPORT_BODY_CAP = 1024
# Not a secret. A page on another origin cannot put a custom header on a
# no-cors request (<img>, <script>, a form post), and a cors request carrying
# one needs a preflight this app never answers. The dashboard and the MCP
# client send it; see Guard.
HUB_HEADER = "x-hub-request"
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


def check_allowed_hosts(hosts: list[str]) -> list[str]:
    """Refuse a list that would switch the Host check off (P1-3)."""
    if not hosts or any(not h or "*" in h for h in hosts):
        raise SystemExit("XBLOOM_ALLOWED_HOSTS must list exact host names; "
                         "no empty entry and no wildcard")
    return hosts


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
    """No authentication: the allowed networks are the gate (docs/PLAN.md,
    "No pairing", 2026-11-22). This keeps a page on another site, open in a
    browser on the LAN, from making the hub act.

    - A present Origin that is not this host is refused (``Origin: null``
      included). Browsers send Origin on every cross-origin non-GET request.
    - Every request that can change something, every request under /mcp (its
      GET is a tool stream) and every request under /api/community (it makes
      the hub call xBloom) must carry HUB_HEADER. A no-cors GET such as
      ``<img src>`` carries no Origin, so this is what stops it. Sec-Fetch-Site
      is not used: browsers send it only to https or localhost, and the hub is
      plain http on a LAN address.
    - TrustedHostMiddleware, before this, is the DNS-rebinding defence: a
      rebound page has Origin equal to Host, so only the Host check refuses it.
    """

    def __init__(self, app) -> None:
        self._app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self._app(scope, receive, send)
        headers = Headers(scope=scope)
        origin = headers.get("origin")
        if origin is not None and urlsplit(origin).netloc != headers.get("host"):
            return await JSONResponse({"error": "foreign_origin"}, 403)(scope, receive, send)
        path = scope["path"]
        if (scope["method"] not in ("GET", "HEAD") or path.startswith(MCP_PATH)
                or path == COMMUNITY_PATH or path.startswith(COMMUNITY_PATH + "/")):
            if headers.get(HUB_HEADER) != "1":
                return await JSONResponse({"error": "missing_hub_header"}, 403)(scope, receive, send)
        return await self._app(scope, receive, send)


def create_app(machine: Machine, store: RecipeStore,
               allowed_hosts: list[str], radio=None, brews=None, allowed_networks=None,
               community: Community | None = None) -> Starlette:
    # No networks given: this host only. Never "no check".
    allowed_networks = allowed_networks or parse_networks(LOOPBACK)
    check_allowed_hosts(allowed_hosts)
    community = community or Community()

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

    async def scale_enter(request):
        return JSONResponse(await machine.scale_enter())

    async def tare(request):
        return JSONResponse(await machine.tare())

    async def scale_quit(request):
        return JSONResponse(await machine.scale_quit())

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

    async def community_criteria(request):
        return JSONResponse(await community.criteria())

    async def community_search(request):
        query = request.query_params
        page = query.get("page", "1")
        return JSONResponse(await community.search(
            # isdecimal and isascii: "²" is a digit to isdigit() and not to int().
            keyword=query.get("keyword"), page=int(page) if page.isascii() and page.isdecimal() and len(page) <= 3 else 0,
            sort=query.get("sort", "date"), kind=query.get("kind") or None,
            source=query.get("source") or None, machine=query.get("machine") or None,
            filters={key: query.getlist(key) for key in LIST_FILTERS if query.getlist(key)}))

    async def community_item(request):
        return JSONResponse(await community.detail(request.path_params["id"]))

    async def community_import(request):
        body = await _json(request, cap=IMPORT_BODY_CAP)
        # Saves under a new slug only; never loads the machine.
        slug = await community.import_recipe(store, body.get("slug"), body.get("id"),
                                             body.get("share_url"))
        return JSONResponse({"slug": slug})

    async def failure(request, exc):
        return JSONResponse(errors.describe(exc), errors.lookup(exc)[0])

    from mcp.server.transport_security import TransportSecuritySettings

    from .mcp_tools import build_server

    mcp_server = build_server(machine, store, community)
    # Stateless JSON responses: one request, one answer, nothing to resume.
    # The SDK's own Host check is tied to a host it is told it binds to; this
    # app is the one that binds, and TrustedHostMiddleware plus Guard below
    # (Host and Origin, the same two checks the SDK makes) already sit in
    # front of this route, so the SDK's check is turned off rather than left
    # guessing.
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
            try:
                if radio is not None:
                    await radio.stop()
            finally:
                await community.aclose()

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
            # Mounted, so a GET is answered 405 here rather than falling
            # through to the dashboard's files below (S3e, P3-2).
            Mount("/api/scale", routes=[
                Route("/enter", scale_enter, methods=["POST"]),
                Route("/tare", tare, methods=["POST"]),
                Route("/quit", scale_quit, methods=["POST"]),
            ]),
            Route("/api/brews", brew_list),
            Route("/api/brews/{brew_id}", brew_item),
            Route("/api/link/release", release, methods=["POST"]),
            Route("/api/link/acquire", acquire, methods=["POST"]),
            Route(f"{COMMUNITY_PATH}/criteria", community_criteria),
            Route(f"{COMMUNITY_PATH}/search", community_search),
            Route(f"{COMMUNITY_PATH}/{{id:int}}", community_item),
            Route(f"{COMMUNITY_PATH}/import", community_import, methods=["POST"]),
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
            Middleware(Guard),
        ],
        exception_handlers={exc: failure for exc in errors.TABLE},
        lifespan=lifespan,
    )

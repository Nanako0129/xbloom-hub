"""HTTP surface: REST for commands, Server-Sent Events for state."""
from __future__ import annotations

import asyncio
import contextlib
import hmac
import json
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
from .recipes import RecipeInvalid, RecipeStore

TOKEN_FILE_VAR = "XBLOOM_HUB_TOKEN_FILE"
MCP_PATH = "/mcp"
STATIC_DIR = Path(__file__).parent / "static"

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
        if scope["method"] not in ("GET", "HEAD") or scope["path"].startswith(MCP_PATH):
            scheme, _, presented = headers.get("authorization", "").partition(" ")
            if (scheme != "Bearer" or not presented
                    or not hmac.compare_digest(presented.encode(), self._token)):
                return await JSONResponse({"error": "unauthorized"}, 401)(scope, receive, send)
        return await self._app(scope, receive, send)


def create_app(machine: Machine, store: RecipeStore, token: str,
               allowed_hosts: list[str], radio=None, brews=None) -> Starlette:
    check_allowed_hosts(allowed_hosts)

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
        return JSONResponse(store.put(slug, body))

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

    async def brew_list(request):
        return JSONResponse(brews.list() if brews else [])

    async def brew_item(request):
        record = brews.get(request.path_params["brew_id"]) if brews else None
        return JSONResponse(record) if record else JSONResponse({"error": "not_found"}, 404)

    async def release(request):
        await machine.release()
        return JSONResponse(machine.snapshot())

    async def acquire(request):
        await machine.acquire()
        return JSONResponse(machine.snapshot())

    async def _json(request) -> dict:
        try:
            body = await request.json()
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
            Route("/api/recipes/{slug}", recipe_item, methods=["GET", "PUT", "DELETE"]),
            Route("/api/machine/load", load, methods=["POST"]),
            Route("/api/machine/cancel", cancel, methods=["POST"]),
            Route("/api/machine/start", start, methods=["POST"]),
            Route("/api/machine/stop", stop, methods=["POST"]),
            Route("/api/brews", brew_list),
            Route("/api/brews/{brew_id}", brew_item),
            Route("/api/link/release", release, methods=["POST"]),
            Route("/api/link/acquire", acquire, methods=["POST"]),
            # The SDK's route itself, at /mcp, rather than a Mount (which
            # would answer at /mcp/ and redirect /mcp).
            *mcp_app.routes,
            # Last: the dashboard's own files.
            Mount("/", app=StaticFiles(directory=STATIC_DIR, html=True), name="static"),
        ],
        middleware=[
            # Refuses a Host header that is not ours: DNS-rebinding protection.
            Middleware(SecurityHeaders),
            Middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts),
            Middleware(Guard, token=token),
        ],
        exception_handlers={exc: failure for exc in errors.TABLE},
        lifespan=lifespan,
    )

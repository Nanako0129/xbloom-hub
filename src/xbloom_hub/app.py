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
from starlette.routing import Route
from xbloom.ble import CommandRefused, CommandUnanswered

from .gate import GateRefused
from .machine import CancelFailed, Machine, NotReady
from .recipes import BadSlug, RecipeInvalid, RecipeStore, to_machine

TOKEN_FILE_VAR = "XBLOOM_HUB_TOKEN_FILE"
MCP_PATH = "/mcp"


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
               allowed_hosts: list[str], radio=None) -> Starlette:

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
            return JSONResponse({"deleted": store.delete(slug)})
        return JSONResponse(store.put(slug, await _json(request)))

    async def load(request):
        slug = (await _json(request)).get("slug")
        recipe = store.get(slug)
        if recipe is None:
            return JSONResponse({"error": "not_found"}, 404)
        return JSONResponse(await machine.load(slug, to_machine(recipe)))

    async def cancel(request):
        return JSONResponse(await machine.cancel())

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

    def _error(status: int, name: str):
        async def handler(request, exc):
            body = {"error": name, "detail": str(exc)}
            if isinstance(exc, RecipeInvalid):
                body["fields"] = exc.errors
            return JSONResponse(body, status)
        return handler

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
            Route("/api/link/release", release, methods=["POST"]),
            Route("/api/link/acquire", acquire, methods=["POST"]),
            # The SDK's route itself, at /mcp, rather than a Mount (which
            # would answer at /mcp/ and redirect /mcp).
            *mcp_app.routes,
        ],
        middleware=[
            # Refuses a Host header that is not ours: DNS-rebinding protection.
            Middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts),
            Middleware(Guard, token=token),
        ],
        exception_handlers={
            BadSlug: _error(400, "bad_slug"),
            RecipeInvalid: _error(422, "recipe_invalid"),
            NotReady: _error(409, "not_ready"),
            CancelFailed: _error(502, "cancel_failed"),
            CommandRefused: _error(502, "machine_refused"),
            CommandUnanswered: _error(502, "machine_unanswered"),
            GateRefused: _error(500, "gate_refused"),
        },
        lifespan=lifespan,
    )

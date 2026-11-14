"""MCP tools for an AI assistant. Thin wrappers over the recipe store and the
machine. There is deliberately no tool that starts, pauses or resumes a brew,
and the start ticket a load produces is never returned here: starting is a
person's decision, made at the machine or in the dashboard.
"""
from __future__ import annotations

from mcp.server.mcpserver import MCPServer

from . import errors
from .machine import Machine
from .recipes import RecipeStore, as_sent, checked

INSTRUCTIONS = (
    "Recipes and machine state for an xBloom Studio coffee machine. "
    "load_recipe only puts a recipe on the machine; it never starts a brew, and "
    "no tool here can. A person starts the brew at the machine."
)


def _failure(err: Exception) -> dict:
    body = {"ok": False, **errors.describe(err)}
    if body["error"] == "bad_slug":
        body["detail"] = "use lowercase letters, digits and hyphens, at most 64 characters"
    return body


_EXPECTED = tuple(errors.TABLE)


def build_server(machine: Machine, store: RecipeStore) -> MCPServer:
    server = MCPServer("xbloom-hub", instructions=INSTRUCTIONS)

    @server.tool(description="List the recipes in the library: slug and name of each.")
    async def list_recipes() -> dict:
        return {"ok": True, "recipes": store.list()}

    @server.tool(description="Read one recipe from the library by its slug.")
    async def get_recipe(slug: str) -> dict:
        try:
            recipe = store.get(slug)
        except _EXPECTED as err:
            return _failure(err)
        return {"ok": True, "recipe": recipe} if recipe else {"ok": False, "error": "not_found"}

    @server.tool(description="Check a recipe against the machine's limits without saving it. "
                             "Returns the normalised recipe and, under as_sent, the dose, ratio and "
                             "pour volumes the machine will actually be given; or an error "
                             "code per field.")
    async def validate_recipe(recipe: dict) -> dict:
        try:
            recipe = checked(recipe)
            return {"ok": True, "recipe": recipe, "as_sent": as_sent(recipe)}
        except _EXPECTED as err:
            return _failure(err)

    @server.tool(description="Validate a recipe and store it in the library under a slug, "
                             "replacing any recipe already stored under that slug.")
    async def save_recipe(slug: str, recipe: dict) -> dict:
        try:
            errors.guard_loaded(machine, slug)
            recipe = store.put(slug, recipe)
            return {"ok": True, "slug": slug, "recipe": recipe, "as_sent": as_sent(recipe)}
        except _EXPECTED as err:
            return _failure(err)

    @server.tool(description="Remove a recipe from the library.")
    async def delete_recipe(slug: str) -> dict:
        try:
            errors.guard_loaded(machine, slug)
            return {"ok": True, "deleted": store.delete(slug)}
        except _EXPECTED as err:
            return _failure(err)

    @server.tool(description="The machine's current state: offline, idle, asleep, armed "
                             "(a recipe is loaded and waiting), brewing, finished (the screen after "
                             "a brew; a load has to wait for idle), busy or released. "
                             "weight_g is "
                             "the scale's reading after its last tare and is 0 with a cup on "
                             "it once a recipe has been loaded; it does not show whether a "
                             "cup is present.")
    async def machine_state() -> dict:
        return {"ok": True, **machine.snapshot()}

    @server.tool(description="Put a stored recipe on the machine. This does not start a brew: "
                             "the machine waits for a person to start it at the machine. "
                             "Only works while the machine is idle or asleep.")
    async def load_recipe(slug: str) -> dict:
        try:
            if store.get(slug) is None:
                return {"ok": False, "error": "not_found"}

            # Raises unless the machine reports it is waiting, so ok means armed.
            await machine.load(slug, lambda: store.for_load(slug))
        except _EXPECTED as err:
            return _failure(err)
        # machine.load() returns a one-time start ticket. It stays in the hub.
        return {"ok": True, **machine.snapshot()}

    @server.tool(description="Take back a recipe that was loaded and has not been started. "
                             "Only works while the machine is armed.")
    async def cancel_load() -> dict:
        try:
            snapshot = await machine.cancel()
        except _EXPECTED as err:
            return _failure(err)
        if snapshot["state"] == "armed":
            return {**snapshot, "ok": False, "error": "not_confirmed",
                    "detail": "the cancel was sent but the machine still reports the recipe "
                              "as loaded"}
        return {"ok": True, **snapshot}

    return server

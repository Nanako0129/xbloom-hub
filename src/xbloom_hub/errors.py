"""One table of the failures a caller can cause, shared by REST and MCP so a
failure has the same name on both."""
from __future__ import annotations

from xbloom.ble import CommandRefused, CommandUnanswered

from .gate import GateRefused
from .machine import CancelFailed, NotReady
from .recipes import BadSlug, RecipeInvalid


class RecipeLoaded(RuntimeError):
    """The recipe under this slug is the one loaded on the machine right now."""


# exception -> (HTTP status, name)
TABLE: dict[type[Exception], tuple[int, str]] = {
    BadSlug: (400, "bad_slug"),
    RecipeInvalid: (422, "recipe_invalid"),
    NotReady: (409, "not_ready"),
    RecipeLoaded: (409, "recipe_is_loaded"),
    CancelFailed: (502, "cancel_failed"),
    CommandRefused: (502, "machine_refused"),
    CommandUnanswered: (502, "machine_unanswered"),
    GateRefused: (500, "gate_refused"),
}


def describe(err: Exception) -> dict:
    body = {"error": TABLE[type(err)][1], "detail": str(err)}
    if isinstance(err, RecipeInvalid):
        body["fields"] = err.errors
    return body


def guard_loaded(machine, slug: str) -> None:
    """Changing or deleting the recipe a person is about to start would leave
    the machine brewing something the library no longer says it holds."""
    loaded = machine.snapshot().get("recipe") or {}
    if loaded.get("slug") == slug:
        raise RecipeLoaded(f"'{slug}' is loaded on the machine; cancel the load first")

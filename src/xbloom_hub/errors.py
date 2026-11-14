"""One table of the failures a caller can cause, shared by REST and MCP so a
failure has the same name on both."""
from __future__ import annotations

from xbloom.ble import CommandRefused, CommandUnanswered

from .brews import BadBrewId
from .gate import GateRefused
from .machine import BadTicket, CancelFailed, NoCup, NotReady
from .recipes import BadSlug, RecipeExists, RecipeInvalid


class RecipeLoaded(RuntimeError):
    """The recipe under this slug is the one loaded on the machine right now."""


# exception -> (HTTP status, name)
TABLE: dict[type[Exception], tuple[int, str]] = {
    BadSlug: (400, "bad_slug"),
    RecipeInvalid: (422, "recipe_invalid"),
    RecipeExists: (409, "recipe_exists"),
    BadBrewId: (400, "bad_brew_id"),
    NotReady: (409, "not_ready"),
    BadTicket: (403, "bad_ticket"),
    NoCup: (409, "no_cup"),
    RecipeLoaded: (409, "recipe_is_loaded"),
    CancelFailed: (502, "cancel_failed"),
    CommandRefused: (502, "machine_refused"),
    CommandUnanswered: (502, "machine_unanswered"),
    GateRefused: (500, "gate_refused"),
}


def lookup(err: Exception) -> tuple[int, str]:
    # By class hierarchy, as the callers catch: a subclass added later must
    # not turn into a KeyError.
    return next(TABLE[cls] for cls in type(err).__mro__ if cls in TABLE)


def describe(err: Exception) -> dict:
    body = {"error": lookup(err)[1], "detail": str(err)}
    if isinstance(err, RecipeInvalid):
        body["fields"] = err.errors
    return body


def guard_loaded(machine, slug: str) -> None:
    """Changing or deleting the recipe a person is about to start would leave
    the machine brewing something the library no longer says it holds."""
    loaded = machine.snapshot().get("recipe") or {}
    if loaded.get("slug") == slug:
        raise RecipeLoaded(f"'{slug}' is loaded on the machine; cancel the load first")

"""One table of the failures a caller can cause, shared by REST and MCP so a
failure has the same name on both."""
from __future__ import annotations

from xbloom.ble import CommandRefused, CommandUnanswered

from .brews import BadBrewId
from .community import BadQuery, BadShareLink, CommunityBusy, CommunityFailed, NotImportable
from .gate import GateRefused
from .machine import BadTicket, CancelFailed, NotReady
from .recipes import BadSlug, RecipeExists, RecipeInvalid


class RecipeLoaded(RuntimeError):
    """The recipe under this slug is the one loaded on the machine right now."""


class BodyTooLarge(ValueError):
    """A request body over the route's cap, refused before it is read in full."""


# exception -> (HTTP status, name)
TABLE: dict[type[Exception], tuple[int, str]] = {
    BadSlug: (400, "bad_slug"),
    RecipeInvalid: (422, "recipe_invalid"),
    RecipeExists: (409, "recipe_exists"),
    BadBrewId: (400, "bad_brew_id"),
    NotReady: (409, "not_ready"),
    BadTicket: (403, "bad_ticket"),
    RecipeLoaded: (409, "recipe_is_loaded"),
    CancelFailed: (502, "cancel_failed"),
    CommandRefused: (502, "machine_refused"),
    CommandUnanswered: (502, "machine_unanswered"),
    GateRefused: (500, "gate_refused"),
    BodyTooLarge: (413, "body_too_large"),
    # xBloom's community hub (docs/PLAN.md, S3b). A 502's detail is one of
    # community.WORDS, never text from the service.
    CommunityFailed: (502, "community_failed"),
    CommunityBusy: (503, "community_busy"),
    BadQuery: (400, "bad_query"),
    BadShareLink: (400, "bad_share_link"),
    NotImportable: (422, "not_importable"),
}


def lookup(err: Exception) -> tuple[int, str]:
    # The one refusal a person can fix at the machine: the tank is low.
    if isinstance(err, CommandRefused) and err.reason == "no_water":
        return (409, "no_water")
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

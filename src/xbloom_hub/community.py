"""Recipes from xBloom's community hub and from share links (docs/PLAN.md, S3b).

The only part of the hub that calls out of the home network. Every request
goes through `Community._post`, which posts to four fixed URLs and nothing
else, follows no redirect, keeps no cookie, accepts only a 200, and reads at
most 1 MiB. Search terms, recipe ids and share ids are all that is sent.

`XBloomCommunityClient` (xbloom-py) builds the community requests and parses
the answers; it is handed `_Session`, which has the one `post()` it calls.
The share request is made here with the body xbloom-py's share client sends,
so `xbloom.client` (and aiohttp) is never imported.

Everything received is untrusted text: what leaves this module is built from
explicit field lists, every string through `clean_text()` and a cap.
"""
from __future__ import annotations

import asyncio
import contextlib
import http.cookiejar
import json
import logging
import math
import re
from urllib.parse import quote, unquote, urlsplit

import httpx
from xbloom.community import KINDS, SORTS, SOURCES, XBloomCommunityClient
from xbloom.exceptions import XBloomAPIError

from .recipes import META_MAX, NAME_MAX, RecipeInvalid, _whole, as_sent, checked, clean_text, plan

COLLECTIVE = "https://collective-api.xbloom.com/communityRecipe"
CRITERIA_URL = f"{COLLECTIVE}/recipe/criteria"
SEARCH_URL = f"{COLLECTIVE}/index/page"
DETAIL_URL = f"{COLLECTIVE}/recipe/detail"
SHARE_URL = "https://client-api.xbloom.com/RecipeDetail.html"
URLS = frozenset({CRITERIA_URL, SEARCH_URL, DETAIL_URL, SHARE_URL})

# What xbloom-py's share client sends (client.py, get_recipe_by_share_id).
# interfaceVersion and skey are public constants of the share page.
SHARE_HEADERS = {"Content-Type": "application/json; charset=utf-8", "Accept": "*/*"}
SHARE_INTERFACE_VERSION = 19700101
SHARE_SKEY = "testskey"

SHARE_NETLOCS = ("share-h5.xbloom.com", "share-h5.xbloom.com:443")
_SHARE_ID = re.compile(r"[A-Za-z0-9+/=]{1,64}")

BODY_CAP = 1 << 20          # bytes, counted after decompression
REQUEST_S = 10              # each request
DEADLINE_S = 15             # each hub operation, over all its requests
MAX_AT_ONCE = 2             # a third operation is refused, not queued
PAGE_SIZE = 20
MAX_PAGE = 50
KEYWORD_MAX = 80
FILTER_NAMES_MAX = 20
RECIPE_ID_MAX = 2**53 - 1

# Filter -> the criteria list its names come from. `machine` is one name,
# the others are lists (xbloom-py's search arguments).
CRITERIA_LISTS = {
    "machine": "machineList", "origins": "originList", "varietals": "varietalList",
    "processes": "processingList", "roasts": "roastList", "flavors": "flavorList",
    "cup_types": "cupTypeList",
}
LIST_FILTERS = tuple(key for key in CRITERIA_LISTS if key != "machine")

WORDS = ("timeout", "upstream_status", "upstream_refused", "malformed_response")

log = logging.getLogger(__name__)
# httpx logs each request at INFO with the server's status text. Failures are
# logged here instead, with the exception class and the status code only.
logging.getLogger("httpx").setLevel(logging.WARNING)


class CommunityFailed(RuntimeError):
    """The service could not be asked, or did not answer as expected. The
    message is one of WORDS, never text from the service."""


class CommunityBusy(RuntimeError):
    """MAX_AT_ONCE operations are running already."""


class BadQuery(ValueError):
    """A search or import request the hub does not send."""


class BadShareLink(ValueError):
    """Not a share link the hub accepts (refused before any request)."""


class NotImportable(RecipeInvalid):
    """A fetched recipe the hub cannot save as its author wrote it."""


class OutboundRefused(RuntimeError):
    """A request to a URL not on the list. A bug in the hub; never sent."""


def share_id(link: object) -> str:
    """The share id of a share-h5 link, or BadShareLink. `+` stays `+`."""
    if not isinstance(link, str) or len(link) > 512:
        raise BadShareLink("not a share link")
    parts = urlsplit(link.strip())
    # The netloc compared whole: no user info, port absent or 443, the host exact.
    if parts.scheme != "https" or parts.netloc not in SHARE_NETLOCS:
        raise BadShareLink("only https://share-h5.xbloom.com links")
    # Not parse_qs: it reads `+` as a space, and base64 ids contain `+`.
    ids = [unquote(value) for key, _, value in
           (item.partition("=") for item in parts.query.split("&")) if unquote(key) == "id"]
    if len(ids) != 1 or not _SHARE_ID.fullmatch(ids[0]):
        raise BadShareLink("the link needs exactly one id of at most 64 base64 characters")
    return ids[0]


def share_link(sid: str) -> str:
    return f"https://share-h5.xbloom.com/?id={quote(sid, safe='')}"


# ---- turning what was received into what is returned -------------------- #
def _text(value: object, cap: int) -> str:
    return clean_text(value)[:cap].strip() if isinstance(value, str) else ""


def _texts(value: object, cap: int = 80, count: int = 20) -> list[str]:
    return [_text(item, cap) for item in value[:count]] if isinstance(value, list) else []


def _num(value: object) -> float | int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value if math.isfinite(value) else None


def _card(r: dict) -> dict:
    """A search result: these fields only. image_url and share_url are dropped."""
    return {
        "id": _num(r.get("id")), "official": r.get("official") is True,
        "name": _text(r.get("name"), NAME_MAX), "author": _text(r.get("author"), 80),
        "machine": _text(r.get("machine"), 40), "coffee_type": _text(r.get("coffee_type"), 40),
        "cup_type": _num(r.get("cup_type")),
        "origin": _texts(r.get("origin")), "varietal": _texts(r.get("varietal")),
        "process": _texts(r.get("process")), "flavor": _texts(r.get("flavor")),
        "roast": _text(r.get("roast"), 80) or None,
        "dose_g": _num(r.get("dose_g")), "water_ratio": _num(r.get("water_ratio")),
        "grinder_size": _num(r.get("grinder_size")), "rpm": _num(r.get("rpm")),
        "pour_count": _num(r.get("pour_count")), "cup_volume": _text(r.get("cup_volume"), 20),
        "likes": _num(r.get("likes")),
    }


def to_hub(vo: object, sid: str, found: dict | None = None) -> tuple[dict | None, dict]:
    """A share answer's recipeVo in the hub's shape, by the table in
    docs/PLAN.md (S3b) and nothing else, then `checked()`. Returns the
    recipe and no reasons, or None and a reason per field. Never repairs."""
    if not isinstance(vo, dict):
        return None, {"recipe": "not_an_object"}
    reasons: dict[str, str] = {}

    def whole(source: dict, key: str, field: str) -> int | None:
        value = _whole(source.get(key))
        if value is None:
            reasons[field] = "must_be_whole_number"
        return value

    name = vo.get("theName")
    if not isinstance(name, str):
        reasons["name"] = "must_be_text"
        name = ""
    name = clean_text(name)
    if len(name) > NAME_MAX:
        reasons["name"] = "name_too_long"

    ratio, water = None, vo.get("grandWater")
    if (isinstance(water, (int, float)) and not isinstance(water, bool) and math.isfinite(water)
            and 5 <= water <= 25 and float(water * 2).is_integer()):
        ratio = f"1:{float(water):g}"
    else:
        reasons["ratio"] = "must_be_5_to_25_on_the_0.5_step"

    grind = {1: True, 2: False}.get(_whole(vo.get("isSetGrinderSize")))
    if grind is None:
        reasons["grind"] = "must_be_1_or_2"

    pour_list = vo.get("pourList")
    if not isinstance(pour_list, list):
        reasons["pours"] = "not_a_list"
        pour_list = []
    pours = []
    for i, p in enumerate(pour_list):
        if not isinstance(p, dict):
            reasons[f"pours.{i}"] = "not_an_object"
            continue
        pour = {
            "volume_ml": whole(p, "volume", f"pours.{i}.volume_ml"),
            "temperature_c": whole(p, "temperature", f"pours.{i}.temperature_c"),
            "pause_s": whole(p, "pausing", f"pours.{i}.pause_s"),
            "pattern": p.get("pattern"),
            "flow_rate": p.get("flowRate"),
            "agitate_before": p.get("isEnableVibrationBefore"),
            "agitate_after": p.get("isEnableVibrationAfter"),
        }
        for key in ("agitate_before", "agitate_after"):
            if pour[key] is None:           # checked() would default it to off
                reasons[f"pours.{i}.{key}"] = "missing"
        pours.append(pour)

    if _whole(vo.get("pourCount")) != len(pour_list):
        reasons["pour_count"] = "differs_from_pours"
    if (vo.get("isEnableBypassWater") == 1 or vo.get("bypassVolume") is not None
            or vo.get("bypassTemp") is not None):
        reasons["bypass_water"] = "not_supported"
    if vo.get("podsVo"):
        reasons["pod"] = "not_supported"

    meta = {"source": share_link(sid)}
    if found is not None:
        for key, value in (
            ("notes", clean_text(found.get("description") or "")[:META_MAX].strip()),
            ("roast", clean_text(found.get("roast") or "")),
            ("process", clean_text(", ".join(found.get("process") or []))),
            ("bean", clean_text(", ".join([*(found.get("origin") or []),
                                           *(found.get("varietal") or [])]))),
        ):
            if value:
                meta[key] = value

    recipe = {
        "name": name, "cup_type": whole(vo, "cupType", "cup_type"),
        "dose_g": whole(vo, "dose", "dose_g"), "ratio": ratio, "grind": grind,
        "grind_size": whole(vo, "grinderSize", "grind_size"),
        "grinder_speed_rpm": whole(vo, "rpm", "grinder_speed_rpm"),
        "pours": pours, "meta": meta,
    }
    if reasons:
        return None, reasons
    try:
        return checked(recipe), {}
    except RecipeInvalid as err:
        return None, err.errors


class _Session:
    """The part of an aiohttp session XBloomCommunityClient uses:
    `async with session.post(url, json=, headers=) as resp`, then
    `resp.raise_for_status()` and `await resp.json(content_type=None)`."""

    def __init__(self, post) -> None:
        self._post = post

    @contextlib.asynccontextmanager
    async def post(self, url: str, *, json: dict, headers: dict):
        data = await self._post(url, json, headers)
        # The library would call this a refusal; it is the wrong shape.
        if not isinstance(data, dict):
            raise _failed("malformed_response")
        yield _Answer(data)


class _Answer:
    def __init__(self, data: object) -> None:
        self._data = data

    def raise_for_status(self) -> None:
        """Every status but 200 was refused already."""

    async def json(self, content_type: str | None = None) -> object:
        return self._data


class Community:
    """Owns one HTTP client for the life of the hub."""

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._http = httpx.AsyncClient(
            transport=transport, follow_redirects=False, trust_env=False, timeout=REQUEST_S,
            # A jar whose policy takes no cookie from anyone: nothing is kept.
            cookies=http.cookiejar.CookieJar(http.cookiejar.DefaultCookiePolicy(allowed_domains=[])),
        )
        self._client = XBloomCommunityClient(_Session(self._post))
        self._running = 0

    async def aclose(self) -> None:
        await self._http.aclose()

    # ---- the one outbound function -------------------------------------- #
    async def _post(self, url: str, body: dict, headers: dict) -> object:
        if url not in URLS:
            raise OutboundRefused("not one of the four fixed URLs")
        async with asyncio.timeout(REQUEST_S):
            async with self._http.stream("POST", url, json=body, headers=headers) as resp:
                if resp.status_code != 200:
                    raise _failed("upstream_status", status=resp.status_code)
                data = bytearray()
                async for chunk in resp.aiter_bytes():      # decompressed
                    data += chunk
                    if len(data) > BODY_CAP:
                        raise _failed("malformed_response", status=200)
        try:
            return json.loads(data)
        except (ValueError, RecursionError) as err:
            raise _failed("malformed_response", err) from None

    @contextlib.asynccontextmanager
    async def _operation(self):
        if self._running >= MAX_AT_ONCE:
            raise CommunityBusy("two requests to xBloom are running; try again shortly")
        self._running += 1          # no await since the check
        try:
            async with asyncio.timeout(DEADLINE_S):
                yield
        except (CommunityFailed, BadQuery):
            raise
        except (TimeoutError, httpx.TimeoutException) as err:
            raise _failed("timeout", err) from None
        except httpx.DecodingError as err:
            raise _failed("malformed_response", err) from None
        except (httpx.HTTPError, XBloomAPIError) as err:
            raise _failed("upstream_refused", err) from None
        except (KeyError, TypeError, ValueError) as err:
            raise _failed("malformed_response", err) from None
        finally:
            self._running -= 1

    async def _share(self, sid: str) -> object:
        answer = await self._post(SHARE_URL, {"tableIdOfRSA": sid,
                                              "interfaceVersion": SHARE_INTERFACE_VERSION,
                                              "skey": SHARE_SKEY}, SHARE_HEADERS)
        if not isinstance(answer, dict):
            raise _failed("malformed_response")
        if answer.get("result") != "success":
            raise _failed("upstream_refused")
        if not isinstance(answer.get("recipeVo"), dict):
            raise _failed("malformed_response")
        return answer["recipeVo"]

    async def _detail_and_share(self, recipe_id: int) -> tuple[dict, str | None, object]:
        found = await self._client.detail(recipe_id)
        try:
            sid = share_id(found.get("share_url"))
        except BadShareLink:
            return found, None, None        # never fetched: not importable
        return found, sid, await self._share(sid)

    # ---- what the routes and tools call --------------------------------- #
    async def criteria(self) -> dict:
        async with self._operation():
            found = await self._client.criteria()
        return {"from_xbloom_users": {
            key: [_text(o.get("name"), 80) for o in found.get(name, [])[:200]]
            for key, name in CRITERIA_LISTS.items()}}

    async def search(self, keyword: object = None, page: object = 1, sort: object = "date",
                     kind: object = None, source: object = None, machine: object = None,
                     filters: object = None) -> dict:
        if keyword is not None and not isinstance(keyword, str):
            raise BadQuery("keyword must be text")
        keyword = clean_text(keyword or "")
        if len(keyword) > KEYWORD_MAX:
            raise BadQuery(f"keyword: at most {KEYWORD_MAX} characters")
        if isinstance(page, bool) or not isinstance(page, int) or not 1 <= page <= MAX_PAGE:
            raise BadQuery(f"page: 1 to {MAX_PAGE}")
        for label, value, allowed in (("sort", sort, SORTS), ("kind", kind, KINDS),
                                      ("source", source, SOURCES)):
            if (value is not None or label == "sort") and value not in allowed:
                raise BadQuery(f"{label}: one of {', '.join(allowed)}")
        filters = filters or {}
        if not isinstance(filters, dict) or set(filters) - set(LIST_FILTERS):
            raise BadQuery(f"filters: only {', '.join(LIST_FILTERS)}")
        names = {key: value for key, value in filters.items() if value}
        if machine:
            names["machine"] = [machine]
        lists = {key: value for key, value in names.items() if key != "machine"}
        for key, value in names.items():
            if (not isinstance(value, list) or len(value) > FILTER_NAMES_MAX
                    or not all(isinstance(n, str) and len(n) <= 80 for n in value)):
                raise BadQuery(f"{key}: a list of at most {FILTER_NAMES_MAX} names")
        async with self._operation():
            if names:
                criteria = await self._client.criteria()
                for key, value in names.items():
                    # Compared as xbloom-py compares them, so its own lookup cannot fail.
                    known = {o["name"].casefold() for o in criteria.get(CRITERIA_LISTS[key], [])}
                    if any(n.strip().casefold() not in known for n in value):
                        raise BadQuery(f"{key}: a name xBloom's hub does not list")
            found = await self._client.search(
                keyword or None, machine=machine or None, kind=kind, source=source, sort=sort,
                page=page, page_size=PAGE_SIZE, **lists)
        return {"page": _num(found["page"]), "total_pages": _num(found["total_pages"]),
                "total": _num(found["total"]),
                "from_xbloom_users": {"recipes": [_card(r) for r in found["recipes"][:PAGE_SIZE]]}}

    async def detail(self, recipe_id: object) -> dict:
        """One hub recipe: its card and description, the recipe as it would be
        saved, and whether it can be, with the reasons when it cannot."""
        recipe_id = _recipe_id(recipe_id)
        async with self._operation():
            found, sid, vo = await self._detail_and_share(recipe_id)
        recipe, reasons = to_hub(vo, sid, found) if sid else (None, {"share_url": "missing_or_invalid"})
        community = {
            **_card(found), "description": _text(found.get("description"), META_MAX),
            "uploaded_on": _text(found.get("uploaded_on"), 20),
            "pours": [{key: _num(p.get(key)) for key in
                       ("volume_ml", "temperature_c", "pattern", "pause_s")}
                      for p in (found.get("pours") or [])[:20] if isinstance(p, dict)],
        }
        return {"id": recipe_id, "importable": recipe is not None, "reasons": reasons,
                "as_sent": as_sent(recipe) if recipe else None,
                "plan": plan(recipe) if recipe else None,
                "from_xbloom_users": {"community": community, "recipe": recipe}}

    async def import_recipe(self, store, slug: object, recipe_id: object = None,
                            link: object = None) -> str:
        """Fetch one recipe and save it under a new slug. Never loads it."""
        store._path(slug)                   # BadSlug before any request
        if (recipe_id is None) == (link is None):
            raise BadQuery("give either id or share_url")
        if link is not None:
            sid, found = share_id(link), None   # BadShareLink before any request
            async with self._operation():
                vo = await self._share(sid)
        else:
            recipe_id = _recipe_id(recipe_id)
            async with self._operation():
                found, sid, vo = await self._detail_and_share(recipe_id)
            if sid is None:
                raise NotImportable({"share_url": "missing_or_invalid"})
        recipe, reasons = to_hub(vo, sid, found)
        if reasons:
            raise NotImportable(reasons)
        # No await from here to the write: the slug is checked free by the
        # exclusive create itself (RecipeExists).
        store.put(slug, recipe, create=True)
        return slug


def _recipe_id(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= RECIPE_ID_MAX:
        raise BadQuery("id: a positive whole number")
    return value


def _failed(word: str, err: BaseException | None = None, status: int | None = None) -> CommunityFailed:
    # The class and the status only: never the service's text, never an id.
    log.warning("xBloom request failed: %s (%s%s)", word,
                type(err).__name__ if err else "answer", f" {status}" if status else "")
    return CommunityFailed(word)

"""Recipe library: one YAML file per recipe, in one fixed shape.

xbloom-py validates one set of field names (`grind_size`,
`grinder_speed_rpm`) and encodes another (`grinder_size`, `rpm`,
`grinder_size_enabled`), copying only one way. A recipe in the validator's
names would pass and go out with the grinder off; one carrying both would be
validated on one set and sent with the other. So the hub accepts exactly the
shape below, refuses every other key, and builds the encoder's dictionary
itself in `to_machine()` from the checked recipe and nothing else. That is
what makes validation the only check (safety invariant 4): the encoder clamps
bad values instead of refusing them.
"""
from __future__ import annotations

import math
import re
import unicodedata
from pathlib import Path

import yaml
from xbloom import recipe_validate

_SLUG = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")

TOP_KEYS = frozenset({"name", "cup_type", "dose_g", "ratio", "grind_size",
                      "grinder_speed_rpm", "grind", "pours", "meta"})
POUR_KEYS = ("volume_ml", "temperature_c", "pause_s", "pattern", "flow_rate",
             "agitate_before", "agitate_after")
META_KEYS = frozenset({"bean", "roaster", "process", "roast", "notes", "source"})
NAME_MAX = 80
META_MAX = 500


class BadSlug(ValueError):
    pass


class RecipeInvalid(ValueError):
    def __init__(self, errors: dict[str, str]) -> None:
        super().__init__(f"recipe invalid: {errors}")
        self.errors = errors


def clean_text(value: str) -> str:
    """Free text is shown to people and to an assistant. Drop everything that
    is not a visible character or a plain space: control characters, bidi
    overrides and other format characters, line and paragraph separators."""
    out = []
    for ch in value:
        category = unicodedata.category(ch)
        if ch in "\n\r\t" or category in ("Zl", "Zp"):
            out.append(" ")                 # a line break separates words
        elif ch in "\u200c\u200d":
            out.append(ch)                  # joiners: emoji, Persian, Indic text
        elif category[0] != "C":
            out.append(ch)
    # Joiners are kept inside text, not as the whole of it or at its edges.
    return " ".join("".join(out).split()).strip("\u200c\u200d ")


def _whole(value: object) -> int | None:
    """The integer a value stands for, or None. The encoder truncates or
    rounds fractions, so a fraction would be sent as something else."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and math.isfinite(value) and value.is_integer():
        return int(value)
    return None


def _whole_fields(source: dict, keys: tuple[str, ...], prefix: str, errors: dict) -> dict:
    """Copy `keys` from `source`, turning whole numbers into ints. A value
    that is absent or None is left for the library's own check to report."""
    out = {}
    for key in keys:
        value = source.get(key)
        if value is None:
            continue
        whole = _whole(value)
        if whole is None:
            errors[prefix + key] = "must_be_whole_number"
        out[key] = value if whole is None else whole
    return out


def checked(recipe: object) -> dict:
    """Return the recipe in the hub's one shape, or raise RecipeInvalid with an
    error code per field (the library's codes, plus the hub's own)."""
    if not isinstance(recipe, dict):
        raise RecipeInvalid({"recipe": "not_an_object"})
    errors: dict[str, str] = {}
    for key in recipe:
        if key not in TOP_KEYS:
            errors[str(key)] = "unknown_field"

    name = recipe.get("name", "")
    if not isinstance(name, str):
        errors["name"] = "must_be_text"
        name = ""
    name = clean_text(name)
    if len(name) > NAME_MAX:
        errors["name"] = "name_too_long"

    # The machine is sent whole grams, whole millilitres and whole degrees.
    top = _whole_fields(recipe, ("cup_type", "dose_g", "grind_size", "grinder_speed_rpm"),
                        "", errors)

    grind = recipe.get("grind", True)
    if not isinstance(grind, bool):
        errors["grind"] = "must_be_true_or_false"

    pours_in = recipe.get("pours")
    pours: list[dict] = []
    if isinstance(pours_in, list):
        for i, pour in enumerate(pours_in):
            if not isinstance(pour, dict):
                errors[f"pours.{i}"] = "not_an_object"
                continue
            for key in pour:
                if key not in POUR_KEYS:
                    errors[f"pours.{i}.{key}"] = "unknown_field"
            item = {key: pour[key] for key in POUR_KEYS if key in pour}
            item.update(_whole_fields(
                pour, ("volume_ml", "temperature_c", "pattern", "agitate_before",
                       "agitate_after"), f"pours.{i}.", errors))
            flow = item.get("flow_rate")
            if isinstance(flow, (int, float)) and not isinstance(flow, bool):
                if not -1e6 < flow < 1e6:               # inf, nan, absurd
                    errors[f"pours.{i}.flow_rate"] = "flow_out_of_range"
                elif abs(flow * 10 - round(flow * 10)) > 1e-9:
                    errors[f"pours.{i}.flow_rate"] = "must_be_a_step_of_0.1"
                else:
                    item["flow_rate"] = round(float(flow), 1)
            for key in ("agitate_before", "agitate_after"):
                item.setdefault(key, 2)                      # 1 = on, 2 = off
                if item[key] not in (1, 2):
                    errors.setdefault(f"pours.{i}.{key}", "must_be_1_or_2")
            pours.append(item)

    meta_in = recipe.get("meta")
    meta: dict[str, str] = {}
    if meta_in is not None:
        if not isinstance(meta_in, dict):
            errors["meta"] = "not_an_object"
        else:
            for key, value in meta_in.items():
                if key not in META_KEYS:
                    errors[f"meta.{key}"] = "unknown_field"
                elif not isinstance(value, str):
                    errors[f"meta.{key}"] = "must_be_text"
                elif len(clean_text(value)) > META_MAX:
                    errors[f"meta.{key}"] = "too_long"
                else:
                    meta[key] = clean_text(value)

    # Not normalize_recipe(): it is a share-link import shim that fills a
    # missing ratio with 1:16 and snaps an off-grid one, which would accept a
    # recipe the author never wrote.
    library = {
        "name": name,
        "cup_type": top.get("cup_type"),
        "dose_g": top.get("dose_g"),
        "ratio": recipe.get("ratio"),
        "grind_size": top.get("grind_size"),
        "grinder_speed_rpm": top.get("grinder_speed_rpm"),
        "pour_count": len(pours),
        "pours": pours,
    }
    try:
        found = recipe_validate.validate_recipe(library)
    except (OverflowError, ValueError, TypeError):
        # The library does arithmetic before range checks; a number too large
        # to be a float lands here.
        found = {"recipe": "not_a_usable_number"}
    for field, code in found.items():
        errors.setdefault(field, code)
    if errors:
        raise RecipeInvalid(errors)
    ratio = f"1:{float(str(library['ratio']).split(':', 1)[1]):g}"      # "1:16.0" -> "1:16"

    out = {
        "name": name, "cup_type": library["cup_type"], "dose_g": library["dose_g"],
        "ratio": ratio, "grind": grind, "grind_size": library["grind_size"],
        "grinder_speed_rpm": library["grinder_speed_rpm"], "pours": pours,
    }
    if meta:
        out["meta"] = meta
    return out


def as_sent(recipe: dict) -> dict:
    """What the machine will actually be told for a `checked()` recipe.

    The firmware only takes a recipe whose pour total equals its ratio byte
    (one decimal) times the dose, so xbloom-py picks the nearest ratio that
    works and moves the pour volumes to fit. The shift is a few millilitres
    at most for whole-gram doses; it is reported so nobody has to guess.
    """
    from xbloom.ble import _gate_consistent_volumes

    volumes, ratio_byte = _gate_consistent_volumes(
        [float(pour["volume_ml"]) for pour in recipe["pours"]], int(recipe["dose_g"]))
    return {"dose_g": int(recipe["dose_g"]), "ratio": f"1:{ratio_byte / 10:g}",
            "pour_volumes_ml": volumes, "total_ml": sum(volumes)}


def to_machine(recipe: dict) -> dict:
    """The dictionary xbloom-py's encoder reads, from a `checked()` recipe only."""
    return {
        "name": recipe["name"],
        "cup_type": recipe["cup_type"],
        "dose_g": recipe["dose_g"],
        "grinder_size": recipe["grind_size"],
        "grinder_size_enabled": 1 if recipe["grind"] else 2,
        "rpm": recipe["grinder_speed_rpm"],
        "pours": [{key: pour[key] for key in POUR_KEYS} for pour in recipe["pours"]],
    }


class RecipeStore:
    def __init__(self, directory: Path) -> None:
        self._dir = Path(directory)
        self._dir.mkdir(parents=True, exist_ok=True)

    def _path(self, slug: str) -> Path:
        # The slug is the file name, so it is the only thing between a request
        # and the file system.
        if not isinstance(slug, str) or not _SLUG.fullmatch(slug):
            raise BadSlug(slug)
        return self._dir / f"{slug}.yaml"

    def list(self) -> list[dict]:
        out = []
        for path in sorted(self._dir.glob("*.yaml")):
            try:
                data = yaml.safe_load(path.read_text(encoding="utf-8"))
            except (yaml.YAMLError, UnicodeDecodeError, OSError):
                data = None
            # A hand-edited file that is not a recipe is listed, not fatal,
            # and its name gets the same cleaning as a saved one.
            name = data.get("name") if isinstance(data, dict) else None
            out.append({"slug": path.stem,
                        "name": clean_text(name)[:NAME_MAX] if isinstance(name, str) else None})
        return out

    def get(self, slug: str) -> dict | None:
        """The checked recipe, or None. A file that no longer passes is refused."""
        path = self._path(slug)
        if not path.exists():
            return None
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (yaml.YAMLError, UnicodeDecodeError, OSError) as err:
            raise RecipeInvalid({"file": "not_yaml"}) from err
        return checked(data)

    def wire(self, slug: str) -> dict | None:
        """The stored recipe in the encoder's shape, or None. What a load sends."""
        recipe = self.get(slug)
        return to_machine(recipe) if recipe is not None else None

    def put(self, slug: str, recipe: object) -> dict:
        path = self._path(slug)
        recipe = checked(recipe)
        path.write_text(yaml.safe_dump(recipe, sort_keys=False, allow_unicode=True),
                        encoding="utf-8")
        return recipe

    def delete(self, slug: str) -> bool:
        path = self._path(slug)
        if not path.exists():
            return False
        path.unlink()
        return True

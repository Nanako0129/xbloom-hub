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

import re
from pathlib import Path

import yaml
from xbloom import recipe_validate

_SLUG = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")

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


def clean_text(value: object) -> str:
    """Free text is shown to people and to an assistant: no control characters."""
    return _CONTROL.sub("", str(value)).strip()


def checked(recipe: object) -> dict:
    """Return the recipe in the hub's one shape, or raise RecipeInvalid with an
    error code per field (the library's codes, plus the hub's own)."""
    if not isinstance(recipe, dict):
        raise RecipeInvalid({"recipe": "not_an_object"})
    errors: dict[str, str] = {}
    for key in recipe:
        if key not in TOP_KEYS:
            errors[str(key)] = "unknown_field"

    name = clean_text(recipe.get("name", ""))
    if len(name) > NAME_MAX:
        errors["name"] = "name_too_long"

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
            for key in ("agitate_before", "agitate_after"):
                item.setdefault(key, 2)                      # 1 = on, 2 = off
                if isinstance(item[key], bool) or item[key] not in (1, 2):
                    errors[f"pours.{i}.{key}"] = "must_be_1_or_2"
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

    library = recipe_validate.normalize_recipe({
        "name": name,
        "cup_type": recipe.get("cup_type"),
        "dose_g": recipe.get("dose_g"),
        "ratio": recipe.get("ratio"),
        "grind_size": recipe.get("grind_size"),
        "grinder_speed_rpm": recipe.get("grinder_speed_rpm"),
        "pour_count": len(pours),
        "pours": pours,
    })
    for field, code in recipe_validate.validate_recipe(library).items():
        errors.setdefault(field, code)
    if errors:
        raise RecipeInvalid(errors)

    out = {
        "name": name, "cup_type": library["cup_type"], "dose_g": library["dose_g"],
        "ratio": library["ratio"], "grind": grind, "grind_size": library["grind_size"],
        "grinder_speed_rpm": library["grinder_speed_rpm"], "pours": pours,
    }
    if meta:
        out["meta"] = meta
    return out


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
                data = yaml.safe_load(path.read_text())
            except yaml.YAMLError:
                data = None
            # A hand-edited file that is not a recipe is listed, not fatal,
            # and its name gets the same cleaning as a saved one.
            name = data.get("name") if isinstance(data, dict) else None
            out.append({"slug": path.stem,
                        "name": clean_text(name)[:NAME_MAX] if name is not None else None})
        return out

    def get(self, slug: str) -> dict | None:
        """The checked recipe, or None. A file that no longer passes is refused."""
        path = self._path(slug)
        if not path.exists():
            return None
        try:
            data = yaml.safe_load(path.read_text())
        except yaml.YAMLError as err:
            raise RecipeInvalid({"file": "not_yaml"}) from err
        return checked(data)

    def put(self, slug: str, recipe: object) -> dict:
        path = self._path(slug)
        recipe = checked(recipe)
        path.write_text(yaml.safe_dump(recipe, sort_keys=False, allow_unicode=True))
        return recipe

    def delete(self, slug: str) -> bool:
        path = self._path(slug)
        if not path.exists():
            return False
        path.unlink()
        return True

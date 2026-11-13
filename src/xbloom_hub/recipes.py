"""Recipe library: one YAML file per recipe, in xbloom-py's own recipe shape.

Every recipe is validated on the way in and again on the way to the machine
(invariant 4): the frame encoder clamps bad values instead of refusing them.
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml
from xbloom import recipe_validate

_SLUG = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")


class BadSlug(ValueError):
    pass


class RecipeInvalid(ValueError):
    def __init__(self, errors: dict[str, str]) -> None:
        super().__init__(f"recipe invalid: {errors}")
        self.errors = errors


def checked(recipe: object) -> dict:
    """Normalise and validate; raise RecipeInvalid with the library's error codes."""
    if not isinstance(recipe, dict):
        raise RecipeInvalid({"recipe": "not_an_object"})
    recipe = recipe_validate.normalize_recipe(recipe)
    errors = recipe_validate.validate_recipe(recipe)
    if errors:
        raise RecipeInvalid(errors)
    return recipe


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
            data = yaml.safe_load(path.read_text()) or {}
            out.append({"slug": path.stem, "name": data.get("name")})
        return out

    def get(self, slug: str) -> dict | None:
        path = self._path(slug)
        return yaml.safe_load(path.read_text()) if path.exists() else None

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

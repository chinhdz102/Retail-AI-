from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

_PLANOGRAM_PATH = (
    Path(__file__).resolve().parents[2] / "data" / "winmart_planogram_assignments.json"
)
_DATASET_HASH = re.compile(r"^[a-f0-9]{64}$")
_LOCATION_CODE = re.compile(r"^A\d{2}-B\d{2}-L\d{2}-P\d{2}$")


@lru_cache(maxsize=1)
def _load_planogram() -> tuple[str | None, dict[str, str]]:
    """Load recommended locators once; invalid or missing files fail closed."""
    try:
        payload: dict[str, Any] = json.loads(_PLANOGRAM_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None, {}

    if not isinstance(payload, dict):
        return None, {}
    dataset_hash = payload.get("source_dataset_sha256")
    assignments = payload.get("assignments")
    if (
        not isinstance(dataset_hash, str)
        or not _DATASET_HASH.fullmatch(dataset_hash)
        or not isinstance(assignments, list)
    ):
        return None, {}

    locations: dict[str, str] = {}
    for assignment in assignments:
        if not isinstance(assignment, dict):
            continue
        product_id = assignment.get("product_id")
        location_code = assignment.get("location_code")
        if (
            assignment.get("placement_status") == "RECOMMENDED_SYNTHETIC_PLANOGRAM"
            and isinstance(product_id, str)
            and isinstance(location_code, str)
            and product_id
            and _LOCATION_CODE.fullmatch(location_code)
        ):
            locations[product_id] = location_code
    return dataset_hash, locations


def recommended_shelf(product_id: str, dataset_version: str | None) -> str | None:
    """Return a locator only when the catalog row matches the map's source data."""
    if not dataset_version:
        return None
    source_hash, locations = _load_planogram()
    if source_hash is None or dataset_version != source_hash:
        return None
    return locations.get(product_id)

"""Fail-closed split authorization for human qrels exports.

Repository-visible query sets are development data. A file name or caller-supplied
``--split blind_test`` flag cannot make an exposed set independent or blind.
Promotion to validation/blind requires an exact content hash in the evaluator-owned
registry plus the provenance and access-control assertions required by policy.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CASE_ROOT = ROOT / "evaluation" / "cases"
REGISTRY_PATH = Path(__file__).with_name("dataset_split_registry.json")
ALLOWED_SPLITS = frozenset({"development", "validation", "blind_test"})


class SplitPolicyError(ValueError):
    """Raised when a dataset cannot be used in the requested evaluation split."""


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _stable_text_sha256(raw: bytes) -> str:
    """Hash UTF-8 text content consistently across Windows and Unix checkouts."""
    normalized = raw.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    return hashlib.sha256(normalized).hexdigest()


def _load_registry(path: Path = REGISTRY_PATH) -> tuple[dict[str, Any], str]:
    try:
        raw = path.read_bytes()
        registry = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        raise SplitPolicyError(f"Could not read evaluation split registry: {path}") from exc
    if not isinstance(registry, dict) or registry.get("schema_version") != "1.0":
        raise SplitPolicyError("Evaluation split registry must use schema_version 1.0")
    if not isinstance(registry.get("protected_datasets"), list):
        raise SplitPolicyError("Evaluation split registry has no protected_datasets list")
    return registry, _stable_text_sha256(raw)


def authorize_dataset_split(
    dataset_path: Path,
    requested_split: str,
    *,
    registry_path: Path = REGISTRY_PATH,
) -> dict[str, str | bool]:
    """Return split provenance or fail when a caller tries to promote exposed data."""
    if requested_split not in ALLOWED_SPLITS:
        raise SplitPolicyError(f"Unsupported evaluation split: {requested_split!r}")
    path = dataset_path.resolve()
    if requested_split == "blind_test":
        # Check both the lexical path (which may be a symlink into an external
        # directory) and the resolved path (which may point back into the repo).
        # Either route makes the data discoverable from the checkout and therefore
        # unsuitable for a blind-test claim.
        repo_root = ROOT.resolve()
        lexical_path = Path(os.path.abspath(dataset_path))
        if _is_within(lexical_path, repo_root) or _is_within(path, repo_root):
            raise SplitPolicyError(
                "Repository-visible datasets are development-only and cannot be "
                "used as blind_test."
            )
    if not path.is_file():
        raise SplitPolicyError(f"Evaluation dataset does not exist: {path}")
    dataset_sha256 = _stable_text_sha256(path.read_bytes())
    registry, registry_sha256 = _load_registry(registry_path)

    if requested_split == "development":
        in_repo = _is_within(path, ROOT.resolve())
        status = "repository_visible_development" if in_repo else "unregistered_development"
        known = next(
            (
                item
                for item in registry.get("known_exposed_datasets", [])
                if isinstance(item, dict) and item.get("sha256") == dataset_sha256
            ),
            None,
        )
        if known:
            status = "registered_exposed_development"
        return {
            "requested_split": requested_split,
            "effective_split": "development",
            "authorization_status": status,
            "dataset_sha256": dataset_sha256,
            "split_registry_sha256": registry_sha256,
            "eligible_for_blind_claim": False,
        }

    if _is_within(path, CASE_ROOT.resolve()):
        raise SplitPolicyError(
            "Repository-visible evaluation/cases datasets are development-only; "
            "they cannot be exported as validation or blind_test."
        )

    protected = next(
        (
            item
            for item in registry["protected_datasets"]
            if isinstance(item, dict)
            and item.get("sha256") == dataset_sha256
            and item.get("split") == requested_split
        ),
        None,
    )
    if protected is None:
        raise SplitPolicyError(
            f"Dataset SHA-256 {dataset_sha256} is not registered for {requested_split}; "
            "unknown or merely renamed data defaults to development."
        )

    required = (
        "independent_authoring_verified",
        "independent_review_complete",
        "frozen_before_model_selection",
        "adjudication_complete",
    )
    missing = [key for key in required if protected.get(key) is not True]
    try:
        reviewer_count = int(protected.get("independent_reviewer_count", 0))
    except (TypeError, ValueError):
        reviewer_count = 0
    if reviewer_count < 2:
        missing.append("independent_reviewer_count>=2")
    if missing:
        raise SplitPolicyError(
            f"Registered {requested_split} dataset lacks required provenance: "
            f"{', '.join(missing)}"
        )
    if requested_split == "blind_test":
        blind_requirements = (
            "not_used_for_tuning",
            "data_and_labels_external_to_repo",
            "labels_sealed_until_final_evaluation",
        )
        missing = [key for key in blind_requirements if protected.get(key) is not True]
        if missing:
            raise SplitPolicyError(
                "Registered blind_test lacks an access boundary: " + ", ".join(missing)
            )

    return {
        "requested_split": requested_split,
        "effective_split": requested_split,
        "authorization_status": "registered_protected_split",
        "dataset_sha256": dataset_sha256,
        "split_registry_sha256": registry_sha256,
        "eligible_for_blind_claim": requested_split == "blind_test",
    }

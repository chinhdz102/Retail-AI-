import copy
import json
from pathlib import Path

import pytest

from backend.products.knowledge import product_knowledge_facts
from backend.products.snapshot_repository import SnapshotProductRepository


@pytest.fixture(scope="module")
def snapshot_repository() -> SnapshotProductRepository:
    return SnapshotProductRepository(
        Path(__file__).resolve().parents[1] / "data" / "winmart_products.json"
    )


@pytest.mark.parametrize(
    ("product_id", "attributes", "expected_scope", "expected_match_scope"),
    (
        (
            "WIN_10307381",
            ("nutrition", "ingredients", "caffeine"),
            "MANUFACTURER_DECLARATION",
            "PRODUCT_LINE",
        ),
        (
            "WIN_10160995",
            ("usage",),
            "MANUFACTURER_DECLARATION",
            "EXACT_VARIANT",
        ),
        (
            "WIN_10007893",
            ("ingredients", "usage"),
            "MANUFACTURER_DECLARATION",
            "PRODUCT_LINE",
        ),
        (
            "WIN_10016738",
            ("benefits",),
            "MANUFACTURER_CLAIM",
            "PRODUCT_LINE",
        ),
    ),
)
def test_manufacturer_facts_are_bound_to_catalog_identity_and_version(
    snapshot_repository: SnapshotProductRepository,
    product_id: str,
    attributes: tuple[str, ...],
    expected_scope: str,
    expected_match_scope: str,
) -> None:
    product = snapshot_repository.get_product_snapshot(product_id)
    assert product is not None

    facts = product_knowledge_facts(
        product,
        attributes,
        source="winmart_snapshot",
        catalog_version=snapshot_repository.dataset_sha256,
    )

    assert len(facts) == len(attributes)
    assert {fact.status.value for fact in facts} == {
        "INFERRED" if expected_scope == "MANUFACTURER_CLAIM" else "VERIFIED"
    }
    assert {fact.evidence_scope.value for fact in facts} == {expected_scope}
    assert {fact.product_match_scope for fact in facts} == {expected_match_scope}
    assert all(fact.source_url and fact.source_url.startswith("https://") for fact in facts)
    assert all(fact.evidence_version.startswith("manufacturer-enrichment-v1:") for fact in facts)
    assert all(fact.catalog_version == snapshot_repository.dataset_sha256 for fact in facts)


@pytest.mark.parametrize(
    "identity_change",
    (
        {"name": "Different product variant"},
        {"brand": "Different brand"},
        {"barcode": "0000000000000"},
        {"catalog_version": "0" * 64},
    ),
)
def test_enrichment_fails_closed_when_product_identity_or_catalog_changes(
    snapshot_repository: SnapshotProductRepository,
    identity_change: dict[str, str],
) -> None:
    product = snapshot_repository.get_product_snapshot("WIN_10016738")
    assert product is not None
    stale_product = product.model_copy(update=identity_change)

    fact = product_knowledge_facts(
        stale_product,
        ("benefits",),
        source="winmart_snapshot",
    )[0]

    assert fact.status.value == "UNKNOWN"
    assert fact.value is None
    assert fact.evidence_scope.value == "UNKNOWN"


def test_direct_catalog_ingredient_evidence_takes_precedence_over_enrichment(
    snapshot_repository: SnapshotProductRepository,
) -> None:
    product = snapshot_repository.get_product_snapshot("WIN_10307381")
    assert product is not None
    product_with_direct_ingredients = product.model_copy(
        update={"ingredients": "Exact package ingredient text from current catalog"}
    )

    fact = product_knowledge_facts(
        product_with_direct_ingredients,
        ("ingredients",),
        source="winmart_snapshot",
    )[0]

    assert fact.value == "Exact package ingredient text from current catalog"
    assert fact.status.value == "VERIFIED"
    assert fact.evidence_scope.value == "SOURCE_RECORD"


def test_calories_are_projected_from_verified_nutrition_with_the_original_basis(
    snapshot_repository: SnapshotProductRepository,
) -> None:
    product = snapshot_repository.get_product_snapshot("WIN_10307381")
    assert product is not None

    fact = product_knowledge_facts(
        product,
        ("calories",),
        source="winmart_snapshot",
        catalog_version=snapshot_repository.dataset_sha256,
    )[0]

    assert fact.attribute == "calories"
    assert fact.value == {"energy_kcal": 0, "basis": "per 100 ml"}
    assert fact.status.value == "VERIFIED"
    assert fact.evidence_scope.value == "MANUFACTURER_DECLARATION"
    assert fact.product_match_scope == "PRODUCT_LINE"
    assert fact.source_field == "Coca-Cola Zero Sugar nutrition values per 100 ml.energy_kcal"


@pytest.mark.parametrize(
    "mutation",
    (
        {"source_url": "http://example.com/unsafe"},
        {"status": "VERIFIED"},
    ),
)
def test_invalid_or_overstated_enrichment_fails_closed(
    snapshot_repository: SnapshotProductRepository,
    tmp_path: Path,
    mutation: dict[str, str],
) -> None:
    registry_path = (
        Path(__file__).resolve().parents[1] / "data" / "product_knowledge_enrichment_pilot.json"
    )
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    changed_registry = copy.deepcopy(registry)
    changed_fact = changed_registry["records"][3]["facts"][0]
    changed_fact.update(mutation)
    temporary_path = tmp_path / "invalid-enrichment.json"
    temporary_path.write_text(json.dumps(changed_registry), encoding="utf-8")

    product = snapshot_repository.get_product_snapshot("WIN_10016738")
    assert product is not None
    fact = product_knowledge_facts(
        product,
        ("benefits",),
        source="winmart_snapshot",
        enrichment_path=temporary_path,
    )[0]

    assert fact.status.value == "UNKNOWN"
    assert fact.value is None

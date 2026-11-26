import copy
import json
from pathlib import Path

import pytest

from backend.products.knowledge import product_knowledge_facts
from backend.products.snapshot_repository import SnapshotProductRepository


@pytest.fixture(scope="module")
def snapshot_repository() -> SnapshotProductRepository:
    return SnapshotProductRepository(
        Path(__file__).resolve().parents[1] / "data" / "thanh_lam_products.json"
    )


@pytest.mark.parametrize(
    ("product_id", "attributes", "expected_scope", "expected_match_scope"),
    (
        (
            "TLM_10307381",
            ("nutrition", "ingredients", "caffeine"),
            "MANUFACTURER_DECLARATION",
            "PRODUCT_LINE",
        ),
        (
            "TLM_10160995",
            ("usage",),
            "MANUFACTURER_DECLARATION",
            "EXACT_VARIANT",
        ),
        (
            "TLM_10007893",
            ("ingredients", "usage"),
            "MANUFACTURER_DECLARATION",
            "PRODUCT_LINE",
        ),
        (
            "TLM_10016738",
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
        source="thanh_lam_snapshot",
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
    product = snapshot_repository.get_product_snapshot("TLM_10016738")
    assert product is not None
    stale_product = product.model_copy(update=identity_change)

    fact = product_knowledge_facts(
        stale_product,
        ("benefits",),
        source="thanh_lam_snapshot",
    )[0]

    assert fact.status.value == "UNKNOWN"
    assert fact.value is None
    assert fact.evidence_scope.value == "UNKNOWN"


def test_direct_catalog_ingredient_evidence_takes_precedence_over_enrichment(
    snapshot_repository: SnapshotProductRepository,
) -> None:
    product = snapshot_repository.get_product_snapshot("TLM_10307381")
    assert product is not None
    product_with_direct_ingredients = product.model_copy(
        update={"ingredients": "Exact package ingredient text from current catalog"}
    )

    fact = product_knowledge_facts(
        product_with_direct_ingredients,
        ("ingredients",),
        source="thanh_lam_snapshot",
    )[0]

    assert fact.value == "Exact package ingredient text from current catalog"
    assert fact.status.value == "VERIFIED"
    assert fact.evidence_scope.value == "SOURCE_RECORD"


def test_calories_are_projected_from_verified_nutrition_with_the_original_basis(
    snapshot_repository: SnapshotProductRepository,
) -> None:
    product = snapshot_repository.get_product_snapshot("TLM_10307381")
    assert product is not None

    fact = product_knowledge_facts(
        product,
        ("calories",),
        source="thanh_lam_snapshot",
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

    product = snapshot_repository.get_product_snapshot("TLM_10016738")
    assert product is not None
    fact = product_knowledge_facts(
        product,
        ("benefits",),
        source="thanh_lam_snapshot",
        enrichment_path=temporary_path,
    )[0]

    assert fact.status.value == "UNKNOWN"
    assert fact.value is None


def test_all_enriched_registry_records_bind_to_live_thanh_lam_snapshot(
    snapshot_repository: SnapshotProductRepository,
) -> None:
    registry_path = (
        Path(__file__).resolve().parents[1] / "data" / "product_knowledge_enrichment_pilot.json"
    )
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    assert len(registry["records"]) >= 20
    for record in registry["records"]:
        product = snapshot_repository.get_product_snapshot(record["product_id"])
        assert product is not None, f"Missing product {record['product_id']}"
        attributes = tuple(fact["attribute"] for fact in record["facts"])
        facts = product_knowledge_facts(
            product,
            attributes,
            source="thanh_lam_snapshot",
            catalog_version=snapshot_repository.dataset_sha256,
        )
        assert len(facts) == len(attributes)
        assert all(fact.status.value != "UNKNOWN" for fact in facts)
        assert all(fact.source_url and fact.source_url.startswith("https://") for fact in facts)


def test_crawled_thanh_lam_details_surface_ingredients_and_usage_for_chin_su_1kg(
    snapshot_repository: SnapshotProductRepository,
) -> None:
    chinsu = snapshot_repository.get_product_snapshot("TLM_10141747")
    assert chinsu is not None

    facts = {
        fact.attribute: fact
        for fact in product_knowledge_facts(
            chinsu,
            ("ingredients", "usage"),
            source="thanh_lam_snapshot",
            catalog_version=snapshot_repository.dataset_sha256,
        )
    }
    assert facts["ingredients"].status.value == "VERIFIED"
    assert facts["ingredients"].evidence_scope.value == "RETAILER_LISTING"
    ing_lower = str(facts["ingredients"].value).casefold()
    assert "ớt 110 g/kg" in ing_lower
    assert "1422" in ing_lower
    assert "cà chua cô đặc" in ing_lower
    assert facts["usage"].status.value == "VERIFIED"
    assert facts["usage"].evidence_scope.value == "RETAILER_LISTING"


def test_enriched_details_surface_selsun_ingredients_usage_and_origin(
    snapshot_repository: SnapshotProductRepository,
) -> None:
    selsun = snapshot_repository.get_product_snapshot("TLM_10016912")
    assert selsun is not None
    facts = {
        fact.attribute: fact
        for fact in product_knowledge_facts(
            selsun,
            ("ingredients", "usage", "origin"),
            source="thanh_lam_snapshot",
            catalog_version=snapshot_repository.dataset_sha256,
        )
    }
    assert facts["ingredients"].status.value == "VERIFIED"
    assert "selenium sulfide" in str(facts["ingredients"].value).casefold()
    assert facts["usage"].status.value == "VERIFIED"
    assert "lắc kỹ" in str(facts["usage"].value).casefold()
    assert facts["origin"].status.value == "VERIFIED"


def test_enrichment_binds_across_windows_crlf_and_linux_lf_catalog_hashes(
    snapshot_repository: SnapshotProductRepository,
) -> None:
    from backend.data.catalog_import import canonical_dataset_sha256

    canonical_sha = canonical_dataset_sha256(
        (Path(__file__).resolve().parents[1] / "data" / "thanh_lam_products.json").read_bytes()
    )
    coca_zero = snapshot_repository.get_product_snapshot("TLM_10307381")
    selsun = snapshot_repository.get_product_snapshot("TLM_10016912")
    assert coca_zero is not None and selsun is not None

    lf_coca = coca_zero.model_copy(update={"catalog_version": canonical_sha})
    lf_selsun = selsun.model_copy(update={"catalog_version": canonical_sha})

    coca_cal = product_knowledge_facts(
        lf_coca, ("calories",), source="thanh_lam_snapshot", catalog_version=canonical_sha
    )[0]
    assert coca_cal.status.value == "VERIFIED"
    assert coca_cal.value == {"energy_kcal": 0, "basis": "per 100 ml"}

    selsun_usage = product_knowledge_facts(
        lf_selsun, ("usage",), source="thanh_lam_snapshot", catalog_version=canonical_sha
    )[0]
    assert selsun_usage.status.value == "VERIFIED"
    assert "lắc kỹ" in str(selsun_usage.value).casefold()


def test_cleaned_thanh_lam_product_details_meets_zero_defect_quality_gate() -> None:
    details_path = (
        Path(__file__).resolve().parents[1] / "data" / "thanh_lam_product_details.json"
    )
    payload = json.loads(details_path.read_text(encoding="utf-8"))
    assert payload["enriched_products"] >= 2000
    assert payload["field_counts"]["ingredients"] >= 1200
    audit = payload.get("quality_audit_after", {})
    remaining_defects = {k: v for k, v in audit.items() if k.startswith("defect_") and v > 0}
    assert remaining_defects == {}, f"Expected 0 defects, found: {remaining_defects}"


def test_product_card_exposes_safe_https_image_url(
    snapshot_repository: SnapshotProductRepository,
) -> None:
    from backend.api.main import _product_card, _safe_product_image_url

    chinsu = snapshot_repository.get_product_snapshot("TLM_10141747")
    assert chinsu is not None
    card = _product_card(chinsu)
    assert card.image_url is not None
    assert card.image_url.startswith("https://")
    assert _safe_product_image_url("javascript:alert(1)") is None
    assert _safe_product_image_url("https://example.com/a b.jpg") is None


def test_canonical_dataset_preserves_benefits_usage_ingredients_and_thanh_lam_profile(
    snapshot_repository: SnapshotProductRepository,
) -> None:
    from backend.services.organization_profiles import get_organization_answer

    # 1. Thanh Lam Mart profile resolves from canonical verified_organization_facts.json
    #    and is never intercepted as an out-of-catalog product by streaming consultation.
    org = get_organization_answer("Giới thiệu Thanh Lam Mart", "vi")
    assert org is not None
    assert org.profile.entity_id == "thanh_lam_mart"
    assert any("Thanh Lam Mart" in fact for fact in org.facts)
    from backend.config import Settings
    from backend.services.customer_consultation import prepare_streaming_consultation
    from backend.services.groq import GroqService
    from backend.services.sessions import InMemorySessionStore

    stub = GroqService(settings=Settings(_env_file=None, chat_llm_api_key="test-key"))
    rec = InMemorySessionStore().create("thanh-lam-org")
    import asyncio

    assert (
        asyncio.run(
            prepare_streaming_consultation(
                text="Giới thiệu Thanh Lam Mart",
                record=rec,
                repository=snapshot_repository,
                groq=stub,
            )
        )
        is None
    )

    # 2. All three preserved enrichment dimensions (công dụng, cách sử dụng, thành phần chính)
    #    remain bound and retrievable against the canonical 3,099-SKU catalog.
    pilot_sku = snapshot_repository.get_product_snapshot("TLM_10016738")
    assert pilot_sku is not None
    pilot_benefits = product_knowledge_facts(
        pilot_sku,
        ("benefits",),
        source="thanh_lam_snapshot",
        catalog_version=snapshot_repository.dataset_sha256,
    )[0]
    assert pilot_benefits.value is not None and pilot_benefits.status.value != "UNKNOWN"

    selsun = snapshot_repository.get_product_snapshot("TLM_10016912")
    assert selsun is not None
    facts = {
        fact.attribute: fact
        for fact in product_knowledge_facts(
            selsun,
            ("description", "usage", "ingredients"),
            source="thanh_lam_snapshot",
            catalog_version=snapshot_repository.dataset_sha256,
        )
    }
    assert facts["description"].value is not None and facts["description"].status.value != "UNKNOWN"
    assert facts["usage"].value is not None and facts["usage"].status.value == "VERIFIED"
    assert (
        facts["ingredients"].value is not None
        and facts["ingredients"].status.value == "VERIFIED"
    )

    # 3. Companion canonical datasets from both repos & bridge are present on disk.
    data_dir = Path(__file__).resolve().parents[1] / "data"
    assert (data_dir / "vietnamese_product_terms.json").is_file()
    assert (data_dir / "retail_reference_guidance.json").is_file()
    assert (data_dir / "bridge" / "crosswalk.csv").is_file()
    assert (data_dir / "bridge" / "ops_catalog_overrides.json").is_file()
    assert (data_dir / "ops_chest_frn" / "frn" / "stores_daily.csv.gz").is_file()





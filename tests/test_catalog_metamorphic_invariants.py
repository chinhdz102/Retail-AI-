"""Seeded, catalog-wide invariants for search identity and hard filters.

These are engineering regressions over the local snapshot, not human relevance
judgments or evidence of real-world recommendation suitability.
"""

from __future__ import annotations

import json
import random
import unicodedata
from pathlib import Path

import pytest

from backend.products.category_taxonomy import normalize_category_text
from backend.products.snapshot_repository import SnapshotProductRepository
from backend.services.retail_tools import execute_catalog_tool

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "data" / "winmart_products.json"
SEED = 20261029


@pytest.fixture(scope="module")
def snapshot() -> SnapshotProductRepository:
    return SnapshotProductRepository(CATALOG)


def _without_vietnamese_marks(value: str) -> str:
    decomposed = unicodedata.normalize("NFD", value.replace("đ", "d").replace("Đ", "D"))
    return "".join(char for char in decomposed if unicodedata.category(char) != "Mn")


def test_seeded_title_variants_preserve_catalog_identity_and_determinism(
    snapshot: SnapshotProductRepository,
) -> None:
    """Punctuation, accent folding, and repeat calls must not lose the SKU."""
    rng = random.Random(SEED)
    sample = rng.sample(snapshot.products, 64)

    for product in sample:
        variants = (
            product.name,
            f"!!! {product.name} ???",
            _without_vietnamese_marks(product.name),
        )
        for query in variants:
            first = snapshot.search(query, limit=5)
            second = snapshot.search(query, limit=5)
            first_ids = [item.product_id for item in first]
            assert product.product_id in first_ids, (product.product_id, query, first_ids)
            assert first_ids == [item.product_id for item in second]

        exact_identifier = snapshot.search(product.product_id.lower(), limit=5)
        assert [item.product_id for item in exact_identifier] == [product.product_id]
        assert snapshot.get_product_snapshot(f"{product.product_id}_NOT_A_REAL_ID") is None


def test_seeded_price_caps_are_hard_and_tightening_is_monotonic(
    snapshot: SnapshotProductRepository,
) -> None:
    """A lower price cap cannot admit a more expensive or previously excluded SKU."""
    rng = random.Random(SEED + 1)
    sample = rng.sample(snapshot.products, min(320, len(snapshot.products)))
    categories: list[str] = []
    seen_categories: set[str] = set()
    for product in sample:
        category = product.category
        normalized = normalize_category_text(category)
        if category and normalized not in seen_categories:
            categories.append(category)
            seen_categories.add(normalized)
        if len(categories) >= 32:
            break

    checked = 0
    for category in categories:
        broad = snapshot.search(query="", category=category, limit=20)
        if not broad:
            continue
        cap = broad[max(0, (len(broad) - 1) // 2)].price_vnd
        capped = snapshot.search(
            query="", category=category, max_price_vnd=cap, limit=20
        )
        broad_ids = {item.product_id for item in broad}
        capped_ids = {item.product_id for item in capped}

        assert all(item.price_vnd <= cap for item in capped), category
        assert capped_ids <= broad_ids, category
        checked += 1

    # Some sampled category labels are not independently searchable leaves;
    # keep the minimum on successfully retrieved catalog scopes explicit.
    assert checked >= 12


def test_seeded_brand_negation_never_returns_the_excluded_catalog_brand(
    snapshot: SnapshotProductRepository,
) -> None:
    """Exercise natural-language brand exclusion across many real catalog slices."""
    rng = random.Random(SEED + 2)
    sample = rng.sample(snapshot.products, 80)
    scopes: list[tuple[str, str]] = []
    seen_scopes: set[tuple[str, str]] = set()
    for product in sample:
        if not product.brand or not product.category:
            continue
        scope = (normalize_category_text(product.category), normalize_category_text(product.brand))
        if scope not in seen_scopes:
            scopes.append((product.category, product.brand))
            seen_scopes.add(scope)
        if len(scopes) >= 32:
            break

    nonempty = 0
    for category, excluded_brand in scopes:
        query = f"Cho mình xem {category} nhưng không lấy hãng {excluded_brand}"
        observation = execute_catalog_tool(
            "search_products",
            json.dumps({"query": query, "limit": 5}, ensure_ascii=False),
            repository=snapshot,
            current_user_text=query,
        )
        assert observation.status == "SUCCESS", (category, excluded_brand, observation.content)
        if observation.products:
            nonempty += 1
        assert all(
            normalize_category_text(product.brand)
            != normalize_category_text(excluded_brand)
            for product in observation.products
        ), (category, excluded_brand, [item.product_id for item in observation.products])

    assert len(scopes) >= 24
    assert nonempty >= 8


def test_seeded_unseen_tokens_return_no_fabricated_catalog_product(
    snapshot: SnapshotProductRepository,
) -> None:
    rng = random.Random(SEED + 3)
    alphabet = "qxzjvwy"

    for _ in range(32):
        unknown_token = "".join(rng.choice(alphabet) for _ in range(24))
        assert snapshot.search(unknown_token, limit=5) == []


def test_new_snapshot_sku_is_indexed_without_product_specific_code(
    tmp_path: Path,
) -> None:
    """A new catalog row is immediately addressable through the generic index."""
    source_row = json.loads(CATALOG.read_text(encoding="utf-8"))[0]
    new_product = {
        **source_row,
        "product_id": "WIN_TEST_DYNAMIC_CATALOG_0001",
        "item_no": "TEST_DYNAMIC_CATALOG_0001",
        "barcode": "99999102610291",
        "name": "Sản phẩm kiểm thử catalog mới QXZV 250g",
        "brand": "NHÃN KIỂM THỬ",
        "category_code": "TEST_DYNAMIC_001",
        "category_name": "Thực phẩm khô kiểm thử",
        "department": "Thực phẩm",
        "sub_department": "Thực phẩm công nghệ",
        "category_hierarchy": "Thực phẩm > Thực phẩm công nghệ > Thực phẩm khô kiểm thử",
        "price_vnd": 10_000,
        "sale_price_vnd": 10_000,
        "stock": 1,
        "uom": "Gói",
        "description": "Synthetic row used only to test catalog index rebuilding.",
        "scraped_at": "2026-10-29T00:00:00+00:00",
    }
    path = tmp_path / "new-catalog-version.json"
    path.write_text(json.dumps([new_product], ensure_ascii=False), encoding="utf-8")

    updated_snapshot = SnapshotProductRepository(path)

    assert updated_snapshot.dataset_sha256
    assert updated_snapshot.get_product_snapshot(new_product["product_id"]) is not None
    assert [item.product_id for item in updated_snapshot.search(new_product["name"], limit=5)] == [
        new_product["product_id"]
    ]

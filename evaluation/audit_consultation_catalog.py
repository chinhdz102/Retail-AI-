"""Offline retrieval/constraint audit against the actual local catalog.

This does not call a model, rate semantic advice, or establish current stock.
Run: python -m evaluation.audit_consultation_catalog
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError

from backend.products.category_taxonomy import normalize_category_text as norm
from backend.products.snapshot_repository import SnapshotProductRepository
from backend.services.customer_consultation import (
    ConsultationDecision,
    SearchNeed,
    _allowed,
    _preserve_constraints,
    _retrieve,
)


def audit(snapshot: Path) -> dict:
    root = Path(__file__).resolve().parents[1]
    source_paths = [
        root / name
        for name in (
            "backend/services/customer_consultation.py",
            "backend/products/snapshot_repository.py",
            "backend/products/category_taxonomy.py",
        )
    ]
    source_hashes = {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in source_paths
    }
    repository = SnapshotProductRepository(snapshot)
    products = repository.products
    paths = repository.list_category_hierarchies()
    leaves: dict[str, list[tuple[str, ...]]] = defaultdict(list)
    for path in paths:
        leaves[norm(path[-1])].append(path)
    parent_leaf_collisions = [
        {
            "leaf": group[0][-1],
            "leaf_paths": [" > ".join(path) for path in group],
            "descendant_paths": [
                " > ".join(path)
                for path in paths
                if any(norm(part) == key for part in path[:-1]) and norm(path[-1]) != key
            ],
        }
        for key, group in leaves.items()
        if any(
            any(norm(part) == key for part in path[:-1]) and norm(path[-1]) != key for path in paths
        )
    ]
    failures: list[dict] = []
    checks: Counter = Counter()

    def check(kind, condition, **details):
        checks[kind] += 1
        if not condition:
            failures.append({"check": kind, **details})

    def retrieve(searches, **kwargs):
        return _retrieve(
            repository,
            paths,
            ConsultationDecision(
                action="recommend",
                searches=searches,
                **kwargs,
            ),
            [],
        )

    # All 3,081 actual products, independent of retrieval's candidate sampling.
    for product in products:
        search = SearchNeed(
            category=(product.category_hierarchy or product.category).split(">")[-1].strip()
        )
        strict = ConsultationDecision(
            action="recommend", max_price_vnd=product.price_vnd, strict_price_limit=True
        )
        inclusive = strict.model_copy(update={"strict_price_limit": False})
        check(
            "allowed_strict_upper_bound",
            not _allowed(product, strict, search),
            product_id=product.product_id,
        )
        check(
            "allowed_inclusive_upper_bound",
            _allowed(product, inclusive, search),
            product_id=product.product_id,
        )
        exclusion = search.model_copy(update={"excluded": [product.name]})
        check(
            "allowed_explicit_exclusion",
            not _allowed(
                product,
                ConsultationDecision(action="recommend"),
                exclusion,
            ),
            product_id=product.product_id,
        )

    rows = []
    long_paths = []
    all_retrieved_ids = set()
    for path in paths:
        full = " > ".join(path)
        # The production wire schema limits category to 100 chars. Exercise the
        # private helper separately for longer canonical paths; disclose bypass.
        try:
            full_search = SearchNeed(category=full)
        except ValidationError:
            long_paths.append(full)
            full_search = SearchNeed.model_construct(category=full)
        full_rows = retrieve([full_search])
        full_wrong = [
            p.product_id
            for p in full_rows
            if norm(p.category_hierarchy or p.category) != norm(full)
        ]
        check("canonical_full_path_scope", not full_wrong, path=full, outside_scope_ids=full_wrong)
        check("canonical_full_path_nonempty", bool(full_rows), path=full)

        search = SearchNeed(category=path[-1])
        leaf_rows = retrieve([search])
        exact_leaf_paths = {norm(" > ".join(p)) for p in leaves[norm(path[-1])]}
        leaf_wrong = [
            p.product_id
            for p in leaf_rows
            if norm(p.category_hierarchy or p.category) not in exact_leaf_paths
        ]
        check(
            "exact_leaf_scope",
            not leaf_wrong,
            path=full,
            leaf=path[-1],
            outside_scope_ids=leaf_wrong,
        )
        check("exact_leaf_nonempty", bool(leaf_rows), path=full)
        all_retrieved_ids.update(p.product_id for p in leaf_rows)

        price_limit = leaf_rows[len(leaf_rows) // 2].price_vnd if leaf_rows else 100_000
        priced = retrieve([search], max_price_vnd=price_limit, strict_price_limit=True)
        check(
            "retrieved_strict_upper_bound",
            all(p.price_vnd < price_limit for p in priced),
            path=full,
            limit_vnd=price_limit,
        )
        repeated = retrieve([search, search])
        check(
            "duplicate_search_deduplication",
            len({p.product_id for p in repeated}) == len(repeated),
            path=full,
        )

        excluded_title = (
            leaf_rows[0].name
            if leaf_rows
            else next(
                p.name for p in products if norm(p.category_hierarchy or p.category) == norm(full)
            )
        )
        excluding = search.model_copy(update={"excluded": [excluded_title]})
        excluded = retrieve([excluding])
        check(
            "retrieved_explicit_exclusion",
            all(norm(excluded_title) not in norm(p.name) for p in excluded),
            path=full,
        )

        previous = {
            "need": "Chọn " + path[-1],
            "constraints": ConsultationDecision(
                action="recommend",
                searches=[excluding],
                max_price_vnd=100_000,
                strict_price_limit=True,
            ).model_dump(),
        }
        followup = _preserve_constraints(
            ConsultationDecision(
                action="recommend",
                searches=[search.model_copy(deep=True)],
            ),
            previous,
            "Còn loại nào khác?",
            parsed_budget=None,
            explicit_sizes=set(),
        )
        check(
            "followup_price_preservation",
            followup.max_price_vnd == 100_000 and followup.strict_price_limit,
            path=full,
        )
        check(
            "followup_exclusion_preservation",
            all(excluded_title in item.excluded for item in followup.searches),
            path=full,
            excluded_title=excluded_title,
        )
        rows.append(
            {
                "path": full,
                "leaf": path[-1],
                "full_path_candidates": len(full_rows),
                "full_path_outside_scope": len(full_wrong),
                "leaf_candidates": len(leaf_rows),
                "leaf_outside_scope": len(leaf_wrong),
                "strict_limit_vnd": price_limit,
                "strict_price_candidates": len(priced),
                "excluded_candidates": len(excluded),
                "full_path_exceeds_wire_schema_length": len(full) > 100,
            }
        )

    # Stock is observational: snapshot rows have no current inventory authority.
    # Mutating only stock fields on actual rows isolates whether _allowed itself
    # blocks current zero/expired stock; these are probes, not claimed catalog facts.
    neutral = ConsultationDecision(action="recommend")
    stock_search = SearchNeed(category="stock probe")
    stock = {
        "recorded_zero_products": sum(p.stock == 0 for p in products),
        "noncurrent_products": sum(not p.stock_is_current for p in products),
        "current_products": sum(p.stock_is_current for p in products),
        "sellable_stock_zero_products": sum(p.sellable_stock == 0 for p in products),
        "unique_retrieved_products_with_recorded_zero": sum(
            p.product_id in all_retrieved_ids and p.stock == 0 for p in products
        ),
        "allowed_noncurrent_products": sum(
            _allowed(p, neutral, stock_search) for p in products if not p.stock_is_current
        ),
        "current_zero_probe_allowed": sum(
            _allowed(
                p.model_copy(
                    update={
                        "stock": 0,
                        "stock_is_current": True,
                    }
                ),
                neutral,
                stock_search,
            )
            for p in products
        ),
        "expired_current_probe_allowed": sum(
            _allowed(
                p.model_copy(
                    update={
                        "stock": 1,
                        "stock_is_current": True,
                        "expiry_at": "2000-01-01",
                    }
                ),
                neutral,
                stock_search,
            )
            for p in products
        ),
        "interpretation": "Retrieval permits catalog discovery without current stock evidence. "
        "_allowed has no availability/expiry gate, including for synthetic current-zero rows. "
        "This audit does not inspect generated stock claims or checkout eligibility.",
    }
    counts = Counter(item["check"] for item in failures)
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "scope": "Offline retrieval constraints only; no network/model calls "
        "or semantic advice rating.",
        "source_sha256": source_hashes,
        "source_unchanged_during_audit": all(
            hashlib.sha256(path.read_bytes()).hexdigest()
            == source_hashes[str(path.relative_to(root))]
            for path in source_paths
        ),
        "snapshot": str(snapshot.resolve()),
        "dataset_sha256": repository.dataset_sha256,
        "inventory": {
            "products": len(products),
            "taxonomy_paths": len(paths),
            "unique_leaf_labels": len(leaves),
            "duplicate_product_ids": len(products) - len({p.product_id for p in products}),
        },
        "methodology": [
            "All actual products exercise _allowed strict/inclusive equality "
            "and exact-title exclusion.",
            "Every taxonomy path exercises _retrieve full-path and exact-leaf scope, strict price, "
            "explicit exclusion, repeated-search deduplication "
            "and same-scope follow-up preservation.",
            "Scope expectations require exact canonical path; leaf-only queries may span identical "
            "leaf labels, but unrelated or merely overlapping labels count as broadening.",
            "A leaf label that is also an ancestor can legitimately describe descendants; "
            "exact_leaf_scope deliberately uses the stricter leaf-equality expectation. "
            "See parent_leaf_label_collisions before treating such breadth as a bug.",
            "Long full paths use SearchNeed.model_construct only to audit the helper beyond the "
            "100-character wire schema limit; this is explicitly not a valid provider payload.",
            "Candidate recall is not scored because _retrieve samples at most "
            "eight products per search.",
        ],
        "checks": {
            kind: {"total": total, "failed": counts[kind], "passed": total - counts[kind]}
            for kind, total in sorted(checks.items())
        },
        "failure_count": len(failures),
        "failures": failures,
        "ambiguous_leaf_labels": [
            {"leaf": group[0][-1], "paths": [" > ".join(path) for path in group]}
            for group in leaves.values()
            if len(group) > 1
        ],
        "full_path_wire_valid_results": {
            "total": sum(not row["full_path_exceeds_wire_schema_length"] for row in rows),
            "empty": sum(
                not row["full_path_exceeds_wire_schema_length"] and not row["full_path_candidates"]
                for row in rows
            ),
            "scope_broadened": sum(
                not row["full_path_exceeds_wire_schema_length"]
                and bool(row["full_path_outside_scope"])
                for row in rows
            ),
        },
        "full_paths_exceeding_wire_schema_length": long_paths,
        "parent_leaf_label_collisions": parent_leaf_collisions,
        "stock_observations": stock,
        "path_results": rows,
    }


def main():
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, default=root / "data/thanh_lam_products.json")
    parser.add_argument(
        "--output",
        type=Path,
        default=root / ("docs/reports/artifacts/consultation_catalog_constraints_20261103.json"),
    )
    args = parser.parse_args()
    report = audit(args.snapshot)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    summary = {
        key: report[key] for key in ("inventory", "checks", "failure_count", "stock_observations")
    }
    print(json.dumps(summary, ensure_ascii=True, indent=2))
    print(f"Artifact: {args.output}")


if __name__ == "__main__":
    main()

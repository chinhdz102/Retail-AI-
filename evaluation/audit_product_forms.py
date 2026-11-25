"""Audit literal product-form observations over every validated snapshot row."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path

from backend.products.snapshot_repository import SnapshotProductRepository
from backend.services.product_form import product_form_evidence


def audit(source: Path) -> dict:
    repository = SnapshotProductRepository(source)
    counts: Counter[str] = Counter()
    examples: dict[str, list[dict]] = defaultdict(list)
    observed = integrity_errors = 0
    for product in repository.products:
        observations = product_form_evidence(product)
        observed += bool(observations)
        for observation in observations:
            key = observation["value"] + ":" + observation["subject"]
            counts[key] += 1
            integrity_errors += observation["quote"] not in product.name
            if len(examples[key]) < 4:
                examples[key].append({
                    "product_id": product.product_id, "name": product.name,
                    "category_hierarchy": product.category_hierarchy,
                    "observation": observation,
                })
    return {
        "kind": "catalog_title_form_observation_audit",
        "generated_at": datetime.now(UTC).isoformat(),
        "source": str(source), "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "products": len(repository.products), "taxonomy_paths": len(
            repository.list_category_hierarchies(),
        ),
        "products_with_observations": observed,
        "products_with_unknown_form": len(repository.products) - observed,
        "literal_quote_integrity_errors": integrity_errors,
        "observation_counts": dict(sorted(counts.items())),
        "examples": dict(sorted(examples.items())),
        "interpretation": [
            "Coverage is vocabulary coverage, not accuracy or shopping-task success.",
            "Each observation is bounded title wording, never verified ingredients or suitability.",
            "Product, component, tool-function and product-line subjects must remain distinct.",
            "Instant/premixed wording does not prove readiness to eat or cooking directions.",
            "No category, use-case, product-ID or recommendation mapping drives observations.",
            "Absent observations are unknown; they must not become hard exclusions.",
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path("data/thanh_lam_products.json"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.source)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    print(json.dumps({key: value for key, value in result.items() if key != "examples"},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

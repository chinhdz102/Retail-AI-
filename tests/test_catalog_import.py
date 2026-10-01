import json
from pathlib import Path

from backend.data.catalog_import import analyze_dataset

ROOT = Path(__file__).resolve().parents[1]


def test_winmart_catalog_dry_run_validates_and_quarantines_price_anomalies() -> None:
    _, quarantined, report = analyze_dataset(ROOT / "data" / "winmart_products.json")

    assert report["valid"] is True
    assert report["rows_total"] == 3099
    assert report["rows_accepted"] == 3081
    assert report["rows_quarantined"] == 18
    assert report["quarantine_reasons"] == {"SALE_PRICE_EXCEEDS_REGULAR_PRICE": 18}
    assert report["warning_counts"] == {
        "description_equals_name": 2860,
        "brand_missing": 30,
        "image_missing": 3,
    }
    assert len(quarantined) == 18


def test_winmart_catalog_import_rejects_duplicate_identifiers(tmp_path: Path) -> None:
    source = json.loads((ROOT / "data" / "winmart_products.json").read_text(encoding="utf-8"))
    source.append(source[0])
    path = tmp_path / "duplicate.json"
    path.write_text(json.dumps(source, ensure_ascii=False), encoding="utf-8")

    _, _, report = analyze_dataset(path)

    assert report["valid"] is False
    assert any("duplicate product_id" in str(error) for error in report["fatal_errors"])

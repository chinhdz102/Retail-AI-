from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from backend.config import get_settings
from backend.data.catalog_import import analyze_dataset, import_rows

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate and import the WinMart product catalog.")
    parser.add_argument("--source", type=Path, default=ROOT / "data" / "winmart_products.json")
    parser.add_argument("--report", type=Path)
    parser.add_argument("--apply", action="store_true", help="Write validated rows to PostgreSQL.")
    args = parser.parse_args()

    rows, quarantined, report = analyze_dataset(args.source)
    report["mode"] = "apply" if args.apply else "dry-run"
    if args.apply and report["valid"]:
        asyncio.run(import_rows(get_settings().database_url, rows, quarantined, report))
        report["imported"] = True
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

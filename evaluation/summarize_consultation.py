"""Summarize retained HTTP runs; separate integrity from qualitative review."""

import argparse
import json
import math
import re
import statistics
import unicodedata
from collections import Counter
from pathlib import Path


def inspect_turn(row, catalog):
    payload = row.get("response", {})
    products = payload.get("products", [])
    issues = []
    if row.get("http_status") != 200:
        return {"data_issues": ["http_error"], "answered": False}
    if payload.get("grounded_product_ids", []) != [p["product_id"] for p in products]:
        issues.append("card_ids_disagree")
    text = unicodedata.normalize("NFC", payload.get("text", ""))
    costs = []
    for product in products:
        source = catalog.get(product["product_id"])
        if not source or product["price_vnd"] != min(
            source["price_vnd"], source["sale_price_vnd"],
        ):
            issues.append("card_price_not_in_source")
        name = unicodedata.normalize("NFC", product["name"])
        match = re.search(re.escape(name) + (
            r"(?:\s*×\s*(?P<quantity>\d+)|\s*—\s*(?P<grams>\d+)g)?"
            r"\s*\((?P<price>[\d.]+)đ(?P<per_kg>/kg)?\)"
        ), text)
        if not match:
            issues.append("card_without_rendered_price")
            continue
        quantity = int(match["quantity"] or 1)
        expected = product["price_vnd"] * quantity
        if match["grams"]:
            if (product.get("uom") or "").casefold() != "kg":
                issues.append("grams_on_packaged_item")
            expected = (product["price_vnd"] * int(match["grams"]) + 500) // 1000
        if int(match["price"].replace(".", "")) != expected:
            issues.append("rendered_price_arithmetic")
        if not match["per_kg"]:
            costs.append(expected)
    totals = re.findall(r"Tổng:\s*([\d.]+)đ", text)
    if len(totals) > 1:
        issues.append("duplicate_total")
    if totals and (len(costs) != len(products) or
                   int(totals[-1].replace(".", "")) != sum(costs)):
        issues.append("basket_total_arithmetic")
    return {"data_issues": sorted(set(issues)), "answered": True,
            "cards": len(products), "characters": len(text)}


def main(args):
    root = Path(__file__).resolve().parents[1]
    catalog = {p["product_id"]: p for p in json.loads(
        (root / "data/winmart_products.json").read_text(encoding="utf-8"))}
    reviews = json.loads(args.reviews.read_text(encoding="utf-8")) if args.reviews else {}
    result = {"reviewer": "assistant qualitative review; not an independent human study",
              "qualitative_review_is_separate_from_data_checks": True, "runs": []}
    for path in args.inputs:
        source = json.loads(path.read_text(encoding="utf-8"))
        rows = []
        for case in source["cases"]:
            for index, turn in enumerate(case["turns"], 1):
                key = f"{case['id']}:{index}"
                rows.append({"key": key, **inspect_turn(turn, catalog),
                             "qualitative_review": reviews.get(path.name, {}).get(key)})
        answered = [turn for case in source["cases"] for turn in case["turns"]
                    if turn.get("http_status") == 200]
        times = sorted(turn["seconds"] for turn in answered)
        result["runs"].append({
            "artifact": path.name, "complete": source["complete"], "turns": len(rows),
            "http_200": len(answered), "http_error": len(rows) - len(answered),
            "models": dict(Counter(turn["response"]["model"] for turn in answered)),
            "median_seconds_http_200": statistics.median(times) if times else None,
            "p95_seconds_http_200": times[math.ceil(.95 * len(times)) - 1] if times else None,
            "data_integrity_issues": sum(bool(row["data_issues"]) for row in rows
                                         if row["answered"]),
            "qualitative_ratings": dict(Counter(row["qualitative_review"]["rating"] for row in rows
                                                 if row["qualitative_review"])),
            "rows": rows,
        })
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps([{k: v for k, v in run.items() if k != "rows"}
                      for run in result["runs"]], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inputs", type=Path, nargs="+")
    parser.add_argument("--reviews", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    main(parser.parse_args())

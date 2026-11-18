"""Run catalog-grounded Vietnamese recommendation cases against the live API."""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import statistics
import unicodedata
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any
from uuid import uuid4

import httpx

ROOT = Path(__file__).resolve().parents[1]


def normalize(value: Any) -> str:
    text = unicodedata.normalize("NFD", str(value or "").casefold())
    text = "".join(char for char in text if unicodedata.category(char) != "Mn")
    return " ".join(text.replace("đ", "d").split())


def contains(text: str, term: Any) -> bool:
    needle = normalize(term)
    return bool(needle) and re.search(rf"(?<!\w){re.escape(needle)}(?!\w)", text) is not None


def price(row: dict[str, Any]) -> int | None:
    value = row.get("sale_price_vnd") or row.get("price_vnd")
    return int(value) if isinstance(value, (int, float)) else None


def category(row: dict[str, Any]) -> str:
    return str(row.get("category_name") or row.get("category") or "")


def load_catalog(path: Path) -> dict[str, dict[str, Any]]:
    rows = json.loads(path.read_text(encoding="utf-8"))
    return {str(row["product_id"]): row for row in rows}


def resolved_products(
    body: dict[str, Any], catalog: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    body_products = {
        str(row.get("product_id")): row
        for row in body.get("products", [])
        if isinstance(row, dict) and row.get("product_id")
    }
    products = []
    for product_id in body.get("grounded_product_ids", []):
        product_id = str(product_id)
        raw = catalog.get(product_id, {})
        shown = body_products.get(product_id, {})
        products.append(
            {
                "product_id": product_id,
                "name": shown.get("name") or raw.get("name") or "",
                "category": shown.get("category") or category(raw),
                "price_vnd": shown.get("price_vnd")
                if shown.get("price_vnd") is not None
                else price(raw),
                "stock": shown.get("stock") if shown.get("stock") is not None else raw.get("stock"),
                "promotion": raw.get("discount_promotion"),
                "in_catalog": product_id in catalog,
            }
        )
    return products


def check_products(
    products: list[dict[str, Any]], checks: dict[str, Any], body: dict[str, Any]
) -> dict[str, bool]:
    names = [normalize(row["name"]) for row in products]
    categories = [normalize(row["category"]) for row in products]
    prices = [row["price_vnd"] for row in products if row["price_vnd"] is not None]
    text = normalize(body.get("text", ""))
    tools = {str(tool) for tool in body.get("tools_used", [])}
    result: dict[str, bool] = {
        "http_200": True,
        "all_in_catalog": all(row["in_catalog"] for row in products),
    }
    if "min_products" in checks:
        result["min_products"] = len(products) >= int(checks["min_products"])
    if "max_products" in checks:
        result["max_products"] = len(products) <= int(checks["max_products"])
    if checks.get("no_products"):
        result["no_products"] = not products
    if "required_tools" in checks:
        result["required_tools"] = all(tool in tools for tool in checks["required_tools"])
    if "forbidden_tools" in checks:
        result["forbidden_tools"] = not any(tool in tools for tool in checks["forbidden_tools"])
    if "allowed_categories" in checks:
        allowed = {normalize(item) for item in checks["allowed_categories"]}
        result["allowed_categories"] = bool(products) and all(
            item in allowed for item in categories
        )
    if "category_prefix_allow" in checks:
        prefixes = tuple(normalize(item) for item in checks["category_prefix_allow"])
        result["category_prefix_allow"] = bool(products) and all(
            any(item.startswith(prefix) for prefix in prefixes) for item in categories
        )
    if "required_categories" in checks:
        result["required_categories"] = all(
            normalize(item) in categories for item in checks["required_categories"]
        )
    if "min_distinct_categories" in checks:
        result["min_distinct_categories"] = len(set(categories)) >= int(
            checks["min_distinct_categories"]
        )
    if "price_lt" in checks:
        result["price_lt"] = bool(prices) and all(
            value < int(checks["price_lt"]) for value in prices
        )
    if "price_lte" in checks:
        result["price_lte"] = bool(prices) and all(
            value <= int(checks["price_lte"]) for value in prices
        )
    if "name_contains_all" in checks:
        result["name_contains_all"] = bool(names) and all(
            all(contains(name, term) for term in checks["name_contains_all"]) for name in names
        )
    if "name_contains_any" in checks:
        result["name_contains_any"] = bool(names) and all(
            any(contains(name, term) for term in checks["name_contains_any"]) for name in names
        )
    if "name_not_contains" in checks:
        result["name_not_contains"] = not any(
            contains(name, term) for name in names for term in checks["name_not_contains"]
        )
    if checks.get("promo_only"):
        result["promo_only"] = bool(products) and all(bool(row["promotion"]) for row in products)
    if "text_contains" in checks:
        result["text_contains"] = all(contains(text, term) for term in checks["text_contains"])
    if "text_not_contains" in checks:
        result["text_not_contains"] = not any(
            contains(text, term) for term in checks["text_not_contains"]
        )
    return result


def grade(
    body: dict[str, Any],
    status: int,
    elapsed_ms: int,
    checks: dict[str, Any],
    catalog: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    if status != 200:
        return {"checks": {"http_200": False}, "passed": False, "products": []}
    products = resolved_products(body, catalog)
    graded = check_products(products, checks, body)
    if "max_seconds" in checks:
        graded["max_seconds"] = elapsed_ms <= int(checks["max_seconds"])
    return {"checks": graded, "passed": all(graded.values()), "products": products}


async def run(args: argparse.Namespace) -> dict[str, Any]:
    case_set = json.loads(args.case_file.read_text(encoding="utf-8"))
    catalog = load_catalog(ROOT / "data/thanh_lam_products.json")
    wanted = set(args.ids.split(",")) if args.ids else None
    cases = [case for case in case_set["cases"] if wanted is None or case["case_id"] in wanted]
    report: dict[str, Any] = {
        "started_utc": datetime.now(UTC).isoformat(),
        "dataset_id": case_set["dataset_id"],
        "base_url": args.base_url,
        "cases": [],
        "complete": False,
    }
    async with httpx.AsyncClient(base_url=args.base_url, timeout=args.timeout) as client:
        for case in cases:
            session = await client.post(
                "/sessions", json={"kiosk_id": "catalog-recommendation-matrix"}
            )
            session.raise_for_status()
            session_id = session.json()["session_id"]
            case_result = {
                "case_id": case["case_id"],
                "suite": case.get("suite"),
                "reasoning_category": case.get("reasoning_category"),
                "turns": [],
            }
            try:
                for number, turn in enumerate(case["turns"], 1):
                    started = perf_counter()
                    try:
                        response = await client.post(
                            "/agent/respond",
                            json={
                                "session_id": session_id,
                                "text": turn["text"],
                                "language": "vi",
                                "client_turn_id": str(uuid4()),
                            },
                        )
                        status = response.status_code
                        body = response.json() if status == 200 else {}
                    except httpx.HTTPError as error:
                        status, body = 0, {"error": type(error).__name__}
                    elapsed_ms = round((perf_counter() - started) * 1000)
                    required = grade(body, status, elapsed_ms, turn.get("required", {}), catalog)
                    optional = grade(body, status, elapsed_ms, turn.get("optional", {}), catalog)
                    row = {
                        "number": number,
                        "question": turn["text"],
                        "http_status": status,
                        "elapsed_ms": elapsed_ms,
                        "model": body.get("model"),
                        "tools_used": body.get("tools_used", []),
                        "grounded_product_ids": body.get("grounded_product_ids", []),
                        "text": body.get("text", body.get("error", "")),
                        "required": required,
                        "optional": optional,
                    }
                    case_result["turns"].append(row)
                    print(
                        json.dumps(
                            {
                                "case": case["case_id"],
                                "turn": number,
                                "status": status,
                                "passed": required["passed"],
                                "model": body.get("model"),
                                "elapsed_ms": elapsed_ms,
                            },
                            ensure_ascii=False,
                        ),
                        flush=True,
                    )
                    if args.pause_seconds:
                        await asyncio.sleep(args.pause_seconds)
            finally:
                await client.post(f"/sessions/{session_id}/close")
            case_result["passed"] = all(turn["required"]["passed"] for turn in case_result["turns"])
            report["cases"].append(case_result)
    turns = [turn for case in report["cases"] for turn in case["turns"]]
    required_checks = [check for turn in turns for check in turn["required"]["checks"].values()]
    optional_checks = [
        check
        for turn in turns
        if turn["optional"]["checks"]
        for check in turn["optional"]["checks"].values()
    ]
    by_suite: dict[str, dict[str, Any]] = {}
    for case in report["cases"]:
        suite = case["suite"] or "unclassified"
        entry = by_suite.setdefault(suite, {"cases": 0, "passed": 0})
        entry["cases"] += 1
        entry["passed"] += int(case["passed"])
    for entry in by_suite.values():
        entry["pass_rate"] = round(entry["passed"] / entry["cases"], 4) if entry["cases"] else 0
    report["summary"] = {
        "cases": len(report["cases"]),
        "case_passed": sum(int(case["passed"]) for case in report["cases"]),
        "case_pass_rate": round(
            sum(int(case["passed"]) for case in report["cases"]) / len(report["cases"]), 4
        )
        if report["cases"]
        else 0,
        "turns": len(turns),
        "required_rubric_pass_rate": round(sum(required_checks) / len(required_checks), 4)
        if required_checks
        else 0,
        "optional_rubric_pass_rate": round(sum(optional_checks) / len(optional_checks), 4)
        if optional_checks
        else None,
        "by_suite": by_suite,
        "median_elapsed_ms": statistics.median(turn["elapsed_ms"] for turn in turns)
        if turns
        else None,
    }
    report["complete"] = True
    report["finished_utc"] = datetime.now(UTC).isoformat()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False))
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--case-file",
        type=Path,
        default=ROOT / "evaluation/cases/vietnamese_catalog_recommendation_v1.json",
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--ids")
    parser.add_argument("--pause-seconds", type=float, default=1.0)
    parser.add_argument("--timeout", type=float, default=120.0)
    asyncio.run(run(parser.parse_args()))

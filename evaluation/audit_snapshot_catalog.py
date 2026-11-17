"""Check the running API against the unchanged, local snapshot catalog."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
from pathlib import Path

import httpx

from backend.products.postgres_repository import normalize_search_text


def words(text: str) -> set[str]:
    return set(re.findall(r"\w+", normalize_search_text(text)))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--chat", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    source = root / "data/winmart_products.json"
    raw = source.read_bytes()
    catalog = {row["product_id"]: row for row in json.loads(raw)}
    report = {
        "catalog": str(source), "catalog_sha256": hashlib.sha256(raw).hexdigest(),
        "checks": [], "conversations": [],
    }
    queries = [
        "OMO", "Hảo Hảo", "Ariel", "Milo", "Lipton", "mì Hảo Hảo", "nước giặt OMO",
        "nước giặt Ariel", "thịt bò", "dầu ăn", "dầu gội", "trà xanh", "bánh bao",
        "sữa tắm", "kem đánh răng", "nước rửa chén", "nước lau sàn", "khăn giấy",
        "giấy vệ sinh", "tã quần", "ba chỉ", "trứng gà", "gạo", "sữa tươi",
        "sữa chua", "nước khoáng", "cà phê", "nước cam", "bánh quy",
    ]
    with httpx.Client(base_url=args.base_url, timeout=50) as client:
        for query in queries:
            started = time.monotonic()
            response = client.get("/products/search", params={"q": query, "limit": 3})
            items = response.json().get("items", [])
            checks = {
                "http_ok": response.status_code == 200,
                "nonempty": bool(items),
                "title_words_match": bool(items) and all(
                    words(query) <= words(product["name"]) for product in items
                ),
                "source_prices_match": all(
                    product["product_id"] in catalog
                    and product["price_vnd"] == min(
                        catalog[product["product_id"]]["price_vnd"],
                        catalog[product["product_id"]]["sale_price_vnd"],
                    ) for product in items
                ),
            }
            report["checks"].append({
                "query": query, "checks": checks, "passed": all(checks.values()),
                "latency_ms": round((time.monotonic() - started) * 1000, 2),
                "items": items,
            })
        for category in ("drink", "snack", "dairy", "meal"):
            response = client.get("/products/search", params={"category": category, "limit": 3})
            items = response.json().get("items", [])
            report["checks"].append({
                "category": category, "passed": response.status_code == 200 and bool(items),
                "items": items,
            })
        for params in (
            {"q": "nước giặt", "brand": "AJINOMOTO"},
            {"q": "zzznoproductxyz"}, {"q": "!!!"},
        ):
            response = client.get("/products/search", params=params)
            report["checks"].append({
                "params": params,
                "passed": response.status_code == 200 and response.json().get("items") == [],
            })
        params = {
            "q": "nước giặt", "brand": "OMO", "price_operator": "LT",
            "price_value_vnd": 200_000, "limit": 20,
        }
        response = client.get("/products/search", params=params)
        items = response.json().get("items", [])
        report["checks"].append({
            "params": params, "passed": response.status_code == 200 and bool(items) and all(
                product["brand"] == "OMO" and product["price_vnd"] < 200_000
                for product in items
            ), "items": items,
        })
        for product in list(catalog.values())[::300]:
            for field in ("product_id", "item_no", "barcode"):
                response = client.get("/products/search", params={"q": product[field], "limit": 3})
                items = response.json().get("items", [])
                report["checks"].append({
                    "identifier_field": field, "query": product[field],
                    "passed": response.status_code == 200
                    and [item["product_id"] for item in items] == [product["product_id"]],
                })
        if args.chat:
            prompts = [
                "Mì Hảo Hảo giá bao nhiêu?",
                "Bánh bao nhân thịt heo trứng cút Thọ Phát gói 250g giá bao nhiêu?",
                "Tư vấn đồ uống dưới 20 nghìn.",
                "Có nước giặt OMO không?",
            ]
            for prompt in prompts:
                session = client.post("/sessions", json={"kiosk_id": "catalog-live-audit"}).json()
                started = time.monotonic()
                response = client.post("/agent/respond", json={
                    "session_id": session["session_id"], "text": prompt, "language": "vi",
                })
                payload = response.json()
                report["conversations"].append({
                    "prompt": prompt, "status": response.status_code,
                    "elapsed_seconds": round(time.monotonic() - started, 2), "response": payload,
                })
                print(json.dumps({"prompt": prompt, "status": response.status_code,
                                  "text": payload.get("text"),
                                  "product_count": len(payload.get("products", []))},
                                 ensure_ascii=False), flush=True)
    report["summary"] = {
        "total": len(report["checks"]),
        "passed": sum(check["passed"] for check in report["checks"]),
        "failed": [
            {key: value for key, value in check.items() if key != "items"}
            for check in report["checks"] if not check["passed"]
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False))


if __name__ == "__main__":
    main()

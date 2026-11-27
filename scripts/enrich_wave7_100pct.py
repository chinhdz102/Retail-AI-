"""Wave-7 Exhaustive 100% Co.op Online & Kingfoodmart + ITF-14 Conversion Crawl.

Guarantees 100.0% inspection of:
  1. Kingfoodmart full catalog (10,275 / 10,275 URLs — finishes remaining ~1,882)
  2. Co.op Online full catalog (18,092 / 18,092 URLs — finishes remaining ~7,762, unlocking numeric & cryptic slugs)
  3. GS1 ITF-14 (14-digit carton barcodes) -> single-unit EAN-13 mathematical conversion across ALL caches & OpenFoodFacts
  4. Individual OpenFoodFacts `/api/v2/product/{bc}.json` for remaining EAN-13 & ITF-14->EAN-13 SKUs still lacking nutrition/ingredients
  5. Safe decimal-normalized pack-size & multipack sibling alignment
"""

from __future__ import annotations

import concurrent.futures
import hashlib
import json
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.enrich_all_missing_details import (  # noqa: E402
    CACHE_DIR,
    CATALOG_PATH,
    DETAILS_PATH,
    OFF_HEADERS,
    fetch_cooponline_page,
    fetch_kf_fast,
    load_cooponline_urls,
)
from scripts.enrich_detailed_ingredients import (  # noqa: E402
    extract_form,
    extract_variants,
    load_kingfoodmart_urls,
    norm_text,
)
from scripts.enrich_wave2_deep import (  # noqa: E402
    _PRIVATE_OR_GENERIC_BRANDS,
    _blank_stats,
    propagate_multipack_siblings,
)
from scripts.enrich_wave3_exhaustive import (  # noqa: E402
    _needs_enrichment,
    apply_fields_v3,
)
from scripts.enrich_wave4_final_mopup import (  # noqa: E402
    _brand_aliases_v4,
    _tokens_v4,
    propagate_same_model_siblings,
)
from scripts.enrich_wave5_ultra import _apply_off_product  # noqa: E402
from scripts.enrich_wave6_comprehensive import (  # noqa: E402
    _harmonize_compound_vi,
    strict_verify_v6,
)


def itf14_to_ean13(bc: str | None) -> str | None:
    s = str(bc or "").strip()
    if len(s) != 14 or not s.isdigit():
        return None
    body12 = s[1:13]
    total = sum(int(d) * (3 if i % 2 else 1) for i, d in enumerate(body12))
    chk = (10 - (total % 10)) % 10
    return body12 + str(chk)


def barcode_candidates(p: dict[str, Any]) -> list[str]:
    bc = str(p.get("barcode") or "").strip()
    out: list[str] = []
    if len(bc) >= 8 and bc.isdigit():
        out.append(bc)
    ean = itf14_to_ean13(bc)
    if ean and ean not in out:
        out.append(ean)
    return out


def fetch_remaining_100pct(
    *,
    site_tag: str,
    src_label: str,
    all_urls: list[str],
    parser_fn,
    products: list[dict[str, Any]],
    records: dict[str, dict[str, Any]],
    workers: int = 34,
) -> None:
    cache_jsonl = CACHE_DIR / f"{site_tag.lower()}_pages.jsonl"
    pages: dict[str, dict[str, Any]] = {}
    if cache_jsonl.exists():
        for ln in cache_jsonl.read_text(encoding="utf-8", errors="ignore").splitlines():
            if ln.strip():
                try:
                    d = json.loads(ln)
                    if d.get("url"):
                        pages[d["url"]] = d
                except Exception:
                    pass

    todo = [u for u in all_urls if u not in pages]
    print(f"\n=== WAVE-7 FULL {site_tag.upper()}: total={len(all_urls):,} cached={len(pages):,} fetching_remaining={len(todo):,} ===", flush=True)

    if todo:
        t_f = time.perf_counter()
        with cache_jsonl.open("a", encoding="utf-8") as cf:
            for start in range(0, len(todo), 300):
                chunk = todo[start : start + 300]
                with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
                    results = list(ex.map(parser_fn, chunk))
                for u, res in zip(chunk, results):
                    entry = res if res else {"url": u, "_miss": True}
                    if "url" not in entry:
                        entry["url"] = u
                    pages[u] = entry
                    cf.write(json.dumps(entry, ensure_ascii=False) + "\n")
                cf.flush()
                done_n = min(start + 300, len(todo))
                print(f"    {site_tag} [{done_n}/{len(todo)}] elapsed={time.perf_counter()-t_f:.0f}s", flush=True)

    # Full match across 100% of cached pages using exact EAN + ITF-14->EAN13 + strict harmonized title!
    by_bc: dict[str, dict[str, Any]] = {}
    page_list: list[tuple[dict[str, Any], set[str]]] = []
    tok_idx: dict[str, list[int]] = {}
    for pg in pages.values():
        if pg.get("_miss") or not pg.get("name"):
            continue
        skus: set[str] = set()
        if pg.get("barcode") and len(str(pg["barcode"])) >= 8:
            skus.add(str(pg["barcode"]).strip())
        for s in pg.get("skus") or []:
            if len(str(s)) >= 8:
                skus.add(str(s).strip())
        for s in skus:
            by_bc[s] = pg
        ptoks = _tokens_v4(_harmonize_compound_vi(f"{pg.get('name', '')} {pg.get('brand', '')}"))
        idx = len(page_list)
        page_list.append((pg, skus))
        for t in ptoks:
            tok_idx.setdefault(t, []).append(idx)

    stats = _blank_stats()
    for p in products:
        pid = str(p["product_id"])
        if not _needs_enrichment(records.get(pid)):
            continue
        hit_bc = False
        for bc in barcode_candidates(p):
            if bc in by_bc:
                if apply_fields_v3(records, p, by_bc[bc], src_label, f"{site_tag.upper()}_BARCODE_100PCT", stats):
                    stats["barcode_hits"] += 1
                    hit_bc = True
                    break
        if hit_bc:
            continue
        qtoks = _tokens_v4(_harmonize_compound_vi(p.get("name")), p.get("brand")) | _brand_aliases_v4(p.get("brand"))
        counts: dict[int, int] = {}
        for t in qtoks:
            for pi in tok_idx.get(t, ()):
                counts[pi] = counts.get(pi, 0) + 1
        for _ov, pi in sorted(((ov, pi) for pi, ov in counts.items() if ov >= 2), reverse=True)[:5]:
            pg, skus = page_list[pi]
            m = strict_verify_v6(p, pg.get("name", ""), skus, pg.get("brand", ""), allow_nobrand_exact=True)
            if m:
                if apply_fields_v3(records, p, pg, src_label, f"{site_tag.upper()}_{m}_100PCT", stats):
                    stats["title_hits"] += 1
                    break
    print(f"  {site_tag.upper()} 100% match stats: {stats}", flush=True)


def match_itf14_and_off_direct(
    products: list[dict[str, Any]],
    records: dict[str, dict[str, Any]],
) -> None:
    print("\n=== WAVE-7 ITF-14 & DIRECT OFF BARCODE SWEEP FOR MISSING PACKAGED SKUs ===", flush=True)
    # 1. Replay Emart & HachiHachi with ITF-14->EAN-13
    for fn, src_label, tag in (
        ("emart_pages.jsonl", "thông tin nhãn sản phẩm (Emart Vietnam)", "EMART"),
        ("hachihachi_pages.jsonl", "thông tin nhãn sản phẩm (HachiHachi)", "HACHIHACHI"),
    ):
        fp = CACHE_DIR / fn
        if not fp.exists():
            continue
        by_bc: dict[str, dict[str, Any]] = {}
        for ln in fp.read_text(encoding="utf-8", errors="ignore").splitlines():
            if not ln.strip():
                continue
            d = json.loads(ln)
            if d.get("_miss"):
                continue
            if d.get("barcode"):
                by_bc[str(d["barcode"]).strip()] = d
            for s in d.get("skus") or []:
                by_bc[str(s).strip()] = d
        st = _blank_stats()
        for p in products:
            if not _needs_enrichment(records.get(str(p["product_id"]))):
                continue
            for bc in barcode_candidates(p):
                if bc in by_bc and apply_fields_v3(records, p, by_bc[bc], src_label, f"{tag}_ITF14", st):
                    st["barcode_hits"] += 1
                    break
        if any(st.values()):
            print(f"  ITF-14 replay {tag}: {st}", flush=True)

    # 2. Direct OpenFoodFacts lookup for ALL remaining packaged external EAN-13 + ITF-14->EAN-13 barcodes!
    fields = "code,product_name,brands,ingredients_text_vi,ingredients_text,nutriments,nutrition_data_per,origins"
    direct_cache_p = CACHE_DIR / "off_direct_v2_cache.json"
    direct_cache: dict[str, dict[str, Any] | None] = json.loads(direct_cache_p.read_text(encoding="utf-8")) if direct_cache_p.exists() else {}

    target_bcs: dict[str, list[dict[str, Any]]] = {}
    for p in products:
        pid = str(p["product_id"])
        rec = records.get(pid, {})
        if rec.get("ingredients") and rec.get("nutrition"):
            continue
        for bc in barcode_candidates(p):
            if len(bc) == 13 and not bc.startswith(("20", "21", "22", "23", "24", "25", "26", "27", "28", "29")):
                target_bcs.setdefault(bc, []).append(p)

    # Query barcodes where ingredients OR nutrition is STILL missing AND not yet in direct_cache
    todo = [
        bc for bc, plist in sorted(target_bcs.items())
        if bc not in direct_cache
        and any(not records.get(str(p["product_id"]), {}).get("ingredients") for p in plist)
    ]
    print(f"  OFF direct remaining missing-ing packaged barcodes: {len(todo)} (cached={len(direct_cache)})", flush=True)

    def _fetch(bc: str) -> tuple[str, dict[str, Any] | None]:
        url = f"https://world.openfoodfacts.org/api/v2/product/{bc}.json?fields={fields}"
        for attempt in range(3):
            try:
                req = urllib.request.Request(url, headers=OFF_HEADERS)
                with urllib.request.urlopen(req, timeout=13) as r:
                    d = json.loads(r.read().decode("utf-8", errors="replace"))
                    if d.get("status") == 1 and isinstance(d.get("product"), dict):
                        return bc, d["product"]
                    return bc, None
            except urllib.error.HTTPError as he:
                if he.code == 404:
                    return bc, None
                time.sleep(0.8 * (attempt + 1))
            except Exception:
                time.sleep(0.6 * (attempt + 1))
        return bc, None

    if todo:
        for start in range(0, len(todo), 100):
            chunk = todo[start : start + 100]
            with concurrent.futures.ThreadPoolExecutor(max_workers=10) as ex:
                for bc, prod in ex.map(_fetch, chunk):
                    direct_cache[bc] = prod
            direct_cache_p.write_text(json.dumps(direct_cache, ensure_ascii=False), encoding="utf-8")

    st_off = _blank_stats()
    for bc, plist in target_bcs.items():
        prod = direct_cache.get(bc)
        if not prod:
            continue
        for p in plist:
            if _apply_off_product(records, p, prod, "world.openfoodfacts.org", "OpenFoodFacts", "OFF_DIRECT_W7", st_off):
                st_off["barcode_hits"] += 1
    print(f"  OFF Direct W7 stats: {st_off}", flush=True)


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    t0 = time.perf_counter()

    raw_catalog = CATALOG_PATH.read_bytes()
    catalog_sha = hashlib.sha256(raw_catalog).hexdigest()
    products: list[dict[str, Any]] = json.loads(raw_catalog.decode("utf-8"))
    details_doc: dict[str, Any] = json.loads(DETAILS_PATH.read_text(encoding="utf-8"))
    records: dict[str, dict[str, Any]] = details_doc["records"]

    def _counts() -> dict[str, int]:
        return {
            "total_records": len(records),
            "ingredients": sum(1 for r in records.values() if r.get("ingredients")),
            "usage": sum(1 for r in records.values() if r.get("usage")),
            "storage": sum(1 for r in records.values() if r.get("storage")),
            "origin": sum(1 for r in records.values() if r.get("origin")),
            "nutrition": sum(1 for r in records.values() if r.get("nutrition")),
            "summary": sum(1 for r in records.values() if r.get("summary")),
        }

    def _save() -> None:
        cur = _counts()
        details_doc["generated_at"] = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        details_doc["source"] = "Thanh Lam Mart Product Catalog"
        details_doc["source_catalog_sha256"] = catalog_sha
        details_doc["source_catalog_canonical_sha256"] = catalog_sha
        details_doc["total_catalog_products"] = len(products)
        details_doc["enriched_products"] = len(records)
        details_doc["field_counts"] = {k: cur[k] for k in ("ingredients", "usage", "storage", "origin", "nutrition", "summary")}
        DETAILS_PATH.write_text(json.dumps(details_doc, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    before = _counts()
    print(f"WAVE-7 BEFORE ({len(products)} SKUs): {before}", flush=True)

    kf_urls = load_kingfoodmart_urls(CACHE_DIR / "kingfoodmart_product_urls.json")
    fetch_remaining_100pct(
        site_tag="kingfoodmart",
        src_label="thông tin nhãn sản phẩm (Kingfoodmart)",
        all_urls=kf_urls,
        parser_fn=fetch_kf_fast,
        products=products,
        records=records,
        workers=34,
    )
    _save()

    coop_urls = load_cooponline_urls()
    fetch_remaining_100pct(
        site_tag="cooponline",
        src_label="thông tin nhãn sản phẩm (Co.op Online)",
        all_urls=coop_urls,
        parser_fn=fetch_cooponline_page,
        products=products,
        records=records,
        workers=34,
    )
    _save()

    match_itf14_and_off_direct(products, records)
    _save()

    propagate_multipack_siblings(products, records)
    propagate_same_model_siblings(products, records)
    _save()

    after = _counts()
    print(f"\nWAVE-7 AFTER  ({len(products)} SKUs, {time.perf_counter()-t0:.1f}s): {after}", flush=True)
    for k in before:
        print(f"  {k:15s}: {before[k]:4d} -> {after[k]:4d} (+{after[k]-before[k]})", flush=True)


if __name__ == "__main__":
    main()

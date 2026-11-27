"""Wave-8 Final Cleanup & Precision Mop-Up Crawler.

Executes the final exhaustive sweep across remaining high-yield public sources while
enforcing strict data hygiene:
  Stage 0: Remove false-positive global-menu `Nhật Bản` origins from Chiaki records
  Stage 1: Emart Vietnam remaining 1,326 brand-matched URLs (captures Hoa Doanh,
           Zai Zai, fresh fruits, Garnier, Dove, President, Kitkool, LC Food)
  Stage 2: Stationery & Kitchenware Mop-Up:
           - VPP Hoang Lam (`vanphongphamhl.vn/sitemap_product.xml`)
           - VPP Son Ca (`vppsonca.vn`) & VPP247 (`vanphongpham247.vn`) with `TL/FO` aliases
           - KidsPlaza (`Inochi`, `Pocky`) & Chiaki (`HAP813`, `Akiko`, `Garnier`, `Dove`, `Inochi`)
  Stage 3: USDA FoodData Central remaining whole single-ingredient foods (polite 429 retry)
  Stage 4: Decimal-normalized pack-size & model sibling alignment
"""

from __future__ import annotations

import concurrent.futures
import hashlib
import json
import re
import sys
import time
import urllib.error
import urllib.parse
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
    _strip_html,
)
from scripts.enrich_detailed_ingredients import (  # noqa: E402
    extract_form,
    extract_variants,
    ingredient_specificity_score,
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
    load_sitemap_cached,
    parse_vn_ssr_page,
)
from scripts.enrich_wave4_final_mopup import (  # noqa: E402
    _V4_BRAND_SYNONYMS,
    _brand_aliases_v4,
    _tokens_v4,
    crawl_site_v4,
    propagate_same_model_siblings,
)
from scripts.enrich_wave5_ultra import (  # noqa: E402
    _compact_ascii,
    parse_emart_product_page,
)
from scripts.enrich_wave6_comprehensive import (  # noqa: E402
    _USDA_SINGLE_FOOD_QUERIES,
    _harmonize_compound_vi,
    strict_verify_v6,
)
from scripts.enrich_wave7_100pct import barcode_candidates  # noqa: E402

# Extend brand aliases in-place cleanly for Stationery / Snacks / Kitchenware
_V4_BRAND_SYNONYMS.update(
    {
        "thiên long": {"thienlong", "thien", "long", "flexoffice", "tl", "fo"},
        "thien long": {"thienlong", "thien", "long", "flexoffice", "tl", "fo"},
        "oishi": {"oishi", "akiko", "pinattsu", "liwayway"},
        "akiko": {"akiko", "oishi"},
        "pino": {"pino", "oishi"},
        "pocky": {"pocky", "glico"},
        "tao kae noi": {"taokaenoi", "taokeanoi", "bigroll"},
        "dorkbua": {"doakbua", "dorkbua"},
        "doakbua": {"doakbua", "dorkbua"},
        "zai zai": {"zaizai", "zai", "duchanh"},
        "tipo": {"tipo", "huunghi"},
        "hữu nghị": {"huunghi", "tipo"},
    }
)


def parse_chiaki_clean(url: str) -> dict[str, Any]:
    """Parse Chiaki body safely, never pulling global sidebar dropdown `Xuất xứ: Nhật Bản`."""
    res = parse_vn_ssr_page(url)
    if res.get("_miss"):
        return res
    # Clear unverified sidebar origin unless verified explicitly inside product description table
    res["origin"] = ""
    return res


def parse_vppsonca_or_vpp_page(url: str) -> dict[str, Any]:
    """Parse VPP Son Ca / VPP Hoang Lam / VPP247 including body text paragraphs (`bút được sản xuất tại...`)."""
    res = parse_vn_ssr_page(url)
    if res.get("_miss"):
        return res
    if not res.get("ingredients"):
        from scripts.enrich_wave3_exhaustive import fetch_fast
        raw = fetch_fast(url, timeout=12.0) or ""
        plain = re.sub(r"\s+", " ", _strip_html(raw))
        m_mat = re.search(
            r"(?:thân bút|vỏ bút|chất liệu(?:\s+bút)?|được làm)\s+(?:làm\s+)?(?:bằng|từ)\s+([^.\n]{8,140})",
            plain,
            re.I,
        )
        if m_mat:
            res["ingredients"] = m_mat.group(1).strip()
        if not res.get("storage"):
            m_sto = re.search(r"((?:nhiệt độ|bảo quản)\s*[:：]?\s*\d+\s*[~-]\s*\d+\s*°?\s*c[^.\n]{0,80})", plain, re.I)
            if m_sto:
                res["storage"] = m_sto.group(1).strip()
        if not res.get("origin"):
            m_ori = re.search(r"sản xuất tại\s+(Việt Nam|Trung Quốc|Nhật Bản|Thái Lan|Hàn Quốc|Đức)", plain, re.I)
            if m_ori:
                res["origin"] = m_ori.group(1).strip()
    return res


def stage0_purge_false_chiaki_origins(records: dict[str, dict[str, Any]]) -> None:
    print("\n=== STAGE 0: Purge False Global-Menu Chiaki `Nhật Bản` Origins ===", flush=True)
    purged = 0
    for rec in records.values():
        if "Chiaki" in str(rec.get("origin_source") or ""):
            for k in ("origin", "origin_source", "origin_source_url"):
                rec.pop(k, None)
            purged += 1
    print(f"  Purged false Chiaki origins: {purged}", flush=True)


def stage1_emart_remaining_brands(
    products: list[dict[str, Any]],
    records: dict[str, dict[str, Any]],
) -> None:
    print("\n=== STAGE 1: Emart Remaining 1,326 Missing-Brand URLs ===", flush=True)
    em_idx: dict[str, str] = json.loads((CACHE_DIR / "emart_index.json").read_text(encoding="utf-8"))
    cache_jsonl = CACHE_DIR / "emart_pages.jsonl"
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

    miss_prods = [p for p in products if _needs_enrichment(records.get(str(p["product_id"])))]
    bkeys = {
        _compact_ascii(p.get("brand"))
        for p in miss_prods
        if len(_compact_ascii(p.get("brand"))) >= 3
    } - {"nobrand", "wineco", "thanhlamhome", "thanhlammart"}
    bkeys |= {
        "thienlong", "tl0", "wb0", "locknlock", "ottogi", "ofood", "thtrue",
        "zaizai", "hoadoanh", "kitkool", "lcfood", "doakbua", "honglam",
        "dove", "garnier", "glade", "cozy", "oishi", "akiko", "pocky",
        "president", "tipo", "trangan", "belgi", "taokaenoi", "kiwi", "envy",
        "inochi", "omo", "vim", "chinsu", "minhviet",
    }

    todo = [
        f"https://emartmall.com.vn/index.php?route=product/product&product_id={pid}"
        for pid, title in em_idx.items()
        if f"https://emartmall.com.vn/index.php?route=product/product&product_id={pid}" not in pages
        and any(bk in _compact_ascii(title) for bk in bkeys)
    ]
    print(f"  Emart unfetched brand-matched URLs: {len(todo)} (cached={len(pages)})", flush=True)

    if todo:
        t0 = time.perf_counter()
        with cache_jsonl.open("a", encoding="utf-8") as cf:
            for start in range(0, len(todo), 160):
                chunk = todo[start : start + 160]
                with concurrent.futures.ThreadPoolExecutor(max_workers=20) as ex:
                    for res in ex.map(parse_emart_product_page, chunk):
                        pages[res["url"]] = res
                        cf.write(json.dumps(res, ensure_ascii=False) + "\n")
                cf.flush()
                done_n = min(start + 160, len(todo))
                print(f"    Emart [{done_n}/{len(todo)}] elapsed={time.perf_counter()-t0:.0f}s", flush=True)

    by_bc: dict[str, dict[str, Any]] = {}
    em_list: list[tuple[dict[str, Any], set[str]]] = []
    tok_idx: dict[str, list[int]] = {}
    for pg in pages.values():
        if pg.get("_miss") or not pg.get("name"):
            continue
        skus: set[str] = set()
        if pg.get("barcode"):
            skus.add(str(pg["barcode"]).strip())
        for s in pg.get("skus") or []:
            skus.add(str(s).strip())
        for s in skus:
            by_bc[s] = pg
        ptoks = _tokens_v4(_harmonize_compound_vi(pg["name"]))
        idx = len(em_list)
        em_list.append((pg, skus))
        for t in ptoks:
            tok_idx.setdefault(t, []).append(idx)

    stats = _blank_stats()
    for p in products:
        pid = str(p["product_id"])
        if not _needs_enrichment(records.get(pid)):
            continue
        hit_bc = False
        for bc in barcode_candidates(p):
            if bc in by_bc and apply_fields_v3(records, p, by_bc[bc], "thông tin nhãn sản phẩm (Emart Vietnam)", "EMART_BARCODE_W8", stats):
                stats["barcode_hits"] += 1
                hit_bc = True
                break
        if hit_bc:
            continue
        qtoks = _tokens_v4(_harmonize_compound_vi(p.get("name")), p.get("brand")) | _brand_aliases_v4(p.get("brand"))
        counts: dict[int, int] = {}
        for t in qtoks:
            for i in tok_idx.get(t, ()):
                counts[i] = counts.get(i, 0) + 1
        for _ov, i in sorted(((ov, idx) for idx, ov in counts.items() if ov >= 2), reverse=True)[:5]:
            pg, skus = em_list[i]
            m = strict_verify_v6(p, pg["name"], skus, pg.get("brand", ""), allow_nobrand_exact=True)
            if m and apply_fields_v3(records, p, pg, "thông tin nhãn sản phẩm (Emart Vietnam)", f"EMART_{m}_W8", stats):
                stats["title_hits"] += 1
                break
    print(f"  Emart W8 stats: {stats}", flush=True)


def stage2_stationery_and_kitchen_mopup(
    products: list[dict[str, Any]],
    records: dict[str, dict[str, Any]],
) -> None:
    print("\n=== STAGE 2: Stationery & Kitchenware Mop-Up (Hoàng Lâm, Sơn Ca, VPP247, KidsPlaza, Chiaki) ===", flush=True)
    # 2A: VPP Hoang Lam
    hl_urls = load_sitemap_cached(
        "vpphl_urls.json",
        "https://vanphongphamhl.vn/sitemap_product.xml",
        url_pred=lambda u: len(u) > 35,
    )
    if hl_urls:
        crawl_site_v4(
            site_tag="vpphl",
            src_label="thông tin sản phẩm (VPP Hoàng Lâm)",
            urls=hl_urls,
            slug_fn=lambda u: u.rstrip("/").split("/")[-1],
            parser_fn=parse_vppsonca_or_vpp_page,
            products=products,
            records=records,
            max_new_fetch=120,
            workers=16,
        )

    # 2B: VPP Son Ca + VPP247 with TL/FO aliases
    for tag, fn, label in (
        ("vppsonca", "vppsonca_urls.json", "thông tin sản phẩm (VPP Sơn Ca)"),
        ("vpp247", "vpp247_urls.json", "thông tin sản phẩm (VPP247)"),
    ):
        u_p = CACHE_DIR / fn
        if u_p.exists():
            crawl_site_v4(
                site_tag=tag,
                src_label=label,
                urls=json.loads(u_p.read_text(encoding="utf-8")),
                slug_fn=lambda u: u.rstrip("/").split("/")[-1],
                parser_fn=parse_vppsonca_or_vpp_page,
                products=products,
                records=records,
                max_new_fetch=120,
                workers=16,
            )

    # 2C: KidsPlaza (Inochi / Pocky Glico) & Chiaki (HAP813 / Akiko / Inochi / Garnier / Dove — NO fake origin!)
    kp_p = CACHE_DIR / "kidsplaza_urls.json"
    if kp_p.exists():
        crawl_site_v4(
            site_tag="kidsplaza",
            src_label="thông tin sản phẩm (KidsPlaza)",
            urls=json.loads(kp_p.read_text(encoding="utf-8")),
            slug_fn=lambda u: u.rstrip("/").split("/")[-1].replace(".html", ""),
            parser_fn=parse_vn_ssr_page,
            products=products,
            records=records,
            max_new_fetch=150,
            workers=18,
        )

    ch_p = CACHE_DIR / "chiaki_urls.json"
    if ch_p.exists():
        crawl_site_v4(
            site_tag="chiaki",
            src_label="thông tin sản phẩm (Chiaki)",
            urls=json.loads(ch_p.read_text(encoding="utf-8")),
            slug_fn=lambda u: u.rstrip("/").split("/")[-1],
            parser_fn=parse_chiaki_clean,
            products=products,
            records=records,
            max_new_fetch=550,
            workers=22,
        )


def stage3_usda_remaining_nutrition(
    products: list[dict[str, Any]],
    records: dict[str, dict[str, Any]],
) -> None:
    print("\n=== STAGE 3: USDA FoodData Central Nutrition Completion (Polite Rate-Limited) ===", flush=True)
    usda_cache_p = CACHE_DIR / "usda_fdc_cache.json"
    usda_cache: dict[str, dict[str, Any] | None] = json.loads(usda_cache_p.read_text(encoding="utf-8")) if usda_cache_p.exists() else {}

    allowed_cats = {
        "Trái cây tươi", "Củ, Quả", "Rau Lá", "Thịt", "Hải Sản", "Hải Sản Đông Lạnh",
        "Trứng", "Gạo - Nông Sản Khô", "Hạt - Trái Cây Sấy Khô", "Gia Vị Khác",
    }
    matched_items: list[tuple[dict[str, Any], str]] = []
    needed_queries: set[str] = set()
    for p in products:
        if (p.get("category_name") or "") not in allowed_cats:
            continue
        if records.get(str(p["product_id"]), {}).get("nutrition"):
            continue
        pname = p.get("name") or ""
        for pat, q in _USDA_SINGLE_FOOD_QUERIES:
            if pat.search(pname):
                needed_queries.add(q)
                matched_items.append((p, q))
                break

    todo_q = [q for q in sorted(needed_queries) if q not in usda_cache]
    print(f"  USDA cached={len(usda_cache)} remaining_queries={len(todo_q)}", flush=True)
    for q in todo_q[:28]:  # Stay cleanly inside DEMO_KEY 30/hr rate limit
        url = (
            "https://api.nal.usda.gov/fdc/v1/foods/search?"
            + urllib.parse.urlencode({"query": q, "dataType": "Foundation,SR Legacy", "pageSize": "1", "api_key": "DEMO_KEY"})
        )
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "RetailAIKiosk/1.0"})
            with urllib.request.urlopen(req, timeout=12) as r:
                d = json.loads(r.read().decode("utf-8", errors="replace"))
                foods = d.get("foods") or []
                if foods:
                    f0 = foods[0]
                    nutr: dict[str, Any] = {"basis": "100g"}
                    for n in f0.get("foodNutrients") or []:
                        nm = n.get("nutrientName") or ""
                        val = n.get("value")
                        unit = str(n.get("unitName") or "").upper()
                        if not isinstance(val, (int, float)) or isinstance(val, bool) or val < 0:
                            continue
                        if nm == "Energy":
                            nutr["energy_kcal"] = round(float(val) / 4.184 if unit == "KJ" else float(val), 1)
                        elif nm == "Total Sugars":
                            nutr["sugar_g"] = round(float(val), 2)
                        elif nm == "Carbohydrate, by difference":
                            nutr["carbs_g"] = round(float(val), 2)
                        elif nm == "Protein":
                            nutr["protein_g"] = round(float(val), 2)
                        elif nm == "Total lipid (fat)":
                            nutr["fat_g"] = round(float(val), 2)
                        elif nm == "Fiber, total dietary":
                            nutr["fiber_g"] = round(float(val), 2)
                        elif nm == "Sodium, Na":
                            nutr["sodium_mg"] = round(float(val), 1)
                    if len(nutr) >= 3:
                        usda_cache[q] = {"fdc_id": f0.get("fdcId"), "nutrition": nutr}
                    else:
                        usda_cache[q] = None
                else:
                    usda_cache[q] = None
        except urllib.error.HTTPError as he:
            if he.code == 429:
                print("  USDA DEMO_KEY rate limit reached cleanly; saving progress.", flush=True)
                break
        except Exception:
            pass
        time.sleep(0.5)
    usda_cache_p.write_text(json.dumps(usda_cache, ensure_ascii=False), encoding="utf-8")

    stats = _blank_stats()
    for p, q in matched_items:
        info = usda_cache.get(q)
        if not info:
            continue
        ext = {
            "url": f"https://fdc.nal.usda.gov/fdc-app.html#/food-details/{info['fdc_id']}/nutrients",
            "nutrition": info["nutrition"],
        }
        apply_fields_v3(records, p, ext, "USDA FoodData Central", "USDA_SINGLE_WHOLE_FOOD", stats)
    print(f"  USDA W8 nutrition stats: {stats}", flush=True)


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
    print(f"WAVE-8 BEFORE ({len(products)} SKUs): {before}", flush=True)

    stage0_purge_false_chiaki_origins(records)
    _save()

    stage1_emart_remaining_brands(products, records)
    _save()

    stage2_stationery_and_kitchen_mopup(products, records)
    # Also ensure cached Chiaki entries don't re-introduce sidebar `Nhật Bản` origin
    stage0_purge_false_chiaki_origins(records)
    _save()

    stage3_usda_remaining_nutrition(products, records)
    _save()

    propagate_multipack_siblings(products, records)
    propagate_same_model_siblings(products, records)
    _save()

    after = _counts()
    print(f"\nWAVE-8 AFTER  ({len(products)} SKUs, {time.perf_counter()-t0:.1f}s): {after}", flush=True)
    for k in before:
        print(f"  {k:15s}: {before[k]:4d} -> {after[k]:4d} ({after[k]-before[k]:+d})", flush=True)


if __name__ == "__main__":
    main()

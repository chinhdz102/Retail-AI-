"""Wave-5 Ultra Multi-Source Exhaustive Crawler.

Closes remaining coverage gaps across ALL product attributes (`ingredients`, `usage`,
`storage`, `origin`, `nutrition`) from 100% public HTTP 200 sources:
  Stage 1: OpenFoodFacts Vietnam full catalog (1,735 VN items via paginated search)
           + Direct `/api/v2/product/{bc}.json` for all failed/uncached EAN barcodes
           + OpenBeautyFacts direct `/api/v2/product/{bc}.json` retry
  Stage 2: Đôi Đũa Vàng WP REST API (94 SKUs) & DH Foods deduplicated sitemap
  Stage 3: Co.op Online Compact-Substring + Abbreviated-Slug Deep Sweep
           (unlocks truncated slugs like `b-quepocky-glico...`, `2bcdr-oralclean...`)
  Stage 4: Kingfoodmart Compact-Substring Deep Sweep (~1,800+ remaining candidates)
  Stage 5: Emart Vietnam (`emartmall.com.vn`) Category Indexing + Targeted Brand Search
           (extracts barcode, `Xuất xứ`, `Thành phần`, `Hướng dẫn sử dụng`, `Bảo quản`)
  Stage 6: Same-formula / Same-brand-model sibling alignment
"""

from __future__ import annotations

import concurrent.futures
import hashlib
import html
import json
import re
import sys
import time
import unicodedata
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
    OFF_HEADERS,
    WEB_HEADERS,
    _extract_size_sigs,
    _slug_match_tokens,
    _strip_html,
    fetch_cooponline_page,
    fetch_kf_fast,
    load_cooponline_urls,
)
from scripts.enrich_detailed_ingredients import (  # noqa: E402
    ingredient_specificity_score,
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
    fetch_fast,
    fetch_urllib_gz,
    load_sitemap_cached,
    parse_vn_ssr_page,
)
from scripts.enrich_wave4_final_mopup import (  # noqa: E402
    _brand_aliases_v4,
    _expand_abbrevs,
    _extract_models_v4,
    _tokens_v4,
    propagate_same_model_siblings,
    strict_verify_v4,
)

# Short/custom tokens to match compact slugs on Co.op Online & Kingfoodmart
_EXTRA_COMPACT_BRAND_KEYS = {
    "ottogi", "ofood", "thtrue", "thmilk", "locknlock", "dokbua", "thaibifood",
    "franzzi", "chinsu", "namngu", "omachi", "kokomi", "heocaoboi", "lavie",
    "coca", "pepsi", "milo", "nescafe", "trungnguyen", "lifebuoy", "romano",
    "gervenne", "enchanteur", "clearmen", "tresemme", "sunlight", "vissan",
    "ducviet", "cautre", "bibigo", "safoco", "acecook", "cungdinh", "haribo",
    "doritos", "pocky", "toppo", "chocopie", "kitkat", "president", "anchor",
    "belcube", "kewpie", "leekumkee", "barona", "dhfoods", "glade", "paseo",
    "emos", "tempo", "energizer", "aquafina", "redbull", "nutifood", "growplus",
    "pediasure", "glucerna", "huggies", "vaseline", "lactacyd", "closeup",
    "tohogenkai", "dongwon", "tuongan", "simply", "fami", "susu", "vinamilk",
    "dalatmilk", "meiji", "bauli", "orio", "oreo", "cosy", "bento", "pinattsu",
    "poca", "oishi", "toonies", "oralclean", "lipzo", "inochi", "deli",
    "thienlong", "hongha", "onggiaika", "doiduavang", "hoadoanh", "minhviet",
    "honglam", "vinafood", "baominh", "vinatea", "phuclong", "cozy",
    "scotchbrite", "ambipur", "maxkleen", "suzy", "comfort", "omo", "vim",
}


def _compact_ascii(s: str | None) -> str:
    if not s:
        return ""
    d = unicodedata.normalize("NFD", str(s).lower().replace("đ", "d"))
    a = "".join(c for c in d if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9]", "", a)


def _extract_off_nutriments(prod: dict[str, Any], pname: str) -> dict[str, Any] | None:
    nutr_raw = prod.get("nutriments") or {}
    if not isinstance(nutr_raw, dict):
        return None
    nutr: dict[str, Any] = {}

    def _num(k: str) -> float | None:
        v = nutr_raw.get(k)
        return round(float(v), 2) if isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0 else None

    kcal = _num("energy-kcal_100g")
    if kcal is None:
        kj = _num("energy-kj_100g") or _num("energy_100g")
        if kj and kj > 0:
            kcal = round(kj / 4.184, 1)
    for key, fk, mx in (
        ("energy_kcal", None, 1000),
        ("sugar_g", "sugars_100g", 100),
        ("carbs_g", "carbohydrates_100g", 100),
        ("protein_g", "proteins_100g", 100),
        ("fat_g", "fat_100g", 100),
        ("fiber_g", "fiber_100g", 100),
    ):
        val = kcal if key == "energy_kcal" else _num(fk)  # type: ignore[arg-type]
        if val is not None and val <= mx:
            nutr[key] = val
    sod = _num("sodium_100g")
    salt = _num("salt_100g")
    if sod is not None and sod <= 40:
        nutr["sodium_mg"] = round(sod * 1000.0, 1)
    elif salt is not None and salt <= 100:
        nutr["salt_g"] = salt
    if not nutr:
        return None
    per = str(prod.get("nutrition_data_per") or "100g")
    is_liq = "ml" in per.lower() or any(w in pname.lower() for w in ("ml", "lít", "lit", "nước", "sữa tươi", "trà ", "bia "))
    nutr["basis"] = "100ml" if is_liq else "100g"
    return nutr


def _apply_off_product(
    records: dict[str, dict[str, Any]],
    p: dict[str, Any],
    prod: dict[str, Any],
    host: str,
    src_label: str,
    match_method: str,
    stats: dict[str, int],
) -> bool:
    code = str(prod.get("code") or p.get("barcode") or "").strip()
    nutr = _extract_off_nutriments(prod, str(p.get("name") or ""))
    ing = str(prod.get("ingredients_text_vi") or "").strip() or str(prod.get("ingredients_text") or "").strip()
    ing = re.sub(r"\s+", " ", ing).strip()[:550]
    orig = str(prod.get("origins") or "").strip()
    if orig.lower() in ("vietnam", "việt nam", "en:vietnam"):
        orig = "Việt Nam"
    elif orig.startswith("en:"):
        orig = orig[3:].replace("-", " ").title()
    ext = {
        "url": f"https://{host}/product/{code}",
        "ingredients": ing,
        "origin": orig,
        "nutrition": nutr,
    }
    return apply_fields_v3(records, p, ext, src_label, match_method, stats)


# ---------------------------------------------------------------------------
# Stage 1: OpenFoodFacts VN full catalog + Direct /api/v2/product/{bc}.json
# ---------------------------------------------------------------------------
def stage1_openfoodfacts_direct(
    products: list[dict[str, Any]],
    records: dict[str, dict[str, Any]],
) -> None:
    print("\n=== STAGE 1: OpenFoodFacts VN Full Catalog + Direct Product JSON Sweep ===", flush=True)
    fields = "code,product_name,brands,ingredients_text_vi,ingredients_text,nutriments,nutrition_data_per,origins"

    # 1A: Pull ALL 1,735 Vietnam products via paginated search
    vn_cache_path = CACHE_DIR / "off_vn_all.json"
    vn_prods: list[dict[str, Any]] = []
    if vn_cache_path.exists():
        try:
            vn_prods = json.loads(vn_cache_path.read_text(encoding="utf-8"))
        except Exception:
            vn_prods = []
    if len(vn_prods) < 1200:
        vn_prods = []
        for page in range(1, 22):
            url = f"https://world.openfoodfacts.org/api/v2/search?countries_tags_en=vietnam&page_size=100&page={page}&fields={fields}"
            got = False
            for attempt in range(4):
                try:
                    req = urllib.request.Request(url, headers=OFF_HEADERS)
                    with urllib.request.urlopen(req, timeout=22) as r:
                        if r.status == 200:
                            d = json.loads(r.read().decode("utf-8", errors="replace"))
                            arr = d.get("products") or []
                            vn_prods.extend(arr)
                            got = True
                            if len(arr) < 100:
                                break
                            break
                except Exception:
                    time.sleep(1.5 * (attempt + 1))
            if got and len(vn_prods) < page * 100:
                break
            time.sleep(0.4)
        if vn_prods:
            vn_cache_path.write_text(json.dumps(vn_prods, ensure_ascii=False), encoding="utf-8")
    print(f"  Loaded {len(vn_prods)} Vietnam OFF products", flush=True)

    # Index VN OFF products by barcode + tokens for both exact barcode & strict title match!
    vn_by_bc: dict[str, dict[str, Any]] = {}
    vn_tok_idx: dict[str, list[int]] = {}
    vn_list: list[tuple[dict[str, Any], set[str]]] = []
    for prod in vn_prods:
        c = str(prod.get("code") or "").strip()
        if c:
            vn_by_bc[c] = prod
        nm = str(prod.get("product_name") or "").strip()
        if not nm:
            continue
        br = str(prod.get("brands") or "").strip()
        toks = _tokens_v4(f"{nm} {br}")
        idx = len(vn_list)
        vn_list.append((prod, toks))
        for t in toks:
            vn_tok_idx.setdefault(t, []).append(idx)

    vn_stats = _blank_stats()
    for p in products:
        pid = str(p["product_id"])
        rec = records.get(pid, {})
        if rec.get("nutrition") and rec.get("ingredients"):
            continue
        bc = str(p.get("barcode") or "").strip()
        if bc and bc in vn_by_bc:
            if _apply_off_product(records, p, vn_by_bc[bc], "world.openfoodfacts.org", "OpenFoodFacts", "OFF_VN_BARCODE", vn_stats):
                vn_stats["barcode_hits"] += 1
                continue
        # Strict title/brand/variant fallback on VN catalog
        if not _brand_aliases_v4(p.get("brand")):
            continue
        qtoks = _tokens_v4(p.get("name"), p.get("brand"))
        counts: dict[int, int] = {}
        for t in qtoks:
            for i in vn_tok_idx.get(t, ()):
                counts[i] = counts.get(i, 0) + 1
        for _ov, i in sorted(((ov, idx) for idx, ov in counts.items() if ov >= 2), reverse=True)[:4]:
            op, _ = vn_list[i]
            m = strict_verify_v4(p, str(op.get("product_name") or ""), {str(op.get("code") or "")}, op.get("brands", ""))
            if m:
                if _apply_off_product(records, p, op, "world.openfoodfacts.org", "OpenFoodFacts", f"OFF_VN_{m}", vn_stats):
                    vn_stats["title_hits"] += 1
                    break
    print(f"  OFF VN catalog match stats: {vn_stats}", flush=True)

    # 1B: Retry ALL failed/uncached barcodes directly via `/api/v2/product/{bc}.json`
    off_batch_cache_p = CACHE_DIR / "off_v2_cache.json"
    off_batch_cache: dict[str, dict[str, Any] | None] = {}
    if off_batch_cache_p.exists():
        try:
            off_batch_cache = json.loads(off_batch_cache_p.read_text(encoding="utf-8"))
        except Exception:
            pass

    direct_cache_p = CACHE_DIR / "off_direct_v2_cache.json"
    direct_cache: dict[str, dict[str, Any] | None] = {}
    if direct_cache_p.exists():
        try:
            direct_cache = json.loads(direct_cache_p.read_text(encoding="utf-8"))
        except Exception:
            pass

    nonfood_cats = {"Văn Phòng Phẩm - Đồ Chơi", "Đồ dùng nhà bếp", "Đồ Dùng Phòng Ngủ", "Đồ Điện Gia Dụng", "Vệ Sinh Nhà Cửa", "Chăm Sóc Da", "Chăm Sóc Tóc", "Chăm Sóc Răng Miệng", "Chăm Sóc Cá Nhân Khác", "Chăm Sóc Cá Nhân Cho Bé", "Nước Tẩy Rửa", "Nước Lau Sàn - Lau Kính", "Nước Giặt", "Khăn Giấy - Khăn Ướt", "Tã - Bỉm", "Bình Xịt Côn Trùng", "Chăm Sóc Phụ Nữ", "Sữa Tắm - Gội Cho Bé", "Thiết bị dùng điện trong nhà", "Đồ dùng trong nhà"}
    by_bc: dict[str, list[dict[str, Any]]] = {}
    for p in products:
        bc = str(p.get("barcode") or "").strip()
        if not re.fullmatch(r"\d{8,14}", bc):
            continue
        # Skip internal store scale/PLU codes (20..., 26...)
        if len(bc) == 13 and bc.startswith(("20", "21", "22", "23", "24", "25", "26", "27", "28", "29")):
            continue
        if (p.get("category_name") or "") in nonfood_cats:
            continue
        by_bc.setdefault(bc, []).append(p)

    # Query barcodes that were missed/failed in batch search (not in off_batch_cache or failed 503)
    todo_direct = sorted(
        bc for bc, plist in by_bc.items()
        if bc not in direct_cache
        and bc not in off_batch_cache
        and bc not in vn_by_bc
        and any(not records.get(str(p["product_id"]), {}).get("nutrition") or not records.get(str(p["product_id"]), {}).get("ingredients") for p in plist)
    )
    print(f"  Direct OFF barcode lookup needed: {len(todo_direct)} (cached_direct={len(direct_cache)})", flush=True)

    def _fetch_one_off(bc: str) -> tuple[str, dict[str, Any] | None]:
        url = f"https://world.openfoodfacts.org/api/v2/product/{bc}.json?fields={fields}"
        for attempt in range(3):
            try:
                req = urllib.request.Request(url, headers=OFF_HEADERS)
                with urllib.request.urlopen(req, timeout=14) as r:
                    d = json.loads(r.read().decode("utf-8", errors="replace"))
                    if d.get("status") == 1 and isinstance(d.get("product"), dict):
                        return bc, d["product"]
                    return bc, None
            except urllib.error.HTTPError as he:
                if he.code == 404:
                    return bc, None
                time.sleep(0.9 * (attempt + 1))
            except Exception:
                time.sleep(0.7 * (attempt + 1))
        return bc, None

    if todo_direct:
        t0 = time.perf_counter()
        for start in range(0, len(todo_direct), 120):
            chunk = todo_direct[start : start + 120]
            with concurrent.futures.ThreadPoolExecutor(max_workers=10) as ex:
                for bc, prod in ex.map(_fetch_one_off, chunk):
                    direct_cache[bc] = prod
            direct_cache_p.write_text(json.dumps(direct_cache, ensure_ascii=False), encoding="utf-8")
            done_n = min(start + 120, len(todo_direct))
            hits_n = sum(1 for k in chunk if direct_cache.get(k))
            if len(todo_direct) > 120 or done_n == len(todo_direct):
                print(f"    OFF direct [{done_n}/{len(todo_direct)}] chunk_hits={hits_n} elapsed={time.perf_counter()-t0:.0f}s", flush=True)

    dir_stats = _blank_stats()
    for bc, plist in by_bc.items():
        prod = direct_cache.get(bc) or off_batch_cache.get(bc)
        if not prod:
            continue
        for p in plist:
            if _apply_off_product(records, p, prod, "world.openfoodfacts.org", "OpenFoodFacts", "OFF_DIRECT_BARCODE", dir_stats):
                dir_stats["barcode_hits"] += 1
    print(f"  OFF Direct Barcode stats: {dir_stats}", flush=True)


# ---------------------------------------------------------------------------
# Stage 2: Đôi Đũa Vàng WP REST API + DH Foods Deduplicated Sitemap
# ---------------------------------------------------------------------------
def stage2_doiduavang_and_dhfoods(
    products: list[dict[str, Any]],
    records: dict[str, dict[str, Any]],
) -> None:
    print("\n=== STAGE 2: Đôi Đũa Vàng WP API + DH Foods ===", flush=True)
    # 2A: Doiduavang WP REST API
    ddv_cache = CACHE_DIR / "doiduavang_wp.json"
    if ddv_cache.exists():
        ddv_items = json.loads(ddv_cache.read_text(encoding="utf-8"))
    else:
        ddv_items = []
        raw = fetch_urllib_gz("https://doiduavang.vn/wp-json/wp/v2/product?per_page=100&page=1", timeout=18)
        if raw:
            ddv_items = json.loads(raw)
            ddv_cache.write_text(json.dumps(ddv_items, ensure_ascii=False), encoding="utf-8")
    ddv_pages: list[dict[str, Any]] = []
    from scripts.enrich_wave3_exhaustive import _extract_sections_from_clean_html
    for it in ddv_items:
        title = html.unescape(str(it.get("title", {}).get("rendered") or "")).strip()
        content = str(it.get("content", {}).get("rendered") or "")
        secs = _extract_sections_from_clean_html(content, strip_chrome=False)
        ddv_pages.append({"url": it.get("link") or "", "name": title, "brand": "Đôi Đũa Vàng", **secs})
    stats_ddv = _blank_stats()
    for p in products:
        if "đũa" not in norm_text(p.get("brand")) and "dua vang" not in norm_text(p.get("name")):
            continue
        for pg in ddv_pages:
            if strict_verify_v4(p, pg["name"], set(), pg["brand"]):
                if apply_fields_v3(records, p, pg, "thông tin sản phẩm (Đôi Đũa Vàng)", "DDV_WP", stats_ddv):
                    stats_ddv["title_hits"] += 1
                    break
    print(f"  Đôi Đũa Vàng stats ({len(ddv_pages)} products): {stats_ddv}", flush=True)

    # 2B: DH Foods deduplicated sitemap
    dh_urls = load_sitemap_cached(
        "dhfoods_urls.json",
        "https://dhfoods.com.vn/sitemap.xml",
        url_pred=lambda u: any(k in u for k in ("muoi-", "sat-", "sot-", "gia-vi", "bot-", "tieu-", "ot-")) and u.count("/") == 4,
    )
    if dh_urls:
        from scripts.enrich_wave4_final_mopup import crawl_site_v4
        crawl_site_v4(
            site_tag="dhfoods",
            src_label="thông tin sản phẩm (DH Foods)",
            urls=dh_urls,
            slug_fn=lambda u: u.rstrip("/").split("/")[-1],
            parser_fn=parse_vn_ssr_page,
            products=products,
            records=records,
            max_new_fetch=120,
            workers=14,
        )


# ---------------------------------------------------------------------------
# Stage 3 & 4: Co.op Online & Kingfoodmart Compact-Substring Deep Sweep
# ---------------------------------------------------------------------------
def stage_compact_substring_sweep(
    *,
    site_tag: str,
    src_label: str,
    all_urls: list[str],
    slug_fn,
    parser_fn,
    products: list[dict[str, Any]],
    records: dict[str, dict[str, Any]],
    max_fetch: int,
    workers: int,
) -> None:
    print(f"\n=== STAGE: {site_tag.upper()} Compact-Substring Deep Sweep ({len(all_urls):,} URLs, cap={max_fetch}) ===", flush=True)
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

    miss_prods = [p for p in products if _needs_enrichment(records.get(str(p["product_id"])))]
    miss_ing_prods = [p for p in products if not records.get(str(p["product_id"]), {}).get("ingredients")]

    # Build compact brand keys + distinctive 5+ char product root tokens from STILL-MISSING items
    brand_keys = set(_EXTRA_COMPACT_BRAND_KEYS)
    for p in miss_prods:
        cb = _compact_ascii(p.get("brand"))
        if len(cb) >= 4 and cb not in ("nobrand", "khac", "wineco", "thanhlam", "thanhlamhome", "thanhlammart", "meatdeli"):
            brand_keys.add(cb)
        for al in _brand_aliases_v4(p.get("brand")):
            if len(al) >= 4:
                brand_keys.add(al)

    # Distinctive root tokens from missing products' names (e.g. "hanhnhan", "huongduong", "thachduong", "chocopie", "tl08", "hap731")
    miss_signatures: list[tuple[dict[str, Any], set[str], set[str], set[str]]] = []
    for p in miss_prods:
        b_al = {x for x in _brand_aliases_v4(p.get("brand")) if len(x) >= 3}
        toks = {t for t in _slug_match_tokens(_expand_abbrevs(p.get("name"))) if len(t) >= 3}
        models = _extract_models_v4(p.get("name"))
        miss_signatures.append((p, b_al, toks, models))

    # Score EVERY unfetched URL against ALL still-missing SKUs
    scored_unfetched: list[tuple[float, str]] = []
    for u in all_urls:
        if u in pages:
            continue
        slug_raw = slug_fn(u)
        comp = _compact_ascii(slug_raw)
        nslug = norm_text(slug_raw.replace("-", " "))
        utoks = set(_slug_match_tokens(nslug))
        umodels = _extract_models_v4(slug_raw)

        best_sc = 0.0
        # Fast filter: does URL match any missing brand substring OR exact model code OR >= 2 tokens?
        has_bk = any(bk in comp for bk in brand_keys)
        if not has_bk:
            # Check even against generic noun tokens if >= 2 tokens overlap with a missing SKU!
            for _p, b_al, toks, models in miss_signatures:
                if models and (models & umodels):
                    best_sc = max(best_sc, 3.0)
                    break
                ov = len(toks & utoks)
                if ov >= 3 or (ov >= 2 and len(toks) <= 4):
                    rec = ov / max(1, len(toks))
                    if rec >= 0.45:
                        best_sc = max(best_sc, 1.0 + rec)
        else:
            # Matches a known missing brand! Score by best matching missing SKU's token overlap in `comp`
            best_sc = 0.8  # Base score just for belonging to a missing brand!
            for p, b_al, toks, models in miss_signatures:
                if b_al and not any(ba in comp for ba in b_al):
                    continue
                if models and (models & umodels):
                    best_sc = max(best_sc, 5.0)
                    continue
                sub_ov = sum(1 for t in toks if t in comp)
                if sub_ov >= 1:
                    miss_ing_boost = 0.6 if not records.get(str(p["product_id"]), {}).get("ingredients") else 0.0
                    best_sc = max(best_sc, 1.0 + miss_ing_boost + sub_ov / max(1, len(toks)))

        if best_sc > 0.0:
            scored_unfetched.append((best_sc, u))

    scored_unfetched.sort(key=lambda x: (-x[0], x[1]))
    todo = [u for _sc, u in scored_unfetched[:max_fetch]]
    print(f"  {site_tag}: unfetched_scored={len(scored_unfetched)} cached={len(pages)} fetching_top={len(todo)}", flush=True)

    if todo:
        t_f = time.perf_counter()
        with cache_jsonl.open("a", encoding="utf-8") as cf:
            for start in range(0, len(todo), 200):
                chunk = todo[start : start + 200]
                with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
                    results = list(ex.map(parser_fn, chunk))
                for u, res in zip(chunk, results):
                    entry = res if res else {"url": u, "_miss": True}
                    if "url" not in entry:
                        entry["url"] = u
                    pages[u] = entry
                    cf.write(json.dumps(entry, ensure_ascii=False) + "\n")
                cf.flush()
                done_n = min(start + 200, len(todo))
                print(f"    {site_tag} [{done_n}/{len(todo)}] elapsed={time.perf_counter()-t_f:.0f}s", flush=True)

    # Now match ALL cached pages via BARCODE first, then V4 strict title+brand verifier!
    by_bc: dict[str, dict[str, Any]] = {}
    page_list: list[tuple[dict[str, Any], set[str], set[str]]] = []
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
        ptoks = _tokens_v4(f"{pg.get('name', '')} {pg.get('brand', '')}")
        idx = len(page_list)
        page_list.append((pg, skus, ptoks))
        for t in ptoks:
            tok_idx.setdefault(t, []).append(idx)

    stats = _blank_stats()
    for p in products:
        pid = str(p["product_id"])
        if not _needs_enrichment(records.get(pid)):
            continue
        bc = str(p.get("barcode") or "").strip()
        if bc and bc in by_bc:
            if apply_fields_v3(records, p, by_bc[bc], src_label, f"{site_tag.upper()}_BARCODE_V5", stats):
                stats["barcode_hits"] += 1
                continue
        qtoks = _tokens_v4(p.get("name"), p.get("brand")) | _brand_aliases_v4(p.get("brand"))
        counts: dict[int, int] = {}
        for t in qtoks:
            for pi in tok_idx.get(t, ()):
                counts[pi] = counts.get(pi, 0) + 1
        for _ov, pi in sorted(((ov, pi) for pi, ov in counts.items() if ov >= 2), reverse=True)[:5]:
            pg, skus, _ = page_list[pi]
            m = strict_verify_v4(p, pg.get("name", ""), skus, pg.get("brand", ""), allow_nobrand_exact=True)
            if m:
                if apply_fields_v3(records, p, pg, src_label, f"{site_tag.upper()}_{m}_V5", stats):
                    stats["title_hits"] += 1
                    break
    print(f"  {site_tag.upper()} V5 stats: {stats}", flush=True)


# ---------------------------------------------------------------------------
# Stage 5: Emart Vietnam (`emartmall.com.vn`) Category + Search Crawl!
# ---------------------------------------------------------------------------
def parse_emart_product_page(url: str) -> dict[str, Any]:
    raw = fetch_fast(url, timeout=14.0)
    if not raw:
        return {"url": url, "_miss": True}
    out: dict[str, Any] = {
        "url": url, "name": "", "brand": "", "barcode": "",
        "ingredients": "", "usage": "", "storage": "", "origin": "",
    }
    m_h1 = re.search(r"<h1[^>]*>(.*?)</h1>", raw, re.S | re.I)
    if m_h1:
        out["name"] = _strip_html(m_h1.group(1))[:180]
    if not out["name"]:
        return {"url": url, "_miss": True}

    # Look for explicit EAN barcodes in the page JSON/meta/img filenames (Emart uses barcode filenames!)
    bcs = set(re.findall(r"\b(?:893\d{10}|885\d{10}|880\d{10}|899\d{10}|955\d{10}|69[0-9]\d{10}|49\d{11}|471\d{10}|40\d{11}|30\d{10,11}|32\d{11}|80\d{6,11}|84\d{11}|93\d{11})\b", raw))
    # Ignore fake repeated phone/date patterns, keep clean 8-13 digit barcodes
    out["skus"] = sorted(b for b in bcs if len(b) in (8, 12, 13))
    if len(out["skus"]) == 1:
        out["barcode"] = out["skus"][0]

    # Parse `<div class="tab-pane active" id="tab-description">...</div>`
    m_tab = re.search(r'id="tab-description"[^>]*>(.*?)</div>\s*<(?:div[^>]*id="tab-review"|/div>)', raw, re.S | re.I)
    desc_html = m_tab.group(1) if m_tab else ""
    if not desc_html:
        pos = raw.find('id="tab-description"')
        if pos != -1:
            desc_html = raw[pos : pos + 6000]
    if not desc_html:
        return out

    txt = re.sub(r"</?(?:p|div|br|li|tr|h[1-6])[^>]*>", "\n", desc_html, flags=re.I)
    txt = html.unescape(re.sub(r"<[^>]+>", " ", txt))
    lines = [re.sub(r"\s+", " ", ln).strip() for ln in txt.splitlines() if ln.strip()]

    sec_map = {
        "ingredients": re.compile(r"^(?:thành phần(?:\s+chính)?|chất liệu(?:\s+chính)?)\s*[:：]\s*(.*)$", re.I),
        "usage": re.compile(r"^(?:hướng dẫn sử dụng|cách sử dụng|cách dùng|hướng dẫn chế biến)\s*[:：]\s*(.*)$", re.I),
        "storage": re.compile(r"^(?:hướng dẫn bảo quản|cách bảo quản|bảo quản)\s*[:：]\s*(.*)$", re.I),
        "origin": re.compile(r"^(?:xuất xứ|nơi sản xuất)\s*[:：]\s*(.*)$", re.I),
        "stop": re.compile(r"^(?:tên sản phẩm|khối lượng tịnh|thể tích thực|kích thước|màu sắc|nồng độ cồn|hạn sử dụng|ngày sản xuất|lưu ý|thông tin cảnh báo|thương hiệu)\b", re.I),
    }
    buckets: dict[str, list[str]] = {"ingredients": [], "usage": [], "storage": [], "origin": []}
    cur_sec: str | None = None
    for ln in lines:
        if ln.lower().startswith("lưu ý: hình ảnh sản phẩm"):
            cur_sec = None
            continue
        matched = None
        rest = ""
        for k, pat in sec_map.items():
            mm = pat.match(ln)
            if mm and len(ln) - (len(mm.group(1)) if k != "stop" else 0) <= 35:
                matched = k
                rest = mm.group(1).strip() if k != "stop" else ""
                break
        if matched is not None:
            cur_sec = matched if matched in buckets else None
            if cur_sec and rest:
                if cur_sec == "origin":
                    buckets["origin"].append(rest[:50])
                    cur_sec = None
                else:
                    buckets[cur_sec].append(rest)
            continue
        if cur_sec and sum(len(x) for x in buckets[cur_sec]) < 450:
            buckets[cur_sec].append(ln)

    for k, vals in buckets.items():
        if vals:
            out[k] = " ".join(vals).strip()[:520]
    return out


def stage5_emart_vietnam(
    products: list[dict[str, Any]],
    records: dict[str, dict[str, Any]],
) -> None:
    print("\n=== STAGE 5: Emart Vietnam (Category Tree + Missing-Brand Search) ===", flush=True)
    idx_cache = CACHE_DIR / "emart_index.json"
    em_prods: dict[str, str] = {}  # product_id -> title

    if idx_cache.exists():
        try:
            em_prods = json.loads(idx_cache.read_text(encoding="utf-8"))
        except Exception:
            em_prods = {}

    if len(em_prods) < 1000:
        home = fetch_fast("https://emartmall.com.vn/", timeout=15.0) or ""
        cat_urls = sorted(set(html.unescape(u) for u in re.findall(r'https://emartmall\.com\.vn/index\.php\?route=product/category[^"\'\s<>]+', home)))
        cat_pages_urls = [f"{u}&limit=100" if "limit=" not in u else u for u in cat_urls]
        # Also search ALL remaining missing brands directly on Emart!
        miss_prods = [p for p in products if _needs_enrichment(records.get(str(p["product_id"])))]
        search_terms = sorted({
            norm_text(p.get("brand"))
            for p in miss_prods
            if norm_text(p.get("brand")) not in _PRIVATE_OR_GENERIC_BRANDS and len(norm_text(p.get("brand"))) >= 3
        })
        # Add critical alias terms (ottogi, th true, lock&lock, thien long, chocopie, redbull)
        search_terms = sorted(set(search_terms) | {"ottogi", "th true", "thien long", "lock&lock", "choco pie", "red bull", "kitkat", "lifebuoy", "omo", "pocky", "toppo", "lays", "doritos", "bento", "haribo", "romano", "enchanteur", "gervenne", "nivea", "ponds", "biore", "sunlight", "vim", "clear", "tresemme", "safoco", "vissan", "duc viet", "cau tre", "bibigo", "kewpie", "barona", "dhfoods", "inoci", "inochi", "deli", "hong ha", "paseo", "tempo", "glade", "president", "anchor", "bega", "belcube", "nescafe", "trung nguyen", "phuc long", "cozy"})
        search_urls = [f"https://emartmall.com.vn/index.php?route=product/search&search={urllib.parse.quote(kw)}&limit=100" for kw in search_terms]

        all_listing_urls = list(dict.fromkeys(cat_pages_urls + search_urls))
        print(f"  Crawling {len(cat_pages_urls)} Emart categories + {len(search_urls)} brand searches...", flush=True)

        link_re = re.compile(r'href="https://emartmall\.com\.vn/index\.php\?route=product/product[^"]*product_id=(\d+)[^"]*"[^>]*>\s*([^<\n]{4,140}?)\s*</a>', re.I)

        def _fetch_list(u: str) -> list[tuple[str, str]]:
            h = fetch_fast(u, timeout=13.0)
            if not h:
                return []
            return [(pid, re.sub(r"\s+", " ", html.unescape(t)).strip()) for pid, t in link_re.findall(h) if len(t.strip()) >= 4]

        with concurrent.futures.ThreadPoolExecutor(max_workers=14) as ex:
            for pairs in ex.map(_fetch_list, all_listing_urls):
                for pid, title in pairs:
                    if pid not in em_prods or len(title) > len(em_prods[pid]):
                        em_prods[pid] = title
        idx_cache.write_text(json.dumps(em_prods, ensure_ascii=False), encoding="utf-8")

    print(f"  Emart unique indexed products: {len(em_prods)}", flush=True)

    # Match Emart indexed products against still-missing catalog items
    em_items = list(em_prods.items())  # (epid, title)
    tok_idx: dict[str, list[int]] = {}
    em_meta: list[tuple[str, str, set[str], set[str]]] = []
    for i, (epid, title) in enumerate(em_items):
        toks = _tokens_v4(title)
        models = _extract_models_v4(title)
        em_meta.append((epid, title, toks, models))
        for t in toks:
            tok_idx.setdefault(t, []).append(i)

    wm_to_urls: dict[str, list[str]] = {}
    cand_urls: set[str] = set()
    for p in products:
        pid = str(p["product_id"])
        if not _needs_enrichment(records.get(pid)):
            continue
        b_al = _brand_aliases_v4(p.get("brand"))
        qtoks = _tokens_v4(p.get("name"), p.get("brand")) | b_al
        models = _extract_models_v4(p.get("name"))
        counts: dict[int, int] = {}
        for t in qtoks:
            for i in tok_idx.get(t, ()):
                counts[i] = counts.get(i, 0) + 1
        for i in sorted((k for k, v in counts.items() if v >= 2), key=lambda k: -counts[k])[:4]:
            epid, title, etoks, emodels = em_meta[i]
            if b_al and not (b_al & etoks):
                continue
            if (models and (models & emodels)) or counts[i] >= 2:
                u = f"https://emartmall.com.vn/index.php?route=product/product&product_id={epid}"
                wm_to_urls.setdefault(pid, []).append(u)
                cand_urls.add(u)

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

    todo = [u for u in sorted(cand_urls) if u not in pages]
    print(f"  Emart candidates: {len(cand_urls)} (fetching {len(todo)} new)", flush=True)
    if todo:
        with cache_jsonl.open("a", encoding="utf-8") as cf:
            for start in range(0, len(todo), 120):
                chunk = todo[start : start + 120]
                with concurrent.futures.ThreadPoolExecutor(max_workers=16) as ex:
                    for res in ex.map(parse_emart_product_page, chunk):
                        pages[res["url"]] = res
                        cf.write(json.dumps(res, ensure_ascii=False) + "\n")
                cf.flush()

    by_bc: dict[str, dict[str, Any]] = {}
    for pg in pages.values():
        if pg.get("_miss"):
            continue
        for s in pg.get("skus") or []:
            by_bc[s] = pg

    stats = _blank_stats()
    for p in products:
        pid = str(p["product_id"])
        if not _needs_enrichment(records.get(pid)):
            continue
        bc = str(p.get("barcode") or "").strip()
        if bc and bc in by_bc:
            if apply_fields_v3(records, p, by_bc[bc], "thông tin nhãn sản phẩm (Emart Vietnam)", "EMART_BARCODE", stats):
                stats["barcode_hits"] += 1
                continue
        for u in wm_to_urls.get(pid, ()):
            pg = pages.get(u)
            if not pg or pg.get("_miss"):
                continue
            m = strict_verify_v4(p, pg.get("name", ""), set(pg.get("skus") or []), pg.get("brand", ""), allow_nobrand_exact=True)
            if m:
                if apply_fields_v3(records, p, pg, "thông tin nhãn sản phẩm (Emart Vietnam)", f"EMART_{m}", stats):
                    stats["title_hits"] += 1
                    break
    print(f"  Emart stats: {stats}", flush=True)


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
    print(f"WAVE-5 BEFORE ({len(products)} SKUs): {before}", flush=True)

    stage1_openfoodfacts_direct(products, records)
    _save()

    stage2_doiduavang_and_dhfoods(products, records)
    _save()

    coop_urls = load_cooponline_urls()
    stage_compact_substring_sweep(
        site_tag="cooponline",
        src_label="thông tin nhãn sản phẩm (Co.op Online)",
        all_urls=coop_urls,
        slug_fn=lambda u: re.sub(r"--s\d+$", "", u.rstrip("/").split("/")[-1]),
        parser_fn=fetch_cooponline_page,
        products=products,
        records=records,
        max_fetch=1800,
        workers=28,
    )
    _save()

    kf_urls = load_kingfoodmart_urls(CACHE_DIR / "kingfoodmart_product_urls.json")
    stage_compact_substring_sweep(
        site_tag="kingfoodmart",
        src_label="thông tin nhãn sản phẩm (Kingfoodmart)",
        all_urls=kf_urls,
        slug_fn=lambda u: u.rstrip("/").split("/")[-1],
        parser_fn=fetch_kf_fast,
        products=products,
        records=records,
        max_fetch=2000,
        workers=28,
    )
    _save()

    stage5_emart_vietnam(products, records)
    _save()

    propagate_multipack_siblings(products, records)
    propagate_same_model_siblings(products, records)
    _save()

    after = _counts()
    print(f"\nWAVE-5 AFTER  ({len(products)} SKUs, {time.perf_counter()-t0:.1f}s): {after}", flush=True)
    for k in before:
        print(f"  {k:15s}: {before[k]:4d} -> {after[k]:4d} (+{after[k]-before[k]})", flush=True)


if __name__ == "__main__":
    main()

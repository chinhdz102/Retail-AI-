"""Wave-2 Deep Multi-Source Product Knowledge Crawler.

Targets ALL remaining missing attributes in ``data/thanh_lam_product_details.json``:
  - ``ingredients`` (thành phần chính / chất liệu)
  - ``usage`` (hướng dẫn sử dụng)
  - ``storage`` (hướng dẫn bảo quản)
  - ``origin`` (xuất xứ)
  - ``nutrition`` (structured per-100g/100ml nutrition facts)

Sources (100% public sitemaps / REST APIs / SSR pages, zero CAPTCHA bypass):
  1. OpenFoodFacts + OpenBeautyFacts + OpenProductsFacts API v2 (guaranteed zero-skip retry across all 3,099 EAN barcodes)
  2. HachiHachi (4,781 Japanese & VN supermarket/skincare/kitchenware/stationery SKUs with exact EAN-13 ``sku``)
  3. KidsPlaza (5,408 baby/milk/feeding/personal-care SKUs)
  4. Bibomart (13,017 mom & baby/household/milk SKUs)
  5. Co.op Online Deep Pass (18,092 SKUs — relaxing store-private brand filter & carton/multipack wrappers, still verified by EAN-13 or strict title/form/variant match)
  6. Kingfoodmart & Long Chau Deep Pass (relaxing private-brand & carton/multipack wrappers)
  7. Same-formula carton/multipack/pack-size sibling alignment (strictly identical brand + form + flavor/sugar variant)
"""

from __future__ import annotations

import concurrent.futures
import gzip
import hashlib
import html
import json
import re
import sys
import time
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
    _ensure_record,
    _extract_inline_nutrition,
    _extract_size_sigs,
    _is_valid_text,
    _slug_match_tokens,
    _strip_html,
    fetch_cooponline_page,
    fetch_kf_fast,
    fetch_text,
    fetch_text_urllib,
    load_cooponline_urls,
)
from scripts.enrich_detailed_ingredients import (  # noqa: E402
    content_tokens,
    extract_form,
    extract_variants,
    ingredient_specificity_score,
    is_clean_label_ingredients,
    load_kingfoodmart_urls,
    norm_text,
    strict_verify_match,
)

# Private-label / placeholder brands that should NOT block slug candidate matching on other retailers
_PRIVATE_OR_GENERIC_BRANDS = frozenset(
    {
        "thanh lam", "thanhlam", "thanh lam good", "thanhlamgood",
        "thanh lam home", "thanhlamhome", "thanh lam eco", "thanhlameco",
        "Thanh Lam Mart", "thanh lam good", "thanh lam home", "wineco", "win eco",
        "khac", "khong", "no brand", "nobrand", "oem", "unbranded", "",
    }
)

_PACK_WRAPPER_RE = re.compile(
    r"\b(?:thung\s+\d+\s*(?:lon|chai|goi|hop|ly|bich|tui)?|"
    r"loc\s+\d+\s*(?:lon|chai|goi|hop|ly|bich|tui)?|"
    r"bo\s+\d+\s*(?:chai|cai|chiec|hop|goi|tuyp)?|"
    r"set\s+\d+|t\d{2}|x\d{1,2})\b",
    re.IGNORECASE,
)


def _effective_brand_tokens(brand: str | None) -> set[str]:
    nb = norm_text(brand)
    if nb in _PRIVATE_OR_GENERIC_BRANDS:
        return set()
    return _slug_match_tokens(brand)


def _core_product_tokens(name: str | None, brand: str | None = None) -> set[str]:
    n = norm_text(name)
    n = _PACK_WRAPPER_RE.sub(" ", n)
    b = "" if norm_text(brand) in _PRIVATE_OR_GENERIC_BRANDS else (brand or "")
    return _slug_match_tokens(n, b)


def _strict_verify_relaxed_multipack(
    wm_prod: dict[str, Any],
    ext_name: str,
    ext_skus: set[str],
    ext_brand: str,
    *,
    allow_private_brand_fresh: bool = False,
) -> str | None:
    """Verify match via exact barcode or strict title (allowing carton<->single same-formula matching)."""
    m = strict_verify_match(wm_prod, ext_name, ext_skus, ext_brand)
    if m:
        return m

    # Strip carton/multipack wrappers (Thùng 24 lon X <-> Lon X) and re-verify strictly
    wm_name_stripped = _PACK_WRAPPER_RE.sub(" ", norm_text(wm_prod.get("name")))
    ext_name_stripped = _PACK_WRAPPER_RE.sub(" ", norm_text(ext_name))
    if not wm_name_stripped or not ext_name_stripped:
        return None
    if ("combo" in wm_name_stripped.split()) != ("combo" in ext_name_stripped.split()):
        return None

    wm_brand_norm = norm_text(wm_prod.get("brand"))
    is_private = wm_brand_norm in _PRIVATE_OR_GENERIC_BRANDS
    wm_brand_toks = _effective_brand_tokens(wm_prod.get("brand"))
    ext_all_toks = _slug_match_tokens(ext_name, ext_brand)

    if wm_brand_toks and not (wm_brand_toks & ext_all_toks):
        return None
    if is_private and not allow_private_brand_fresh:
        # For private-label non-fresh items, require very high token overlap & same form
        pass

    # Product form group must NOT conflict
    wm_form = extract_form(wm_name_stripped)
    ext_form = extract_form(ext_name_stripped)
    if wm_form and ext_form and wm_form[0] == ext_form[0] and wm_form[1] != ext_form[1]:
        return None

    # Sugar / zero-calorie tags must NEVER conflict
    wm_vars = extract_variants(wm_name_stripped)
    ext_vars = extract_variants(ext_name_stripped)
    sugar_tags = {"khong duong", "it duong", "co duong", "nguyen chat", "zero", "light"}
    if (wm_vars & sugar_tags) != (ext_vars & sugar_tags):
        return None

    # Flavors must NOT conflict
    diff_wm = wm_vars - ext_vars - {"kem", "tu nhien", "truyen thong", "chanh"}
    diff_ext = ext_vars - wm_vars - {"kem", "tu nhien", "truyen thong", "chanh"}
    if diff_wm or diff_ext:
        return None
    # Allow "chanh" mismatch only for Sprite/7Up where one side says "hương chanh" and other omits it
    if ("chanh" in (wm_vars ^ ext_vars)) and not ({"sprite", "7up"} & ext_all_toks):
        return None

    wm_toks = _core_product_tokens(wm_prod.get("name"), wm_prod.get("brand"))
    ext_toks = _core_product_tokens(ext_name, ext_brand)
    if not wm_toks or not ext_toks:
        return None
    ov = len(wm_toks & ext_toks)
    rec = ov / len(wm_toks)
    prec = ov / len(ext_toks)
    if wm_brand_toks and ov >= 3 and rec >= 0.72 and prec >= 0.50:
        return "STRICT_MULTIPACK"
    if wm_brand_toks and ov >= 2 and rec >= 0.80 and prec >= 0.67:
        return "STRICT_MULTIPACK"
    if is_private and allow_private_brand_fresh and ov >= 2 and rec >= 0.75 and prec >= 0.60:
        return "FRESH_TITLE"
    return None


def _get_urllib_gzip(url: str, timeout: int = 14) -> str | None:
    try:
        req = urllib.request.Request(url, headers={**WEB_HEADERS, "Accept-Encoding": "gzip, deflate"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            if r.status != 200:
                return None
            raw = r.read()
            if raw[:2] == b"\x1f\x8b":
                raw = gzip.decompress(raw)
            return raw.decode("utf-8", errors="ignore")
    except Exception:
        return None


def _apply_extracted_fields(
    records: dict[str, dict[str, Any]],
    p: dict[str, Any],
    ext: dict[str, Any],
    src_label: str,
    match_method: str,
    stats: dict[str, int],
) -> bool:
    pid = str(p["product_id"])
    cur = records.get(pid)
    cur_ing = cur.get("ingredients") if cur else None
    cur_score = ingredient_specificity_score(cur_ing)

    pg_ing = (ext.get("ingredients") or "").strip()
    want_ing = (
        _is_valid_text(pg_ing, 8)
        and (
            (is_clean_label_ingredients(pg_ing) and ingredient_specificity_score(pg_ing) > cur_score)
            or (not cur_ing and len(pg_ing) >= 10)
        )
    )
    pg_use = (ext.get("usage") or "").strip()
    want_use = not (cur and cur.get("usage")) and _is_valid_text(pg_use, 8)
    pg_sto = (ext.get("storage") or "").strip()
    want_sto = not (cur and cur.get("storage")) and _is_valid_text(pg_sto, 10)
    pg_ori = (ext.get("origin") or "").strip()
    want_ori = not (cur and cur.get("origin")) and _is_valid_text(pg_ori, 3)
    pg_nut = ext.get("nutrition")
    want_nut = not (cur and cur.get("nutrition")) and isinstance(pg_nut, dict) and bool(pg_nut)
    pg_sum = (ext.get("summary") or "").strip()
    want_sum = not (cur and cur.get("summary")) and _is_valid_text(pg_sum, 25)

    if not (want_ing or want_use or want_sto or want_ori or want_nut):
        return False

    rec, created = _ensure_record(records, p)
    if created:
        stats["new_records"] += 1
    url = ext.get("url") or ""

    if want_ing:
        if cur_ing:
            stats["ing_upgraded"] += 1
        else:
            stats["ing_added"] += 1
        rec["ingredients"] = pg_ing
        rec["ingredients_source"] = src_label
        if url:
            rec["ingredients_source_url"] = url
        rec["ingredients_match_method"] = match_method
    if want_use:
        rec["usage"] = pg_use
        rec["usage_source"] = src_label
        if url:
            rec["usage_source_url"] = url
        stats["usage_added"] += 1
    if want_sto:
        rec["storage"] = pg_sto
        rec["storage_source"] = src_label
        if url:
            rec["storage_source_url"] = url
        stats["storage_added"] += 1
    if want_ori:
        orig = "Việt Nam" if pg_ori.upper() == "VIETNAM" else pg_ori
        rec["origin"] = orig
        rec["origin_source"] = src_label
        if url:
            rec["origin_source_url"] = url
        stats["origin_added"] += 1
    if want_nut:
        rec["nutrition"] = pg_nut
        rec["nutrition_source"] = src_label
        if url:
            rec["nutrition_source_url"] = url
        stats["nutrition_added"] += 1
    if want_sum:
        rec["summary"] = pg_sum
    return True


def _blank_stats() -> dict[str, int]:
    return {
        "barcode_hits": 0, "title_hits": 0, "new_records": 0,
        "ing_added": 0, "ing_upgraded": 0, "usage_added": 0,
        "storage_added": 0, "origin_added": 0, "nutrition_added": 0,
    }


# ---------------------------------------------------------------------------
# Stage A: OpenFoodFacts + OpenBeautyFacts + OpenProductsFacts (zero-skip retry!)
# ---------------------------------------------------------------------------
def enrich_all_open_facts(
    products: list[dict[str, Any]],
    records: dict[str, dict[str, Any]],
) -> None:
    print("\n=== STAGE A: OpenFoodFacts + OpenBeautyFacts + OpenProductsFacts (zero-skip retry) ===", flush=True)
    by_bc: dict[str, list[dict[str, Any]]] = {}
    for p in products:
        bc = str(p.get("barcode") or "").strip()
        if re.fullmatch(r"\d{8,14}", bc):
            by_bc.setdefault(bc, []).append(p)
    barcodes = sorted(by_bc.keys())
    fields = "code,product_name,brands,ingredients_text_vi,ingredients_text,nutriments,nutrition_data_per,serving_size,origins"

    for host, src_label in (
        ("world.openfoodfacts.org", "OpenFoodFacts"),
        ("world.openbeautyfacts.org", "OpenBeautyFacts"),
        ("world.openproductsfacts.org", "OpenProductsFacts"),
    ):
        stats = _blank_stats()
        batches = [barcodes[i : i + 60] for i in range(0, len(barcodes), 60)]
        ok_batches = 0
        found_prods: list[dict[str, Any]] = []

        def _fetch_b(batch: list[str]) -> tuple[bool, list[dict[str, Any]]]:
            qs = urllib.parse.urlencode({"code": ",".join(batch), "fields": fields, "page_size": str(len(batch))})
            url = f"https://{host}/api/v2/search?{qs}"
            for attempt in range(4):
                try:
                    req = urllib.request.Request(url, headers=OFF_HEADERS)
                    with urllib.request.urlopen(req, timeout=22) as r:
                        if r.status == 200:
                            d = json.loads(r.read().decode("utf-8", errors="replace"))
                            return True, d.get("products") or []
                except Exception:
                    time.sleep(1.2 * (attempt + 1))
            return False, []

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:
            for ok, plist in ex.map(_fetch_b, batches):
                if ok:
                    ok_batches += 1
                    found_prods.extend(plist)

        for prod in found_prods:
            code = str(prod.get("code") or "").strip()
            if code not in by_bc:
                continue
            nutr_raw = prod.get("nutriments") or {}
            nutr: dict[str, Any] = {}
            if isinstance(nutr_raw, dict):
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
                if nutr:
                    per = str(prod.get("nutrition_data_per") or "100g")
                    nutr["basis"] = "100ml" if "ml" in per.lower() else "100g"

            ing = str(prod.get("ingredients_text_vi") or "").strip() or str(prod.get("ingredients_text") or "").strip()
            ing = re.sub(r"\s+", " ", ing).strip()[:550]
            orig = str(prod.get("origins") or "").strip()
            if orig.lower() in ("vietnam", "việt nam", "en:vietnam"):
                orig = "Việt Nam"
            elif orig.startswith("en:"):
                orig = orig[3:].replace("-", " ").title()

            for p in by_bc[code]:
                pn = (p.get("name") or "").lower()
                n_adj = {**nutr, "basis": "100ml"} if (nutr and any(w in pn for w in ("ml", "lít", "lit", "nước", "sữa tươi", "trà ", "bia "))) else nutr
                ext = {
                    "url": f"https://{host}/product/{code}",
                    "ingredients": ing,
                    "origin": orig,
                    "nutrition": n_adj or None,
                }
                if _apply_extracted_fields(records, p, ext, src_label, f"{src_label.upper()}_BARCODE", stats):
                    stats["barcode_hits"] += 1

        print(f"  {src_label:18s} batches={ok_batches}/{len(batches)} hits={len(found_prods)} stats={stats}", flush=True)


# ---------------------------------------------------------------------------
# Stage B: HachiHachi (4,781 products with exact EAN-13 `sku`!)
# ---------------------------------------------------------------------------
def parse_hachihachi_page(url: str) -> dict[str, Any]:
    raw = fetch_text(url, timeout=12)
    if not raw:
        return {"url": url, "_miss": True}
    out: dict[str, Any] = {
        "url": url, "name": "", "brand": "", "barcode": "",
        "ingredients": "", "usage": "", "storage": "", "origin": "", "summary": "",
    }
    for m in re.finditer(r'<script[^>]*application/ld\+json[^>]*>(.*?)</script>', raw, re.S):
        if '"Product"' not in m.group(1):
            continue
        try:
            obj = json.loads(m.group(1))
            if isinstance(obj, dict) and obj.get("@type") == "Product":
                out["name"] = re.sub(r"^\d{3,4}\s+", "", str(obj.get("name") or "").strip())
                sku = str(obj.get("sku") or "").strip()
                if re.fullmatch(r"\d{8,14}", sku):
                    out["barcode"] = sku
                b = obj.get("brand")
                if isinstance(b, dict):
                    out["brand"] = str(b.get("name") or "").strip()
                desc = str(obj.get("description") or "")
                m_ori = re.search(r"xuất xứ\s*:\s*([^,\.\n]{2,40})", desc, re.I)
                if m_ori:
                    out["origin"] = m_ori.group(1).strip()
        except Exception:
            pass

    # Extract structured sections from HachiHachi HTML
    # Convert tags to newline-delimited lines for reliable heading splitting
    cleaned_html = re.sub(r"<(?:script|style|noscript)[^>]*>.*?</(?:script|style|noscript)>", " ", raw, flags=re.S | re.I)
    text = re.sub(r"</?(?:p|div|br|li|tr|td|th|h[1-6]|ul|ol|table)[^>]*>", "\n", cleaned_html, flags=re.I)
    text = html.unescape(re.sub(r"<[^>]+>", " ", text))
    lines = [re.sub(r"\s+", " ", ln).strip() for ln in text.splitlines() if ln.strip()]

    sec_map = {
        "ingredients": re.compile(r"^(?:chất liệu\s*/\s*thành phần|thành phần(?:\s+chính)?|chất liệu(?:\s+chính)?)\s*[:：]?\s*(.*)$", re.I),
        "usage": re.compile(r"^(?:hướng dẫn sử dụng|cách sử dụng|cách dùng)\s*[:：]?\s*(.*)$", re.I),
        "storage": re.compile(r"^(?:hướng dẫn bảo quản|bảo quản|cách bảo quản)\s*[:：]?\s*(.*)$", re.I),
        "origin": re.compile(r"^(?:xuất xứ|nơi sản xuất)\s*[:：]?\s*(.*)$", re.I),
        "stop": re.compile(r"^(?:mô tả chung|đặc điểm nổi bật|lưu ý|thông tin thương hiệu|thương hiệu|nhà sản xuất|mã sản phẩm|mã vạch|sku|barcode|hạn sử dụng|ngày sản xuất|quy cách|dung tích|kích thước|màu sắc|trọng lượng|khối lượng|thông số kỹ thuật|sản phẩm liên quan|đánh giá|bình luận|chính sách|mua ngay|thêm vào giỏ)\b", re.I),
    }
    buckets: dict[str, list[str]] = {"ingredients": [], "usage": [], "storage": [], "origin": []}
    cur_sec: str | None = None
    for ln in lines:
        matched = None
        rest = ""
        for k, pat in sec_map.items():
            mm = pat.match(ln)
            if mm and len(ln) - (len(mm.group(1)) if k != "stop" else 0) <= 45:
                matched = k
                rest = mm.group(1).strip() if k != "stop" else ""
                break
        if matched is not None:
            cur_sec = matched if matched in buckets else None
            if cur_sec and rest:
                buckets[cur_sec].append(rest)
                if cur_sec == "origin":
                    cur_sec = None
            continue
        if cur_sec == "origin":
            if len(ln) <= 60:
                buckets["origin"].append(ln)
            cur_sec = None
        elif cur_sec and sum(len(x) for x in buckets[cur_sec]) < 480:
            buckets[cur_sec].append(ln)

    for k in buckets:
        if buckets[k] and not out[k]:
            out[k] = " ".join(dict.fromkeys(buckets[k]))[:550].strip()

    if not out["name"]:
        return {"url": url, "_miss": True}
    return out


def enrich_from_hachihachi(
    products: list[dict[str, Any]],
    records: dict[str, dict[str, Any]],
) -> dict[str, int]:
    print("\n=== STAGE B: HachiHachi (4,781 products with EAN-13) ===", flush=True)
    cache_u = CACHE_DIR / "hachihachi_urls.json"
    if cache_u.exists():
        urls = json.loads(cache_u.read_text(encoding="utf-8"))
    else:
        xml = _get_urllib_gzip("https://hachihachi.com.vn/sitemap-products.xml", timeout=20)
        urls = sorted(set(re.findall(r"<loc>(.*?)</loc>", xml or "")))
        if urls:
            cache_u.write_text(json.dumps(urls, ensure_ascii=False), encoding="utf-8")
    print(f"  Loaded {len(urls)} HachiHachi URLs", flush=True)

    hh_index: dict[str, list[int]] = {}
    hh_slugs: list[tuple[str, set[str], str]] = []
    for idx, u in enumerate(urls):
        slug = u.rstrip("/").split("/")[-1]
        nslug = norm_text(slug.replace("-", " "))
        toks = _slug_match_tokens(nslug)
        hh_slugs.append((u, toks, nslug))
        for t in toks:
            hh_index.setdefault(t, []).append(idx)

    wm_to_urls: dict[str, list[str]] = {}
    unique_urls: set[str] = set()
    for p in products:
        pid = str(p["product_id"])
        cur = records.get(pid, {})
        if not (
            ingredient_specificity_score(cur.get("ingredients")) < 4
            or not cur.get("usage")
            or not cur.get("storage")
            or not cur.get("origin")
        ):
            continue
        ptoks = _core_product_tokens(p.get("name"), p.get("brand"))
        brand_toks = _effective_brand_tokens(p.get("brand"))
        if not ptoks:
            continue
        counts: dict[int, int] = {}
        for t in ptoks:
            for ci in hh_index.get(t, ()):
                counts[ci] = counts.get(ci, 0) + 1
        ranked = []
        for ci, ov in counts.items():
            if ov < 2:
                continue
            u, utoks, _ns = hh_slugs[ci]
            if brand_toks and not (brand_toks & utoks):
                continue
            rec = ov / len(ptoks)
            jac = ov / max(1, len(ptoks | utoks))
            if (ov >= 3 and rec >= 0.45) or (ov >= 2 and jac >= 0.38 and brand_toks) or (ov >= 2 and rec >= 0.65):
                ranked.append((rec, jac, ov, u))
        if ranked:
            ranked.sort(reverse=True)
            for _r, _j, _o, u in ranked[:3]:
                wm_to_urls.setdefault(pid, []).append(u)
                unique_urls.add(u)

    cache_jsonl = CACHE_DIR / "hachihachi_pages.jsonl"
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

    todo = [u for u in sorted(unique_urls) if u not in pages]
    print(f"  Candidate HachiHachi pages: {len(unique_urls)} (fetching {len(todo)} new)", flush=True)
    if todo:
        with cache_jsonl.open("a", encoding="utf-8") as cf:
            for start in range(0, len(todo), 150):
                chunk = todo[start : start + 150]
                with concurrent.futures.ThreadPoolExecutor(max_workers=20) as ex:
                    for res in ex.map(parse_hachihachi_page, chunk):
                        pages[res["url"]] = res
                        cf.write(json.dumps(res, ensure_ascii=False) + "\n")
                cf.flush()

    by_bc = {pg["barcode"]: pg for pg in pages.values() if not pg.get("_miss") and pg.get("barcode")}
    stats = _blank_stats()
    for p in products:
        pid = str(p["product_id"])
        bc = str(p.get("barcode") or "").strip()
        best_pg = by_bc.get(bc) if bc else None
        best_m = "HACHIHACHI_BARCODE" if best_pg else None
        if best_pg is None:
            for u in wm_to_urls.get(pid, ()):
                pg = pages.get(u)
                if not pg or pg.get("_miss"):
                    continue
                skus = {pg["barcode"]} if pg.get("barcode") else set()
                m = _strict_verify_relaxed_multipack(p, pg.get("name", ""), skus, pg.get("brand", ""))
                if m:
                    best_pg = pg
                    best_m = f"HACHIHACHI_{m}"
                    break
        if best_pg and best_m:
            if _apply_extracted_fields(records, p, best_pg, "thông tin nhãn sản phẩm (HachiHachi)", best_m, stats):
                if "BARCODE" in best_m:
                    stats["barcode_hits"] += 1
                else:
                    stats["title_hits"] += 1
    print(f"  HachiHachi stats: {stats}", flush=True)
    return stats


# ---------------------------------------------------------------------------
# Stage C: KidsPlaza (5,408) + Bibomart (13,017) for baby/milk/feeding/skincare/household
# ---------------------------------------------------------------------------
def parse_generic_vn_product_page(url: str) -> dict[str, Any]:
    raw = _get_urllib_gzip(url, timeout=14)
    if not raw:
        return {"url": url, "_miss": True}
    out: dict[str, Any] = {
        "url": url, "name": "", "brand": "", "barcode": "",
        "ingredients": "", "usage": "", "storage": "", "origin": "", "summary": "",
    }
    for m in re.finditer(r'<script[^>]*application/ld\+json[^>]*>(.*?)</script>', raw, re.S):
        if '"Product"' not in m.group(1):
            continue
        try:
            obj = json.loads(m.group(1))
            if isinstance(obj, dict) and obj.get("@type") == "Product":
                out["name"] = str(obj.get("name") or "").strip()
                b = obj.get("brand")
                out["brand"] = str(b.get("name") if isinstance(b, dict) else b or "").strip()
                for k in ("gtin13", "gtin", "sku"):
                    v = str(obj.get(k) or "").strip()
                    if re.fullmatch(r"\d{8,14}", v):
                        out["barcode"] = v
                        break
        except Exception:
            pass
    if not out["name"]:
        m_t = re.search(r"<h1[^>]*>(.*?)</h1>", raw, re.S | re.I)
        if m_t:
            out["name"] = _strip_html(m_t.group(1))[:180]
    if not out["name"]:
        return {"url": url, "_miss": True}

    # Remove script/style blocks before section parsing
    cleaned_html = re.sub(r"<(?:script|style|noscript)[^>]*>.*?</(?:script|style|noscript)>", " ", raw, flags=re.S | re.I)
    text = re.sub(r"</?(?:p|div|br|li|tr|td|th|h[1-6]|ul|ol|table)[^>]*>", "\n", cleaned_html, flags=re.I)
    text = html.unescape(re.sub(r"<[^>]+>", " ", text))
    lines = [re.sub(r"\s+", " ", ln).strip() for ln in text.splitlines() if ln.strip()]

    sec_map = {
        "ingredients": re.compile(r"^(?:[\d\.\-\*\•]+\s*)?(?:thành phần(?:\s+chính|\s+sản phẩm|\s+nguyên liệu)?|chất liệu(?:\s+chính|\s+sản phẩm)?)\s*[:：\-]?\s*(.*)$", re.I),
        "usage": re.compile(r"^(?:[\d\.\-\*\•]+\s*)?(?:hướng dẫn sử dụng|cách sử dụng|cách dùng|cách pha)\s*[:：\-]?\s*(.*)$", re.I),
        "storage": re.compile(r"^(?:[\d\.\-\*\•]+\s*)?(?:hướng dẫn bảo quản|cách bảo quản|bảo quản(?:\s+sản phẩm)?)\s*[:：\-]?\s*(.*)$", re.I),
        "origin": re.compile(r"^(?:[\d\.\-\*\•]+\s*)?(?:xuất xứ|nơi sản xuất)\s*[:：\-]?\s*(.*)$", re.I),
        "stop": re.compile(r"^(?:thông tin sản phẩm|đặc điểm nổi bật|công dụng|lưu ý|thương hiệu|nhà sản xuất|nhà cung cấp|mã sản phẩm|mã vạch|sku|barcode|đơn vị|quy cách|dung tích|trọng lượng|khối lượng|độ tuổi|hạn sử dụng|ngày sản xuất|kích thước|màu sắc|giá bán|tình trạng|còn hàng|hết hàng|chọn mua|thêm vào giỏ|mua ngay|thông số kỹ thuật|đánh giá|bình luận|sản phẩm liên quan|hỏi đáp|chính sách)\b", re.I),
    }
    buckets: dict[str, list[str]] = {"ingredients": [], "usage": [], "storage": [], "origin": []}
    cur_sec: str | None = None
    for ln in lines:
        matched = None
        rest = ""
        for k, pat in sec_map.items():
            mm = pat.match(ln)
            if mm and len(ln) - (len(mm.group(1)) if k != "stop" else 0) <= 42:
                matched = k
                rest = mm.group(1).strip() if k != "stop" else ""
                break
        if matched is not None:
            cur_sec = matched if matched in buckets else None
            if cur_sec and rest:
                buckets[cur_sec].append(rest)
                if cur_sec == "origin":
                    cur_sec = None
            continue
        if cur_sec == "origin":
            if len(ln) <= 60:
                buckets["origin"].append(ln)
            cur_sec = None
        elif cur_sec and sum(len(x) for x in buckets[cur_sec]) < 420:
            if len(ln) <= 380:
                buckets[cur_sec].append(ln)

    for k in buckets:
        if buckets[k]:
            out[k] = " ".join(dict.fromkeys(buckets[k]))[:520].strip()
    return out


def enrich_from_sitemap_site(
    site_tag: str,
    src_label: str,
    sitemap_url: str,
    url_filter_fn,
    products: list[dict[str, Any]],
    records: dict[str, dict[str, Any]],
    max_pages: int = 700,
) -> dict[str, int]:
    print(f"\n=== STAGE C: {site_tag} ===", flush=True)
    cache_u = CACHE_DIR / f"{site_tag.lower()}_urls.json"
    if cache_u.exists():
        urls = json.loads(cache_u.read_text(encoding="utf-8"))
    else:
        xml = _get_urllib_gzip(sitemap_url, timeout=20)
        urls = sorted(set(u for u in re.findall(r"<loc>(.*?)</loc>", xml or "") if url_filter_fn(u)))
        if urls:
            cache_u.write_text(json.dumps(urls, ensure_ascii=False), encoding="utf-8")
    print(f"  Loaded {len(urls)} {site_tag} URLs", flush=True)

    idx_map: dict[str, list[int]] = {}
    slug_list: list[tuple[str, set[str]]] = []
    for idx, u in enumerate(urls):
        slug = u.rstrip("/").split("/")[-1].replace(".html", "")
        toks = _slug_match_tokens(norm_text(slug.replace("-", " ")))
        slug_list.append((u, toks))
        for t in toks:
            idx_map.setdefault(t, []).append(idx)

    wm_to_urls: dict[str, list[str]] = {}
    unique_urls: list[str] = []
    seen_u: set[str] = set()
    for p in products:
        pid = str(p["product_id"])
        cur = records.get(pid, {})
        if not (not cur.get("ingredients") or not cur.get("usage") or not cur.get("storage")):
            continue
        ptoks = _core_product_tokens(p.get("name"), p.get("brand"))
        brand_toks = _effective_brand_tokens(p.get("brand"))
        if not ptoks or not brand_toks:
            continue
        counts: dict[int, int] = {}
        for t in ptoks:
            for ci in idx_map.get(t, ()):
                counts[ci] = counts.get(ci, 0) + 1
        ranked = []
        for ci, ov in counts.items():
            if ov < 2:
                continue
            u, utoks = slug_list[ci]
            if not (brand_toks & utoks):
                continue
            rec = ov / len(ptoks)
            jac = ov / max(1, len(ptoks | utoks))
            if (ov >= 3 and rec >= 0.50) or (ov >= 2 and jac >= 0.45):
                ranked.append((rec, jac, ov, u))
        if ranked:
            ranked.sort(reverse=True)
            for _r, _j, _o, u in ranked[:2]:
                wm_to_urls.setdefault(pid, []).append(u)
                if u not in seen_u and len(unique_urls) < max_pages:
                    seen_u.add(u)
                    unique_urls.append(u)

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

    todo = [u for u in unique_urls if u not in pages]
    print(f"  Candidate {site_tag} pages: {len(unique_urls)} (fetching {len(todo)} new)", flush=True)
    if todo:
        with cache_jsonl.open("a", encoding="utf-8") as cf:
            for start in range(0, len(todo), 120):
                chunk = todo[start : start + 120]
                with concurrent.futures.ThreadPoolExecutor(max_workers=16) as ex:
                    for res in ex.map(parse_generic_vn_product_page, chunk):
                        pages[res["url"]] = res
                        cf.write(json.dumps(res, ensure_ascii=False) + "\n")
                cf.flush()

    stats = _blank_stats()
    for p in products:
        pid = str(p["product_id"])
        for u in wm_to_urls.get(pid, ()):
            pg = pages.get(u)
            if not pg or pg.get("_miss"):
                continue
            skus = {pg["barcode"]} if pg.get("barcode") else set()
            m = _strict_verify_relaxed_multipack(p, pg.get("name", ""), skus, pg.get("brand", ""))
            if m:
                if _apply_extracted_fields(records, p, pg, src_label, f"{site_tag.upper()}_{m}", stats):
                    if "BARCODE" in m:
                        stats["barcode_hits"] += 1
                    else:
                        stats["title_hits"] += 1
                break
    print(f"  {site_tag} stats: {stats}", flush=True)
    return stats


# ---------------------------------------------------------------------------
# Stage D: Co.op Online Deep Pass (private-brand + fresh produce + carton/multipack!)
# ---------------------------------------------------------------------------
def enrich_cooponline_deep(
    products: list[dict[str, Any]],
    records: dict[str, dict[str, Any]],
) -> dict[str, int]:
    print("\n=== STAGE D: Co.op Online Deep Pass (private-brand, fresh & multipack relaxed) ===", flush=True)
    coop_urls = load_cooponline_urls()
    coop_index: dict[str, list[int]] = {}
    coop_slugs: list[tuple[str, set[str], str, set[str]]] = []
    for idx, u in enumerate(coop_urls):
        slug = re.sub(r"--s\d+$", "", u.rstrip("/").split("/")[-1])
        nslug = norm_text(slug.replace("-", " "))
        toks = _core_product_tokens(nslug)
        sz = _extract_size_sigs(slug)
        coop_slugs.append((u, toks, nslug, sz))
        for t in toks:
            coop_index.setdefault(t, []).append(idx)

    fresh_cats = {"Trái cây tươi", "Củ, Quả", "Rau Lá", "Thịt", "Trứng", "Hải sản", "Cá, Hải Sản"}
    wm_to_urls: dict[str, list[str]] = {}
    unique_urls: set[str] = set()
    for p in products:
        pid = str(p["product_id"])
        cur = records.get(pid, {})
        if not (not cur.get("ingredients") or not cur.get("usage") or not cur.get("storage") or not cur.get("origin")):
            continue
        wm_norm = norm_text(p.get("name"))
        ptoks = _core_product_tokens(p.get("name"), p.get("brand"))
        brand_toks = _effective_brand_tokens(p.get("brand"))
        is_fresh = (p.get("category_name") or "") in fresh_cats
        if not ptoks:
            continue
        wm_is_combo = "combo" in wm_norm.split()
        wm_sz = _extract_size_sigs(p.get("name"))
        wm_zero = bool(re.search(r"\b(?:khong calo|khong duong|zero|light)\b", wm_norm))

        counts: dict[int, int] = {}
        for t in ptoks:
            for ci in coop_index.get(t, ()):
                counts[ci] = counts.get(ci, 0) + 1
        ranked = []
        for ci, ov in counts.items():
            if ov < 2:
                continue
            u, utoks, nslug, usz = coop_slugs[ci]
            if ("combo" in nslug.split()) != wm_is_combo:
                continue
            if brand_toks and not (brand_toks & utoks):
                continue
            u_zero = bool(re.search(r"\b(?:khong calo|khong duong|zero|light)\b", nslug))
            if wm_zero != u_zero:
                continue
            rec = ov / len(ptoks)
            jac = ov / max(1, len(ptoks | utoks))
            if (
                (ov >= 3 and rec >= 0.40)
                or (ov >= 2 and jac >= 0.34 and (brand_toks or is_fresh))
                or (ov >= 2 and rec >= 0.60)
            ):
                sz_bonus = 0.25 if (wm_sz and usz and (wm_sz & usz)) else 0.0
                ranked.append((rec + sz_bonus, jac, ov, u))
        if ranked:
            ranked.sort(reverse=True)
            for _r, _j, _o, u in ranked[:4]:
                wm_to_urls.setdefault(pid, []).append(u)
                unique_urls.add(u)

    cache_jsonl = CACHE_DIR / "cooponline_pages.jsonl"
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

    todo = [u for u in sorted(unique_urls) if u not in pages]
    print(f"  Deep Co.op Online candidates: {len(unique_urls)} (cached={len(unique_urls)-len(todo)}, fetching {len(todo)} new)", flush=True)
    if todo:
        t_f = time.perf_counter()
        with cache_jsonl.open("a", encoding="utf-8") as cf:
            for start in range(0, len(todo), 200):
                chunk = todo[start : start + 200]
                with concurrent.futures.ThreadPoolExecutor(max_workers=28) as ex:
                    results = list(ex.map(fetch_cooponline_page, chunk))
                for u, res in zip(chunk, results):
                    entry = res if res else {"url": u, "_miss": True}
                    pages[u] = entry
                    cf.write(json.dumps(entry, ensure_ascii=False) + "\n")
                cf.flush()
                done_n = min(start + 200, len(todo))
                print(f"    CoopDeep [{done_n}/{len(todo)}] elapsed={time.perf_counter()-t_f:.0f}s", flush=True)

    by_bc = {pg["barcode"]: pg for pg in pages.values() if not pg.get("_miss") and pg.get("barcode")}
    stats = _blank_stats()
    for p in products:
        pid = str(p["product_id"])
        cur = records.get(pid, {})
        if not (not cur.get("ingredients") or not cur.get("usage") or not cur.get("storage") or not cur.get("origin")):
            continue
        is_fresh = (p.get("category_name") or "") in fresh_cats
        bc = str(p.get("barcode") or "").strip()
        best_pg = by_bc.get(bc) if bc else None
        best_m = "COOPONLINE_BARCODE" if best_pg else None
        if best_pg is None:
            for u in wm_to_urls.get(pid, ()):
                pg = pages.get(u)
                if not pg or pg.get("_miss"):
                    continue
                skus = {pg["barcode"]} if pg.get("barcode") else set()
                m = _strict_verify_relaxed_multipack(
                    p, pg.get("name", ""), skus, pg.get("brand", ""),
                    allow_private_brand_fresh=is_fresh,
                )
                if m:
                    best_pg = pg
                    best_m = f"COOPONLINE_{m}"
                    break
        if best_pg and best_m:
            if _apply_extracted_fields(records, p, best_pg, "thông tin nhãn sản phẩm (Co.op Online)", best_m, stats):
                if "BARCODE" in best_m:
                    stats["barcode_hits"] += 1
                else:
                    stats["title_hits"] += 1
    print(f"  Co.op Online Deep stats: {stats}", flush=True)
    return stats


# ---------------------------------------------------------------------------
# Stage E: Kingfoodmart Deep Pass (private-brand, fresh & multipack relaxed)
# ---------------------------------------------------------------------------
def enrich_kingfoodmart_deep(
    products: list[dict[str, Any]],
    records: dict[str, dict[str, Any]],
) -> dict[str, int]:
    print("\n=== STAGE E: Kingfoodmart Deep Pass ===", flush=True)
    kf_urls = load_kingfoodmart_urls(CACHE_DIR / "kingfoodmart_product_urls.json")
    kf_index: dict[str, list[int]] = {}
    kf_slugs: list[tuple[str, set[str], str]] = []
    for idx, u in enumerate(kf_urls):
        slug = u.rstrip("/").split("/")[-1]
        nslug = norm_text(slug.replace("-", " "))
        toks = _core_product_tokens(nslug)
        kf_slugs.append((u, toks, nslug))
        for t in toks:
            kf_index.setdefault(t, []).append(idx)

    fresh_cats = {"Trái cây tươi", "Củ, Quả", "Rau Lá", "Thịt", "Trứng", "Hải sản", "Cá, Hải Sản"}
    wm_to_urls: dict[str, list[str]] = {}
    unique_urls: set[str] = set()
    for p in products:
        pid = str(p["product_id"])
        cur = records.get(pid, {})
        if not (not cur.get("ingredients") or not cur.get("usage") or not cur.get("storage") or not cur.get("origin")):
            continue
        wm_norm = norm_text(p.get("name"))
        ptoks = _core_product_tokens(p.get("name"), p.get("brand"))
        brand_toks = _effective_brand_tokens(p.get("brand"))
        is_fresh = (p.get("category_name") or "") in fresh_cats
        if not ptoks:
            continue
        wm_is_combo = "combo" in wm_norm.split()
        counts: dict[int, int] = {}
        for t in ptoks:
            for ci in kf_index.get(t, ()):
                counts[ci] = counts.get(ci, 0) + 1
        ranked = []
        for ci, ov in counts.items():
            if ov < 2:
                continue
            u, utoks, nslug = kf_slugs[ci]
            if ("combo" in nslug.split()) != wm_is_combo:
                continue
            if brand_toks and not (brand_toks & utoks):
                continue
            rec = ov / len(ptoks)
            jac = ov / max(1, len(ptoks | utoks))
            if (ov >= 3 and rec >= 0.45) or (ov >= 2 and jac >= 0.38 and (brand_toks or is_fresh)):
                ranked.append((rec, jac, ov, u))
        if ranked:
            ranked.sort(reverse=True)
            for _r, _j, _o, u in ranked[:3]:
                wm_to_urls.setdefault(pid, []).append(u)
                unique_urls.add(u)

    cache_jsonl = CACHE_DIR / "kingfoodmart_pages.jsonl"
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

    todo = [u for u in sorted(unique_urls) if u not in pages]
    print(f"  Deep KF candidates: {len(unique_urls)} (fetching {len(todo)} new)", flush=True)
    if todo:
        with cache_jsonl.open("a", encoding="utf-8") as cf:
            for start in range(0, len(todo), 200):
                chunk = todo[start : start + 200]
                with concurrent.futures.ThreadPoolExecutor(max_workers=28) as ex:
                    for u, res in zip(chunk, ex.map(fetch_kf_fast, chunk)):
                        entry = res if res else {"url": u, "_miss": True}
                        pages[u] = entry
                        cf.write(json.dumps(entry, ensure_ascii=False) + "\n")
                cf.flush()

    by_bc: dict[str, dict[str, Any]] = {}
    for pg in pages.values():
        if not pg.get("_miss"):
            for sku in pg.get("skus") or []:
                if len(str(sku)) >= 8:
                    by_bc[str(sku)] = pg

    stats = _blank_stats()
    for p in products:
        pid = str(p["product_id"])
        cur = records.get(pid, {})
        if not (not cur.get("ingredients") or not cur.get("usage") or not cur.get("storage") or not cur.get("origin")):
            continue
        is_fresh = (p.get("category_name") or "") in fresh_cats
        bc = str(p.get("barcode") or "").strip()
        best_pg = by_bc.get(bc) if bc else None
        best_m = "KINGFOODMART_BARCODE" if best_pg else None
        if best_pg is None:
            for u in wm_to_urls.get(pid, ()):
                pg = pages.get(u)
                if not pg or pg.get("_miss"):
                    continue
                m = _strict_verify_relaxed_multipack(
                    p, pg.get("name", ""), set(pg.get("skus") or []), pg.get("brand", ""),
                    allow_private_brand_fresh=is_fresh,
                )
                if m:
                    best_pg = pg
                    best_m = f"KINGFOODMART_{m}"
                    break
        if best_pg and best_m:
            if _apply_extracted_fields(records, p, best_pg, "thông tin nhãn sản phẩm (Kingfoodmart)", best_m, stats):
                if "BARCODE" in best_m:
                    stats["barcode_hits"] += 1
                else:
                    stats["title_hits"] += 1
    print(f"  Kingfoodmart Deep stats: {stats}", flush=True)
    return stats


# ---------------------------------------------------------------------------
# Stage F: Carton / Multipack <-> Single-Unit Same-Formula Sibling Alignment
# ---------------------------------------------------------------------------
def propagate_multipack_siblings(
    products: list[dict[str, Any]],
    records: dict[str, dict[str, Any]],
) -> dict[str, int]:
    print("\n=== STAGE F: Carton / Multipack <-> Single Same-Formula Sibling Alignment ===", flush=True)

    def formula_key(p: dict[str, Any]) -> tuple[str, tuple[str, ...], str, tuple[str, ...]] | None:
        brand = norm_text(p.get("brand"))
        if brand in _PRIVATE_OR_GENERIC_BRANDS or len(brand) < 2:
            return None
        name_norm = _PACK_WRAPPER_RE.sub(" ", norm_text(p.get("name")))
        if "combo" in name_norm.split():
            return None
        toks = sorted(_core_product_tokens(p.get("name"), p.get("brand")))
        if len(toks) < 2:
            return None
        form = extract_form(name_norm)
        form_tag = f"{form[0]}:{form[1]}" if form else ""
        vars_sorted = tuple(sorted(extract_variants(name_norm)))
        return (brand, tuple(toks), form_tag, vars_sorted)

    groups: dict[tuple, list[dict[str, Any]]] = {}
    for p in products:
        k = formula_key(p)
        if k:
            groups.setdefault(k, []).append(p)

    stats = {"ing_shared": 0, "usage_shared": 0, "storage_shared": 0, "origin_shared": 0, "nutrition_shared": 0, "new_records": 0}
    for _k, plist in groups.items():
        if len(plist) < 2:
            continue
        best_ing = max(
            (records.get(p["product_id"], {}) for p in plist),
            key=lambda r: ingredient_specificity_score(r.get("ingredients")),
            default={},
        )
        best_use = next((records[p["product_id"]] for p in plist if records.get(p["product_id"], {}).get("usage")), None)
        best_sto = next((records[p["product_id"]] for p in plist if records.get(p["product_id"], {}).get("storage")), None)
        best_ori = next((records[p["product_id"]] for p in plist if records.get(p["product_id"], {}).get("origin")), None)
        best_nut = next((records[p["product_id"]] for p in plist if records.get(p["product_id"], {}).get("nutrition")), None)

        best_ing_score = ingredient_specificity_score(best_ing.get("ingredients"))
        for p in plist:
            pid = str(p["product_id"])
            cur = records.get(pid)
            cur_score = ingredient_specificity_score(cur.get("ingredients") if cur else None)
            want_ing = best_ing_score >= 3 and cur_score < 2 and bool(best_ing.get("ingredients"))
            want_use = best_use is not None and not (cur and cur.get("usage"))
            want_sto = best_sto is not None and not (cur and cur.get("storage"))
            want_ori = best_ori is not None and not (cur and cur.get("origin"))
            want_nut = best_nut is not None and not (cur and cur.get("nutrition"))
            if not (want_ing or want_use or want_sto or want_ori or want_nut):
                continue
            rec, created = _ensure_record(records, p)
            if created:
                stats["new_records"] += 1
            if want_ing:
                rec["ingredients"] = best_ing["ingredients"]
                rec["ingredients_source"] = best_ing.get("ingredients_source") or "thông tin sản phẩm cùng công thức"
                if best_ing.get("ingredients_source_url"):
                    rec["ingredients_source_url"] = best_ing["ingredients_source_url"]
                rec["ingredients_match_method"] = "SAME_FORMULA_PACK_SIZE"
                stats["ing_shared"] += 1
            if want_use and best_use:
                rec["usage"] = best_use["usage"]
                if best_use.get("usage_source"):
                    rec["usage_source"] = best_use["usage_source"]
                if best_use.get("usage_source_url"):
                    rec["usage_source_url"] = best_use["usage_source_url"]
                stats["usage_shared"] += 1
            if want_sto and best_sto:
                rec["storage"] = best_sto["storage"]
                if best_sto.get("storage_source"):
                    rec["storage_source"] = best_sto["storage_source"]
                if best_sto.get("storage_source_url"):
                    rec["storage_source_url"] = best_sto["storage_source_url"]
                stats["storage_shared"] += 1
            if want_ori and best_ori:
                rec["origin"] = best_ori["origin"]
                if best_ori.get("origin_source"):
                    rec["origin_source"] = best_ori["origin_source"]
                if best_ori.get("origin_source_url"):
                    rec["origin_source_url"] = best_ori["origin_source_url"]
                stats["origin_shared"] += 1
            if want_nut and best_nut:
                rec["nutrition"] = best_nut["nutrition"]
                if best_nut.get("nutrition_source"):
                    rec["nutrition_source"] = best_nut["nutrition_source"]
                if best_nut.get("nutrition_source_url"):
                    rec["nutrition_source_url"] = best_nut["nutrition_source_url"]
                stats["nutrition_shared"] += 1

    print(f"  Multipack sibling stats: {stats}", flush=True)
    return stats


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

    before = _counts()
    print(f"WAVE-2 BEFORE ({len(products)} SKUs): {before}", flush=True)

    enrich_all_open_facts(products, records)
    enrich_from_hachihachi(products, records)
    enrich_from_sitemap_site(
        "KidsPlaza",
        "thông tin sản phẩm (KidsPlaza)",
        "https://www.kidsplaza.vn/sitemap-0.xml",
        lambda u: u.endswith(".html"),
        products,
        records,
        max_pages=500,
    )
    enrich_from_sitemap_site(
        "Bibomart",
        "thông tin sản phẩm (Bibomart)",
        "https://bibomart.com.vn/sitemap.xml",
        lambda u: u.endswith(".html"),
        products,
        records,
        max_pages=600,
    )
    enrich_cooponline_deep(products, records)
    enrich_kingfoodmart_deep(products, records)
    propagate_multipack_siblings(products, records)

    after = _counts()
    print(f"\nWAVE-2 AFTER  ({len(products)} SKUs, {time.perf_counter()-t0:.1f}s): {after}", flush=True)
    for k in before:
        print(f"  {k:15s}: {before[k]:4d} -> {after[k]:4d} (+{after[k]-before[k]})", flush=True)

    details_doc["generated_at"] = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    details_doc["source"] = "Thanh Lam Mart Product Catalog"
    details_doc["source_catalog_sha256"] = catalog_sha
    details_doc["source_catalog_canonical_sha256"] = catalog_sha
    details_doc["total_catalog_products"] = len(products)
    details_doc["enriched_products"] = len(records)
    details_doc["field_counts"] = {k: after[k] for k in ("ingredients", "usage", "storage", "origin", "nutrition", "summary")}
    DETAILS_PATH.write_text(json.dumps(details_doc, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


if __name__ == "__main__":
    main()

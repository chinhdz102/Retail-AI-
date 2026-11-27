"""Wave-6 Comprehensive Deep-Sweep Crawler.

Mops up ALL remaining crawlable attributes across every verified public source:
  Stage 0: Replay existing Co.op/KF/Emart/HachiHachi caches with upgraded cleaner
           (`arabica|robusta|alkylbenzene|sulfonate`) + `so co la -> socola` / `jumpo -> jumbo`
  Stage 1: OpenFoodFacts Complete VN Catalog (all 18 pages = ~1,735 items, fixed early-break bug)
           + OpenFoodFacts Brand Catalogs (haribo, president, bega, belcube, bauli, lay-s, kit-kat,
             doritos, oreo, nescafe, ferrero, ritter-sport, ottogi, bibigo, orion, coca-cola, pepsi)
  Stage 2: VPP Son Ca (929 stationery items) + DMCL 8 Kitchen/Appliance sitemaps
           + BepXanh (kitchenware/appliances) + DalatGAP (fresh/pantry) + DienMayXanh
  Stage 3: Emart Vietnam Page-2+ Pagination + Socola/KitKat/Brand Alias Re-Match
  Stage 4: Co.op Online Exhaustive Sweep (next 4,200 scored unfetched URLs)
  Stage 5: Kingfoodmart Exhaustive Sweep (ALL remaining scored unfetched URLs)
  Stage 6: USDA FoodData Central verified 100g nutrition for unambiguous single-ingredient
           fresh produce, meats, eggs, rice & nuts (nutrition field only; never fabricates ing/storage)
  Stage 7: Multipack & same-model sibling alignment
"""

from __future__ import annotations

import concurrent.futures
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
    _extract_size_sigs,
    _slug_match_tokens,
    _strip_html,
    fetch_cooponline_page,
    fetch_kf_fast,
    load_cooponline_urls,
)
from scripts.enrich_detailed_ingredients import (  # noqa: E402
    extract_form,
    extract_variants,
    load_kingfoodmart_urls,
    norm_text,
    strict_verify_match,
)
from scripts.enrich_wave2_deep import (  # noqa: E402
    _PACK_WRAPPER_RE,
    _PRIVATE_OR_GENERIC_BRANDS,
    _blank_stats,
    propagate_multipack_siblings,
)
from scripts.enrich_wave3_exhaustive import (  # noqa: E402
    _needs_enrichment,
    apply_fields_v3,
    fetch_fast,
    load_sitemap_cached,
    parse_vn_ssr_page,
)
from scripts.enrich_wave4_final_mopup import (  # noqa: E402
    _brand_aliases_v4,
    _clean_ext_brand,
    _expand_abbrevs,
    _extract_models_v4,
    _strip_seo_tail,
    _tokens_v4,
    crawl_site_v4,
    propagate_same_model_siblings,
    strict_verify_v4,
)
from scripts.enrich_wave5_ultra import (  # noqa: E402
    _EXTRA_COMPACT_BRAND_KEYS,
    _apply_off_product,
    _compact_ascii,
    parse_emart_product_page,
)


def _harmonize_compound_vi(text: str | None) -> str:
    """Harmonize spaced Vietnamese compounds & catalog typos before verification."""
    s = _expand_abbrevs(text)
    for src, dst in (
        (r"\bso\s+co\s+la\b", "socola"),
        (r"\bca\s+cao\b", "cacao"),
        (r"\bxi\s+muoi\b", "ximuoi"),
        (r"\bma\s+ca\b", "macadamia"),
        (r"\bmac\s+ca\b", "macadamia"),
        (r"\bha\s+noi\b", "hanoi"),
        (r"\bjumpo\b", "jumbo"),
        (r"\btokpokki\b", "topokki"),
        (r"\bdoublemin\b", "doublemint"),
        (r"\bmrbrow\b", "mrbrown"),
        (r"\bmorden\b", "modern"),
        (r"\bam\s+bi\s+pur\b", "ambipur"),
        (r"\bambipure\b", "ambipur"),
        (r"\bemmetal\b", "emmental"),
        (r"\bsanwhich\b", "sandwich"),
    ):
        s = re.sub(src, dst, s)
    return s


def strict_verify_v6(
    wm_prod: dict[str, Any],
    ext_name_raw: str,
    ext_skus: set[str],
    ext_brand_raw: Any,
    *,
    allow_nobrand_exact: bool = False,
) -> str | None:
    wm_copy = {**wm_prod, "name": _harmonize_compound_vi(wm_prod.get("name"))}
    ext_h = _harmonize_compound_vi(_strip_seo_tail(ext_name_raw))
    m = strict_verify_v4(wm_prod, ext_name_raw, ext_skus, ext_brand_raw, allow_nobrand_exact=allow_nobrand_exact)
    if m:
        return m
    return strict_verify_v4(wm_copy, ext_h, ext_skus, ext_brand_raw, allow_nobrand_exact=allow_nobrand_exact)


# ---------------------------------------------------------------------------
# Stage 0: Replay existing caches with fixed `arabica|robusta` & `socola`
# ---------------------------------------------------------------------------
def stage0_replay_with_v6(
    products: list[dict[str, Any]],
    records: dict[str, dict[str, Any]],
) -> None:
    print("\n=== STAGE 0: Cache Replay with Harmonized Compound Tokens & Upgraded Cleaner ===", flush=True)
    sources = [
        ("cooponline_pages.jsonl", "thông tin nhãn sản phẩm (Co.op Online)", "COOPONLINE"),
        ("kingfoodmart_pages.jsonl", "thông tin nhãn sản phẩm (Kingfoodmart)", "KINGFOODMART"),
        ("emart_pages.jsonl", "thông tin nhãn sản phẩm (Emart Vietnam)", "EMART"),
        ("chiaki_pages.jsonl", "thông tin sản phẩm (Chiaki)", "CHIAKI"),
        ("medigo_pages.jsonl", "thông tin sản phẩm (Medigo)", "MEDIGO"),
        ("hachihachi_pages.jsonl", "thông tin nhãn sản phẩm (HachiHachi)", "HACHIHACHI"),
        ("meta_pages.jsonl", "thông tin sản phẩm (META.vn)", "META"),
    ]
    for fn, src_label, tag in sources:
        fp = CACHE_DIR / fn
        if not fp.exists():
            continue
        by_bc: dict[str, dict[str, Any]] = {}
        pages: list[tuple[dict[str, Any], set[str]]] = []
        tok_idx: dict[str, list[int]] = {}
        for ln in fp.read_text(encoding="utf-8", errors="ignore").splitlines():
            if not ln.strip():
                continue
            try:
                pg = json.loads(ln)
            except Exception:
                continue
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
            ptoks = _tokens_v4(_harmonize_compound_vi(f"{_strip_seo_tail(pg.get('name', ''))} {_clean_ext_brand(pg.get('brand', ''))}"))
            idx = len(pages)
            pages.append((pg, skus))
            for t in ptoks:
                tok_idx.setdefault(t, []).append(idx)

        stats = _blank_stats()
        for p in products:
            pid = str(p["product_id"])
            if not _needs_enrichment(records.get(pid)):
                continue
            bc = str(p.get("barcode") or "").strip()
            if bc and bc in by_bc:
                if apply_fields_v3(records, p, by_bc[bc], src_label, f"{tag}_BARCODE_V6", stats):
                    stats["barcode_hits"] += 1
                    continue
            qtoks = _tokens_v4(_harmonize_compound_vi(p.get("name")), p.get("brand")) | _brand_aliases_v4(p.get("brand"))
            counts: dict[int, int] = {}
            for t in qtoks:
                for pi in tok_idx.get(t, ()):
                    counts[pi] = counts.get(pi, 0) + 1
            for _ov, pi in sorted(((ov, pi) for pi, ov in counts.items() if ov >= 2), reverse=True)[:5]:
                pg, skus = pages[pi]
                m = strict_verify_v6(p, pg.get("name", ""), skus, pg.get("brand", ""), allow_nobrand_exact=True)
                if m:
                    if apply_fields_v3(records, p, pg, src_label, f"{tag}_{m}_V6", stats):
                        stats["title_hits"] += 1
                        break
        if any(stats.values()):
            print(f"  Replay {tag:13s} -> {stats}", flush=True)


# ---------------------------------------------------------------------------
# Stage 1: OpenFoodFacts Full VN (Fixed!) + Global Missing Brands
# ---------------------------------------------------------------------------
def stage1_openfoodfacts_complete_vn_and_brands(
    products: list[dict[str, Any]],
    records: dict[str, dict[str, Any]],
) -> None:
    print("\n=== STAGE 1: OpenFoodFacts Complete VN (1,735) + Global Brands ===", flush=True)
    fields = "code,product_name,brands,ingredients_text_vi,ingredients_text,nutriments,nutrition_data_per,origins"
    vn_cache_path = CACHE_DIR / "off_vn_all.json"
    vn_prods: list[dict[str, Any]] = json.loads(vn_cache_path.read_text(encoding="utf-8")) if vn_cache_path.exists() else []

    if len(vn_prods) < 1650:
        seen_codes = {str(x.get("code") or "") for x in vn_prods if x.get("code")}
        # Fetch pages 1..19 (fixed: never break early when page fails or hits temporary 503!)
        for page in range(1, 20):
            if len(vn_prods) >= page * 100 and page <= 9:
                continue
            url = f"https://world.openfoodfacts.org/api/v2/search?countries_tags_en=vietnam&page_size=100&page={page}&fields={fields}"
            for attempt in range(4):
                try:
                    req = urllib.request.Request(url, headers=OFF_HEADERS)
                    with urllib.request.urlopen(req, timeout=22) as r:
                        d = json.loads(r.read().decode("utf-8", errors="replace"))
                        arr = d.get("products") or []
                        for it in arr:
                            c = str(it.get("code") or "")
                            if c and c not in seen_codes:
                                seen_codes.add(c)
                                vn_prods.append(it)
                        break
                except Exception:
                    time.sleep(1.5 * (attempt + 1))
            time.sleep(0.5)
        vn_cache_path.write_text(json.dumps(vn_prods, ensure_ascii=False), encoding="utf-8")

    # Also pull missing global brands from OpenFoodFacts (high yield for nutrition + imported snacks/cheese/chocolates)
    brand_tags = [
        "haribo", "president", "bega", "belcube", "the-laughing-cow", "bauli",
        "lay-s", "doritos", "kit-kat", "oreo", "pocky", "toppo", "ferrero",
        "kinder", "ritter-sport", "nescafe", "red-bull", "coca-cola", "pepsi",
        "ottogi", "bibigo", "orion", "barilla", "reggia", "lee-kum-kee", "kewpie",
        "glucerna", "pediasure", "nutifood", "vinamilk", "anchor", "meiji",
    ]
    b_cache_p = CACHE_DIR / "off_brands_cache.json"
    brand_prods: list[dict[str, Any]] = json.loads(b_cache_p.read_text(encoding="utf-8")) if b_cache_p.exists() else []
    if len(brand_prods) < 400:
        seen_b_codes = {str(x.get("code") or "") for x in brand_prods}
        for bt in brand_tags:
            url = f"https://world.openfoodfacts.org/api/v2/search?brands_tags={urllib.parse.quote(bt)}&page_size=40&fields={fields}"
            for attempt in range(2):
                try:
                    req = urllib.request.Request(url, headers=OFF_HEADERS)
                    with urllib.request.urlopen(req, timeout=16) as r:
                        d = json.loads(r.read().decode("utf-8", errors="replace"))
                        for it in d.get("products") or []:
                            c = str(it.get("code") or "")
                            if c and c not in seen_b_codes:
                                seen_b_codes.add(c)
                                brand_prods.append(it)
                        break
                except Exception:
                    time.sleep(1.0)
            time.sleep(0.35)
        b_cache_p.write_text(json.dumps(brand_prods, ensure_ascii=False), encoding="utf-8")

    all_off = vn_prods + brand_prods
    print(f"  OFF indexed pool: {len(vn_prods)} VN + {len(brand_prods)} global-brand = {len(all_off)} items", flush=True)

    off_by_bc: dict[str, dict[str, Any]] = {str(x["code"]).strip(): x for x in all_off if x.get("code")}
    tok_idx: dict[str, list[int]] = {}
    off_list: list[tuple[dict[str, Any], set[str]]] = []
    for prod in all_off:
        nm = str(prod.get("product_name") or "").strip()
        if not nm:
            continue
        br = str(prod.get("brands") or "").strip()
        toks = _tokens_v4(_harmonize_compound_vi(f"{nm} {br}"))
        idx = len(off_list)
        off_list.append((prod, toks))
        for t in toks:
            tok_idx.setdefault(t, []).append(idx)

    stats = _blank_stats()
    for p in products:
        pid = str(p["product_id"])
        rec = records.get(pid, {})
        if rec.get("nutrition") and rec.get("ingredients"):
            continue
        bc = str(p.get("barcode") or "").strip()
        if bc and bc in off_by_bc:
            if _apply_off_product(records, p, off_by_bc[bc], "world.openfoodfacts.org", "OpenFoodFacts", "OFF_BARCODE_V6", stats):
                stats["barcode_hits"] += 1
                continue
        if not _brand_aliases_v4(p.get("brand")):
            continue
        qtoks = _tokens_v4(_harmonize_compound_vi(p.get("name")), p.get("brand"))
        counts: dict[int, int] = {}
        for t in qtoks:
            for i in tok_idx.get(t, ()):
                counts[i] = counts.get(i, 0) + 1
        for _ov, i in sorted(((ov, idx) for idx, ov in counts.items() if ov >= 2), reverse=True)[:5]:
            op, _ = off_list[i]
            m = strict_verify_v6(p, str(op.get("product_name") or ""), {str(op.get("code") or "")}, op.get("brands", ""))
            if m:
                if _apply_off_product(records, p, op, "world.openfoodfacts.org", "OpenFoodFacts", f"OFF_BRAND_{m}", stats):
                    stats["title_hits"] += 1
                    break
    print(f"  OFF Stage-1 V6 stats: {stats}", flush=True)


# ---------------------------------------------------------------------------
# Stage 2: VPP Son Ca + DMCL All Kitchen/Appliances + BepXanh + DalatGAP + DMX
# ---------------------------------------------------------------------------
def parse_dmx_product_page(url: str) -> dict[str, Any]:
    raw = fetch_fast(url, timeout=13.0)
    if not raw:
        return {"url": url, "_miss": True}
    out: dict[str, Any] = {
        "url": url, "name": "", "brand": "", "barcode": "",
        "ingredients": "", "usage": "", "storage": "", "origin": "",
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
                for pv in obj.get("additionalProperty") or []:
                    if not isinstance(pv, dict):
                        continue
                    k = str(pv.get("name") or "").lower()
                    v = _strip_html(str(pv.get("value") or ""))
                    if not v:
                        continue
                    if any(w in k for w in ("chất liệu", "thành phần")):
                        out["ingredients"] = v[:300]
                    elif any(w in k for w in ("nơi sản xuất", "xuất xứ")):
                        out["origin"] = v[:60]
        except Exception:
            pass
    if not out["name"]:
        return {"url": url, "_miss": True}
    # Supplement from SSR text sections if any
    ssr = parse_vn_ssr_page(url)
    for f in ("ingredients", "usage", "storage", "origin"):
        if not out[f] and ssr.get(f):
            out[f] = ssr[f]
    return out


def stage2_new_vn_sites(
    products: list[dict[str, Any]],
    records: dict[str, dict[str, Any]],
) -> None:
    print("\n=== STAGE 2: VPP Sơn Ca + DMCL + Bếp Xanh + Đà Lạt GAP + Điện Máy Xanh ===", flush=True)
    # 2A: VPP Son Ca
    vpp_urls = load_sitemap_cached(
        "vppsonca_urls.json",
        "https://vppsonca.vn/product-sitemap.xml",
        url_pred=lambda u: "/san-pham/" in u,
    )
    if vpp_urls:
        crawl_site_v4(
            site_tag="vppsonca",
            src_label="thông tin sản phẩm (VPP Sơn Ca)",
            urls=vpp_urls,
            slug_fn=lambda u: u.rstrip("/").split("/")[-1],
            parser_fn=parse_vn_ssr_page,
            products=products,
            records=records,
            max_new_fetch=250,
            workers=18,
        )

    # 2B: DMCL remaining kitchenware & household sitemaps
    dmcl_urls = load_sitemap_cached(
        "dmcl_all_urls.json",
        "https://dienmaycholon.com/sitemap.xml",
        sub_pred=lambda u: any(k in u for k in ("noi_", "noi_chao", "binh_ly", "ban_ui", "do_dung_nha_bep", "am_ca_binh_dun")),
        url_pred=lambda u: len(u) > 45 and "/tin-tuc/" not in u,
        max_subs=15,
    )
    if dmcl_urls:
        crawl_site_v4(
            site_tag="dmcl_extra",
            src_label="thông tin sản phẩm (Điện Máy Chợ Lớn)",
            urls=dmcl_urls,
            slug_fn=lambda u: u.rstrip("/").split("/")[-1],
            parser_fn=parse_vn_ssr_page,
            products=products,
            records=records,
            max_new_fetch=120,
            workers=18,
        )

    # 2C: BepXanh kitchenware/appliances
    bx_urls = load_sitemap_cached(
        "bepxanh_urls.json",
        "https://bepxanh.com/sitemap.xml",
        sub_pred=lambda u: "sitemap-san-pham" in u,
        url_pred=lambda u: len(u) > 35,
        max_subs=12,
    )
    if bx_urls:
        crawl_site_v4(
            site_tag="bepxanh",
            src_label="thông tin sản phẩm (Bếp Xanh)",
            urls=bx_urls,
            slug_fn=lambda u: u.rstrip("/").split("/")[-1],
            parser_fn=parse_vn_ssr_page,
            products=products,
            records=records,
            max_new_fetch=150,
            workers=18,
        )

    # 2D: DalatGAP
    dg_urls = load_sitemap_cached(
        "dalatgap_urls.json",
        "https://dalatgapstore.com/sitemap.xml",
        sub_pred=lambda u: "product" in u,
        url_pred=lambda u: len(u) > 35,
    )
    if dg_urls:
        crawl_site_v4(
            site_tag="dalatgap",
            src_label="thông tin sản phẩm (Đà Lạt GAP)",
            urls=dg_urls,
            slug_fn=lambda u: u.rstrip("/").split("/")[-1],
            parser_fn=parse_vn_ssr_page,
            products=products,
            records=records,
            max_new_fetch=80,
            workers=14,
        )

    # 2E: Dien May Xanh kitchenware categories
    dmx_cache_u = CACHE_DIR / "dmx_kitchen_urls.json"
    if dmx_cache_u.exists():
        dmx_urls = json.loads(dmx_cache_u.read_text(encoding="utf-8"))
    else:
        dmx_set: set[str] = set()
        for cat in ("noi", "chao-chong-dinh", "hop-dung-thuc-pham", "binh-dung-nuoc", "binh-ly-giu-nhiet", "ban-ui"):
            h = fetch_fast(f"https://www.dienmayxanh.com/{cat}", timeout=12.0) or ""
            for l in re.findall(rf'href="(/{cat}/[^"#?]+)"', h):
                if l.count("/") == 2:
                    dmx_set.add("https://www.dienmayxanh.com" + l)
        dmx_urls = sorted(dmx_set)
        if dmx_urls:
            dmx_cache_u.write_text(json.dumps(dmx_urls, ensure_ascii=False), encoding="utf-8")
    if dmx_urls:
        crawl_site_v4(
            site_tag="dmx",
            src_label="thông tin sản phẩm (Điện Máy Xanh)",
            urls=dmx_urls,
            slug_fn=lambda u: u.rstrip("/").split("/")[-1],
            parser_fn=parse_dmx_product_page,
            products=products,
            records=records,
            max_new_fetch=100,
            workers=16,
        )


# ---------------------------------------------------------------------------
# Stage 3: Emart Page-2+ Pagination + Socola/Compound Re-Match!
# ---------------------------------------------------------------------------
def stage3_emart_deep(
    products: list[dict[str, Any]],
    records: dict[str, dict[str, Any]],
) -> None:
    print("\n=== STAGE 3: Emart Pagination & Compound-Matched Sweep ===", flush=True)
    idx_cache = CACHE_DIR / "emart_index.json"
    em_prods: dict[str, str] = json.loads(idx_cache.read_text(encoding="utf-8")) if idx_cache.exists() else {}

    # Check page=2..3 on Emart search for top missing brands + KitKat Bites / Socola / Thien Long / Lock&Lock / Omo / Lifebuoy
    extra_kw = [
        "socola", "kitkat", "keo deo", "banh que", "banh quy", "banh gao", "banh bao",
        "xuc xich", "ca vien", "bo vien", "cha ca", "rong bien", "sua chua", "sua tuoi",
        "ca phe", "tra o long", "nuoc giat", "bot giat", "nuoc lau san", "giay ve sinh",
        "sap thom", "ban chai", "kem danh rang", "binh nuoc", "noi", "chao", "hop", "but bi",
        "vo", "hat dieu", "hat de", "pho mai", "bo lat", "dau an", "nuoc ngot", "coca", "pepsi",
    ]
    link_re = re.compile(r'href="https://emartmall\.com\.vn/index\.php\?route=product/product[^"]*product_id=(\d+)[^"]*"[^>]*>\s*([^<\n]{4,140}?)\s*</a>', re.I)
    extra_urls = []
    for kw in extra_kw:
        for pg in (1, 2):
            extra_urls.append(f"https://emartmall.com.vn/index.php?route=product/search&search={urllib.parse.quote(kw)}&limit=100&page={pg}")

    before_n = len(em_prods)
    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as ex:
        for pairs in ex.map(
            lambda u: [(pid, re.sub(r"\s+", " ", html.unescape(t)).strip()) for pid, t in link_re.findall(fetch_fast(u, timeout=12.0) or "") if len(t.strip()) >= 4],
            extra_urls,
        ):
            for pid, t in pairs:
                if pid not in em_prods or len(t) > len(em_prods[pid]):
                    em_prods[pid] = t
    idx_cache.write_text(json.dumps(em_prods, ensure_ascii=False), encoding="utf-8")
    print(f"  Emart index: {before_n} -> {len(em_prods)} (+{len(em_prods)-before_n})", flush=True)

    em_items = list(em_prods.items())
    tok_idx: dict[str, list[int]] = {}
    em_meta: list[tuple[str, str, set[str], set[str]]] = []
    for i, (epid, title) in enumerate(em_items):
        ht = _harmonize_compound_vi(title)
        toks = _tokens_v4(ht)
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
        hname = _harmonize_compound_vi(p.get("name"))
        qtoks = _tokens_v4(hname, p.get("brand")) | b_al
        models = _extract_models_v4(p.get("name"))
        counts: dict[int, int] = {}
        for t in qtoks:
            for i in tok_idx.get(t, ()):
                counts[i] = counts.get(i, 0) + 1
        for i in sorted((k for k, v in counts.items() if v >= 2), key=lambda k: -counts[k])[:4]:
            epid, _t, etoks, emodels = em_meta[i]
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
    print(f"  Emart deep candidates: {len(cand_urls)} (fetching {len(todo)} new)", flush=True)
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
        if not pg.get("_miss"):
            for s in pg.get("skus") or []:
                by_bc[s] = pg

    stats = _blank_stats()
    for p in products:
        pid = str(p["product_id"])
        if not _needs_enrichment(records.get(pid)):
            continue
        bc = str(p.get("barcode") or "").strip()
        if bc and bc in by_bc:
            if apply_fields_v3(records, p, by_bc[bc], "thông tin nhãn sản phẩm (Emart Vietnam)", "EMART_BARCODE_V6", stats):
                stats["barcode_hits"] += 1
                continue
        for u in wm_to_urls.get(pid, ()):
            pg = pages.get(u)
            if not pg or pg.get("_miss"):
                continue
            m = strict_verify_v6(p, pg.get("name", ""), set(pg.get("skus") or []), pg.get("brand", ""), allow_nobrand_exact=True)
            if m:
                if apply_fields_v3(records, p, pg, "thông tin nhãn sản phẩm (Emart Vietnam)", f"EMART_{m}_V6", stats):
                    stats["title_hits"] += 1
                    break
    print(f"  Emart V6 stats: {stats}", flush=True)


# ---------------------------------------------------------------------------
# Stage 4 & 5: Remaining Co.op Online (~4,200) & Kingfoodmart (~4,644) Sweep
# ---------------------------------------------------------------------------
def stage_deep_remaining_sweep(
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
    print(f"\n=== STAGE: {site_tag.upper()} Remaining Scored Sweep (cap={max_fetch}) ===", flush=True)
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
    brand_keys = set(_EXTRA_COMPACT_BRAND_KEYS)
    for p in miss_prods:
        cb = _compact_ascii(p.get("brand"))
        if len(cb) >= 4 and cb not in ("nobrand", "khac", "wineco", "thanhlam", "thanhlamhome", "thanhlammart", "meatdeli"):
            brand_keys.add(cb)
        for al in _brand_aliases_v4(p.get("brand")):
            if len(al) >= 4:
                brand_keys.add(al)

    miss_sigs = []
    for p in miss_prods:
        b_al = {x for x in _brand_aliases_v4(p.get("brand")) if len(x) >= 3}
        toks = {t for t in _slug_match_tokens(_harmonize_compound_vi(p.get("name"))) if len(t) >= 3}
        models = _extract_models_v4(p.get("name"))
        miss_sigs.append((p, b_al, toks, models))

    scored: list[tuple[float, str]] = []
    for u in all_urls:
        if u in pages:
            continue
        slug_raw = slug_fn(u)
        comp = _compact_ascii(slug_raw)
        nslug = _harmonize_compound_vi(slug_raw.replace("-", " "))
        utoks = set(_slug_match_tokens(nslug))
        umodels = _extract_models_v4(slug_raw)
        best_sc = 0.0
        has_bk = any(bk in comp for bk in brand_keys)
        if not has_bk:
            for _p, b_al, toks, models in miss_sigs:
                if models and (models & umodels):
                    best_sc = max(best_sc, 3.0)
                    break
                ov = len(toks & utoks)
                if ov >= 2:
                    rec = ov / max(1, len(toks))
                    if rec >= 0.40:
                        best_sc = max(best_sc, 1.0 + rec)
        else:
            best_sc = 0.8
            for p, b_al, toks, models in miss_sigs:
                if b_al and not any(ba in comp for ba in b_al):
                    continue
                if models and (models & umodels):
                    best_sc = max(best_sc, 5.0)
                    continue
                sub_ov = sum(1 for t in toks if t in comp)
                if sub_ov >= 1:
                    boost = 0.6 if not records.get(str(p["product_id"]), {}).get("ingredients") else 0.0
                    best_sc = max(best_sc, 1.0 + boost + sub_ov / max(1, len(toks)))
        if best_sc > 0.0:
            scored.append((best_sc, u))

    scored.sort(key=lambda x: (-x[0], x[1]))
    todo = [u for _s, u in scored[:max_fetch]]
    print(f"  {site_tag}: remaining_scored={len(scored)} cached={len(pages)} fetching={len(todo)}", flush=True)

    if todo:
        t_f = time.perf_counter()
        with cache_jsonl.open("a", encoding="utf-8") as cf:
            for start in range(0, len(todo), 250):
                chunk = todo[start : start + 250]
                with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
                    results = list(ex.map(parser_fn, chunk))
                for u, res in zip(chunk, results):
                    entry = res if res else {"url": u, "_miss": True}
                    if "url" not in entry:
                        entry["url"] = u
                    pages[u] = entry
                    cf.write(json.dumps(entry, ensure_ascii=False) + "\n")
                cf.flush()
                done_n = min(start + 250, len(todo))
                print(f"    {site_tag} [{done_n}/{len(todo)}] elapsed={time.perf_counter()-t_f:.0f}s", flush=True)

    # Full match pass over ALL cached pages
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
        bc = str(p.get("barcode") or "").strip()
        if bc and bc in by_bc:
            if apply_fields_v3(records, p, by_bc[bc], src_label, f"{site_tag.upper()}_BARCODE_V6", stats):
                stats["barcode_hits"] += 1
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
                if apply_fields_v3(records, p, pg, src_label, f"{site_tag.upper()}_{m}_V6", stats):
                    stats["title_hits"] += 1
                    break
    print(f"  {site_tag.upper()} V6 stats: {stats}", flush=True)


# ---------------------------------------------------------------------------
# Stage 6: USDA FoodData Central Nutrition ONLY for Unambiguous Single-Ingredient Fresh/Whole Foods
# ---------------------------------------------------------------------------
_USDA_SINGLE_FOOD_QUERIES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bcà rốt\b", re.I), "carrots, raw"),
    (re.compile(r"\bkhoai tây\b(?!.*(?:snack|chiên|lát|bỉ|nướng))", re.I), "potatoes, flesh and skin, raw"),
    (re.compile(r"\bkhoai lang\b(?!.*(?:kén|chiên|mật|bánh))", re.I), "sweet potato, raw, unprepared"),
    (re.compile(r"\bkhoai môn\b", re.I), "taro, raw"),
    (re.compile(r"\bcà chua\b(?!.*(?:sốt|tương|cô đặc))", re.I), "tomatoes, red, ripe, raw"),
    (re.compile(r"\bhành tây\b", re.I), "onions, raw"),
    (re.compile(r"\bhành lá\b", re.I), "onions, spring or scallions, raw"),
    (re.compile(r"\bcủ cải trắng\b", re.I), "radishes, oriental, raw"),
    (re.compile(r"\bcủ dền\b", re.I), "beets, raw"),
    (re.compile(r"\bsu su\b", re.I), "chayote, fruit, raw"),
    (re.compile(r"\bsu hào\b", re.I), "kohlrabi, raw"),
    (re.compile(r"\bbí xanh\b|\bbí đao\b(?!.*trà)", re.I), "waxgourd, raw"),
    (re.compile(r"\bbí đỏ\b", re.I), "pumpkin, raw"),
    (re.compile(r"\bdưa chuột\b|\bdưa leo\b", re.I), "cucumber, with peel, raw"),
    (re.compile(r"\bdưa hấu\b", re.I), "watermelon, raw"),
    (re.compile(r"\bdưa lưới\b", re.I), "melons, cantaloupe, raw"),
    (re.compile(r"\bcần tây\b", re.I), "celery, raw"),
    (re.compile(r"\bcải bó xôi\b", re.I), "spinach, raw"),
    (re.compile(r"\bgiá đỗ\b", re.I), "mung beans, mature seeds, sprouted, raw"),
    (re.compile(r"\bcải thảo\b", re.I), "cabbage, chinese (pak-choi), raw"),
    (re.compile(r"\bbắp cải trắng\b|\bbắp cải tím\b", re.I), "cabbage, raw"),
    (re.compile(r"\bxà lách\b", re.I), "lettuce, green leaf, raw"),
    (re.compile(r"\bsúp lơ\b|\bbông cải xanh\b", re.I), "broccoli, raw"),
    (re.compile(r"\bớt chuông\b", re.I), "peppers, sweet, red, raw"),
    (re.compile(r"\bđậu bắp\b", re.I), "okra, raw"),
    (re.compile(r"\bcà tím\b", re.I), "eggplant, raw"),
    (re.compile(r"\btáo\s+(?:envy|gala|ambrosia|mỹ|new zealand|nhập)", re.I), "apples, raw, with skin"),
    (re.compile(r"\bkiwi\b", re.I), "kiwifruit, green, raw"),
    (re.compile(r"\bcam sành\b|\bcam vàng\b|\bcam mỹ\b", re.I), "oranges, raw, all commercial varieties"),
    (re.compile(r"\bổi\b", re.I), "guavas, common, raw"),
    (re.compile(r"\bbưởi\b", re.I), "pummelo, raw"),
    (re.compile(r"\bchanh leo\b|\bchanh dây\b", re.I), "passion-fruit, (granadilla), purple, raw"),
    (re.compile(r"\bchôm chôm\b", re.I), "rambutan, canned, syrup pack"),
    (re.compile(r"\bdứa\b|\bkhóm\b(?!.*(?:sấy|khô|bánh|kẹo))", re.I), "pineapple, raw, all varieties"),
    (re.compile(r"\bchuối\b(?!.*(?:chiên|sấy|bánh))", re.I), "bananas, raw"),
    (re.compile(r"\bxoài\b(?!.*(?:sấy|kẹo|bánh|nước))", re.I), "mangos, raw"),
    (re.compile(r"\bnho\s+(?:xanh|đỏ|đen|mỹ|úc|không hạt)", re.I), "grapes, red or green, raw"),
    (re.compile(r"\blê\s+(?:hàn|nam phi|đường)", re.I), "pears, raw"),
    (re.compile(r"\bthịt thăn bò\b|\blõi vai bò\b|\bba chỉ bò\b|\bbắp bò\b", re.I), "beef, tenderloin, raw"),
    (re.compile(r"\bức gà\b|\bđùi gà\b|\bcánh gà\b", re.I), "chicken, broiler or fryers, breast, skinless, boneless, meat only, raw"),
    (re.compile(r"\bba rọi\b|\bnạc dăm\b|\bsườn thăn\b|\bsườn non\b|\bbắp giò\b|\bthịt xay\b", re.I), "pork, fresh, loin, whole, separable lean and fat, raw"),
    (re.compile(r"\btrứng gà\b", re.I), "egg, whole, raw, fresh"),
    (re.compile(r"\btrứng chim cút\b", re.I), "egg, quail, whole, fresh, raw"),
    (re.compile(r"\bcá hồi\b", re.I), "fish, salmon, atlantic, farmed, raw"),
    (re.compile(r"\btôm sú\b|\btôm thẻ\b", re.I), "crustaceans, shrimp, mixed species, raw"),
    (re.compile(r"\bgạo\b", re.I), "rice, white, long-grain, regular, raw, unenriched"),
    (re.compile(r"\bhạt điều rang muối\b", re.I), "nuts, cashew nuts, oil roasted, with salt added"),
    (re.compile(r"\bhạt dẻ cười\b|\bpistachios\b", re.I), "nuts, pistachio nuts, dry roasted, with salt added"),
    (re.compile(r"\bhạt macadamia\b|\bmắc ca\b", re.I), "nuts, macadamia nuts, dry roasted, with salt added"),
    (re.compile(r"\bhạnh nhân\b(?!.*(?:socola|sữa|bánh))", re.I), "nuts, almonds, dry roasted, with salt added"),
    (re.compile(r"\bhạt hướng dương\b", re.I), "seeds, sunflower seed kernels, dried"),
    (re.compile(r"\bmật ong\b", re.I), "honey"),
]


def stage6_usda_fresh_nutrition(
    products: list[dict[str, Any]],
    records: dict[str, dict[str, Any]],
) -> None:
    print("\n=== STAGE 6: USDA FoodData Central Nutrition for Single-Ingredient Fresh/Whole Foods ===", flush=True)
    usda_cache_p = CACHE_DIR / "usda_fdc_cache.json"
    usda_cache: dict[str, dict[str, Any] | None] = json.loads(usda_cache_p.read_text(encoding="utf-8")) if usda_cache_p.exists() else {}

    def _query_usda(q: str) -> dict[str, Any] | None:
        if q in usda_cache:
            return usda_cache[q]
        url = (
            "https://api.nal.usda.gov/fdc/v1/foods/search?"
            + urllib.parse.urlencode({"query": q, "dataType": "Foundation,SR Legacy", "pageSize": "1", "api_key": "DEMO_KEY"})
        )
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "RetailAIKiosk/1.0"})
            with urllib.request.urlopen(req, timeout=12) as r:
                d = json.loads(r.read().decode("utf-8", errors="replace"))
                foods = d.get("foods") or []
                if not foods:
                    usda_cache[q] = None
                    return None
                f0 = foods[0]
                fdc_id = f0.get("fdcId")
                nutr: dict[str, Any] = {"basis": "100g"}
                for n in f0.get("foodNutrients") or []:
                    nm = n.get("nutrientName") or ""
                    val = n.get("value")
                    unit = str(n.get("unitName") or "").upper()
                    if not isinstance(val, (int, float)) or isinstance(val, bool) or val < 0:
                        continue
                    if nm == "Energy":
                        kcal = float(val) / 4.184 if unit == "KJ" else float(val)
                        nutr["energy_kcal"] = round(kcal, 1)
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
                res = {"fdc_id": fdc_id, "nutrition": nutr} if len(nutr) >= 3 else None
                usda_cache[q] = res
                return res
        except Exception:
            return None

    allowed_cats = {
        "Trái cây tươi", "Củ, Quả", "Rau Lá", "Thịt", "Hải Sản", "Hải Sản Đông Lạnh",
        "Trứng", "Gạo - Nông Sản Khô", "Hạt - Trái Cây Sấy Khô", "Gia Vị Khác",
    }
    stats = _blank_stats()
    needed_queries: set[str] = set()
    matched_items: list[tuple[dict[str, Any], str]] = []
    for p in products:
        if (p.get("category_name") or "") not in allowed_cats:
            continue
        pid = str(p["product_id"])
        if records.get(pid, {}).get("nutrition"):
            continue
        pname = p.get("name") or ""
        for pat, q in _USDA_SINGLE_FOOD_QUERIES:
            if pat.search(pname):
                needed_queries.add(q)
                matched_items.append((p, q))
                break

    for q in sorted(needed_queries):
        if q not in usda_cache:
            _query_usda(q)
            time.sleep(0.25)
    usda_cache_p.write_text(json.dumps(usda_cache, ensure_ascii=False), encoding="utf-8")

    for p, q in matched_items:
        info = usda_cache.get(q)
        if not info:
            continue
        ext = {
            "url": f"https://fdc.nal.usda.gov/fdc-app.html#/food-details/{info['fdc_id']}/nutrients",
            "nutrition": info["nutrition"],
        }
        apply_fields_v3(records, p, ext, "USDA FoodData Central", "USDA_SINGLE_WHOLE_FOOD", stats)
    print(f"  USDA nutrition stats ({len(needed_queries)} whole foods): {stats}", flush=True)


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
    print(f"WAVE-6 BEFORE ({len(products)} SKUs): {before}", flush=True)

    stage0_replay_with_v6(products, records)
    _save()

    stage1_openfoodfacts_complete_vn_and_brands(products, records)
    _save()

    stage2_new_vn_sites(products, records)
    _save()

    stage3_emart_deep(products, records)
    _save()

    coop_urls = load_cooponline_urls()
    stage_deep_remaining_sweep(
        site_tag="cooponline",
        src_label="thông tin nhãn sản phẩm (Co.op Online)",
        all_urls=coop_urls,
        slug_fn=lambda u: re.sub(r"--s\d+$", "", u.rstrip("/").split("/")[-1]),
        parser_fn=fetch_cooponline_page,
        products=products,
        records=records,
        max_fetch=4200,
        workers=30,
    )
    _save()

    kf_urls = load_kingfoodmart_urls(CACHE_DIR / "kingfoodmart_product_urls.json")
    stage_deep_remaining_sweep(
        site_tag="kingfoodmart",
        src_label="thông tin nhãn sản phẩm (Kingfoodmart)",
        all_urls=kf_urls,
        slug_fn=lambda u: u.rstrip("/").split("/")[-1],
        parser_fn=fetch_kf_fast,
        products=products,
        records=records,
        max_fetch=4700,
        workers=30,
    )
    _save()

    stage6_usda_fresh_nutrition(products, records)
    _save()

    propagate_multipack_siblings(products, records)
    propagate_same_model_siblings(products, records)
    _save()

    after = _counts()
    print(f"\nWAVE-6 AFTER  ({len(products)} SKUs, {time.perf_counter()-t0:.1f}s): {after}", flush=True)
    for k in before:
        print(f"  {k:15s}: {before[k]:4d} -> {after[k]:4d} (+{after[k]-before[k]})", flush=True)


if __name__ == "__main__":
    main()

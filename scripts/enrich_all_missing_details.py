"""Resumable multi-source product knowledge crawler (OpenFoodFacts + Co.op Online + Long Chau + Kingfoodmart).

Fills missing or generic/blurb product attributes in ``data/thanh_lam_product_details.json``:
  - ``ingredients`` (thành phần chính / chất liệu)
  - ``usage`` (hướng dẫn sử dụng)
  - ``storage`` (hướng dẫn bảo quản)
  - ``origin`` (xuất xứ)
  - ``nutrition`` (structured per-100g/100ml nutrition facts: energy_kcal, sugar_g, carbs_g, protein_g, fat_g, salt_g, sodium_mg, basis)
  - ``summary`` (concise product summary)

Only accesses public sitemaps, public OpenFoodFacts REST v2 API, and public SSR HTML pages
(zero CAPTCHA or anti-bot bypass; polite concurrency with resumable JSONL caches).
"""

from __future__ import annotations

import concurrent.futures
import hashlib
import html
import json
import re
import sys
import threading
import time
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.enrich_detailed_ingredients import (  # noqa: E402
    content_tokens,
    extract_form,
    extract_variants,
    fetch_kingfoodmart_product,
    ingredient_specificity_score,
    is_clean_label_ingredients,
    load_kingfoodmart_urls,
    norm_text,
    strict_verify_match,
)

_HTTP_CLIENT_LOCK = threading.Lock()
_HTTP_CLIENT: httpx.Client | None = None


def _get_http_client() -> httpx.Client:
    global _HTTP_CLIENT
    if _HTTP_CLIENT is None:
        with _HTTP_CLIENT_LOCK:
            if _HTTP_CLIENT is None:
                _HTTP_CLIENT = httpx.Client(
                    timeout=httpx.Timeout(12.0, connect=6.0),
                    follow_redirects=True,
                    limits=httpx.Limits(max_connections=48, max_keepalive_connections=40),
                    headers={
                        "User-Agent": (
                            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
                        ),
                        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                        "Accept-Language": "vi-VN,vi;q=0.9,en;q=0.8",
                        "Accept-Encoding": "gzip, deflate",
                    },
                )
    return _HTTP_CLIENT


def fetch_text(url: str, timeout: int = 12) -> str | None:
    try:
        r = _get_http_client().get(url, timeout=float(timeout))
        if r.status_code == 200:
            return r.text
    except Exception:
        return None
    return None


def fetch_kf_fast(url: str) -> dict[str, Any] | None:
    raw = fetch_text(url, timeout=10)
    if not raw:
        return None
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', raw, re.S)
    if not m:
        return None
    try:
        nd = json.loads(m.group(1))
    except Exception:
        return None
    data = nd.get("props", {}).get("pageProps", {}).get("data")
    if not isinstance(data, dict):
        return None
    dj = data.get("descriptionJson")
    if not isinstance(dj, dict):
        desc_raw = data.get("description")
        if isinstance(desc_raw, str) and desc_raw.strip().startswith("{"):
            try:
                dj = json.loads(desc_raw)
            except Exception:
                dj = {}
        else:
            dj = {}
    skus = set()
    variant_names: list[str] = []
    for v in data.get("variants") or []:
        if isinstance(v, dict):
            sku = str(v.get("sku") or "").strip()
            if sku:
                skus.add(sku)
            vn = str(v.get("name") or "").strip()
            if vn:
                variant_names.append(vn)
    ing = _strip_html(dj.get("ingredients")) if isinstance(dj, dict) else ""
    usages = _strip_html(dj.get("usages") or dj.get("uses")) if isinstance(dj, dict) else ""
    storage = _strip_html(dj.get("preservation") or dj.get("storage") or dj.get("storageInstructions")) if isinstance(dj, dict) else ""
    if not storage and isinstance(dj, dict):
        for cand_blob in (dj.get("uses"), dj.get("introduction"), dj.get("highlights"), dj.get("exp")):
            if isinstance(cand_blob, str) and "bảo quản" in cand_blob.casefold():
                plain_b = _strip_html(cand_blob)
                m_st = re.search(
                    r"((?<!chất\s)(?<!không\s)bảo quản\s*(?::|nơi|lạnh|trong|ở|nhiệt|tủ|đậy|tránh)[^.\n]{8,220}(?:\.[^.\n]{5,120})?)",
                    plain_b,
                    re.IGNORECASE,
                )
                if m_st:
                    storage = m_st.group(1).strip()
                    break
    origin = str(dj.get("source") or "").strip() if isinstance(dj, dict) else ""
    brand = str(dj.get("brand") or "").strip() if isinstance(dj, dict) else ""
    intro = _strip_html(dj.get("introduction") or dj.get("highlights")) if isinstance(dj, dict) else ""
    name = str(data.get("name") or (variant_names[0] if variant_names else "")).strip()
    return {
        "url": url, "name": name, "skus": sorted(skus), "brand": brand,
        "ingredients": ing, "usage": usages, "storage": storage, "origin": origin, "summary": intro,
    }

CATALOG_PATH = ROOT / "data" / "thanh_lam_products.json"
DETAILS_PATH = ROOT / "data" / "thanh_lam_product_details.json"
CACHE_DIR = ROOT / ".cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

OFF_HEADERS = {
    "User-Agent": "ThanhLamMartCatalogEnricher/2.0 (https://github.com/chinhdz102/Retail-AI-)",
    "Accept": "application/json",
}
WEB_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "vi-VN,vi;q=0.9,en;q=0.8",
}

_PLACEHOLDER_VAL_RE = re.compile(
    r"^(?:đang cập nhật|xem trên bao bì|in trên bao bì|theo bao bì|không có|chưa có|n/a|null|-)$",
    re.IGNORECASE,
)


def _strip_html(raw: Any) -> str:
    if not isinstance(raw, str) or not raw.strip():
        return ""
    s = re.sub(r"</?(?:p|div|br|li|tr|h[1-6]|ul|ol|table)[^>]*>", ". ", raw, flags=re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    s = html.unescape(s)
    s = re.sub(r"\s+", " ", s).strip(" .")
    s = re.sub(r"\.\s*\.", ".", s)
    return s


def _is_valid_text(val: str | None, min_len: int = 4) -> bool:
    if not val or not isinstance(val, str):
        return False
    s = val.strip()
    if len(s) < min_len or _PLACEHOLDER_VAL_RE.match(s):
        return False
    return True


def _ensure_record(
    records: dict[str, dict[str, Any]],
    prod: dict[str, Any],
) -> tuple[dict[str, Any], bool]:
    pid = str(prod["product_id"])
    if pid in records:
        return records[pid], False
    records[pid] = {
        "product_name": str(prod.get("name") or ""),
        "brand": str(prod.get("brand") or ""),
        "barcode": str(prod.get("barcode") or ""),
        "source_url": str(prod.get("seo_url") or ""),
    }
    return records[pid], True


def _extract_inline_nutrition(text: str) -> dict[str, Any] | None:
    """Extract explicit Vietnamese/English per-100g/100ml nutrition facts from product description."""
    if not text or len(text) < 20:
        return None
    nutr: dict[str, Any] = {}
    m_basis = re.search(
        r"(?:trong|trên|per|/)\s*(100\s*(?:ml|g|gam)|1\s*(?:hộp|chai|lon|gói|khẩu phần)\s*\(?\d+\s*(?:ml|g)\)?) ",
        text,
        re.I,
    )
    basis = m_basis.group(1).strip() if m_basis else "100ml/100g"

    def _find_num(pattern: str) -> float | None:
        m = re.search(pattern, text, re.I)
        if not m:
            return None
        try:
            return round(float(m.group(1).replace(",", ".")), 2)
        except ValueError:
            return None

    kcal = _find_num(r"(?:năng lượng|energy|calo|calories)\s*[:：\-]?\s*(\d+(?:[.,]\d+)?)\s*kcal")
    sugar = _find_num(r"(?:hàm lượng đường|tổng lượng đường|lượng đường|đường tổng|đường|sugars?)\s*[:：\-]?\s*(\d+(?:[.,]\d+)?)\s*(?:g|gam)\b")
    carbs = _find_num(r"(?:carbohydrate|carbohydrat|hydrat cacbon|tinh bột)\s*[:：\-]?\s*(\d+(?:[.,]\d+)?)\s*(?:g|gam)\b")
    protein = _find_num(r"(?:chất đạm|đạm|protein)\s*[:：\-]?\s*(\d+(?:[.,]\d+)?)\s*(?:g|gam)\b")
    fat = _find_num(r"(?:chất béo|béo tổng|fat)\s*[:：\-]?\s*(\d+(?:[.,]\d+)?)\s*(?:g|gam)\b")
    sodium = _find_num(r"(?:natri|sodium)\s*[:：\-]?\s*(\d+(?:[.,]\d+)?)\s*mg\b")

    if kcal is not None and 0 <= kcal <= 1200:
        nutr["energy_kcal"] = kcal
    if sugar is not None and 0 <= sugar <= 100:
        nutr["sugar_g"] = sugar
    if carbs is not None and 0 <= carbs <= 100:
        nutr["carbs_g"] = carbs
    if protein is not None and 0 <= protein <= 100:
        nutr["protein_g"] = protein
    if fat is not None and 0 <= fat <= 100:
        nutr["fat_g"] = fat
    if sodium is not None and 0 <= sodium <= 10000:
        nutr["sodium_mg"] = sodium

    if len(nutr) >= 2 or ("sugar_g" in nutr) or ("energy_kcal" in nutr):
        nutr["basis"] = basis
        return nutr
    return None


# ---------------------------------------------------------------------------
# Stage 1: OpenFoodFacts API v2 (ALL 3,099 barcodes -> nutrition + ingredients + origin)
# ---------------------------------------------------------------------------
def enrich_from_openfoodfacts(
    products: list[dict[str, Any]],
    records: dict[str, dict[str, Any]],
) -> dict[str, int]:
    print("\n=== STAGE 1: OpenFoodFacts Public API v2 (ALL 3,099 barcodes) ===", flush=True)
    by_barcode: dict[str, list[dict[str, Any]]] = {}
    for p in products:
        bc = str(p.get("barcode") or "").strip()
        if re.fullmatch(r"\d{8,14}", bc):
            by_barcode.setdefault(bc, []).append(p)

    barcodes = sorted(by_barcode.keys())
    stats = {"off_matched": 0, "nutrition_added": 0, "ing_added": 0, "ing_upgraded": 0, "origin_added": 0, "new_records": 0}
    fields = (
        "code,product_name,brands,ingredients_text_vi,ingredients_text,"
        "nutriments,nutrition_data_per,serving_size,origins,countries"
    )

    def _fetch_batch(batch: list[str]) -> list[dict[str, Any]]:
        qs = urllib.parse.urlencode({"code": ",".join(batch), "fields": fields, "page_size": str(len(batch))})
        url = f"https://world.openfoodfacts.org/api/v2/search?{qs}"
        for attempt in range(3):
            try:
                req = urllib.request.Request(url, headers=OFF_HEADERS)
                with urllib.request.urlopen(req, timeout=20) as r:
                    if r.status == 200:
                        data = json.loads(r.read().decode("utf-8", errors="replace"))
                        return data.get("products") or []
            except Exception:
                time.sleep(0.8 * (attempt + 1))
        return []

    batches = [barcodes[i : i + 80] for i in range(0, len(barcodes), 80)]
    all_off_prods: list[dict[str, Any]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as ex:
        for res in ex.map(_fetch_batch, batches):
            all_off_prods.extend(res)

    for prod in all_off_prods:
        code = str(prod.get("code") or "").strip()
        if code not in by_barcode:
            continue
        stats["off_matched"] += 1
        src_url = f"https://world.openfoodfacts.org/product/{code}"

        # 1. Structured nutrition facts
        nutr_raw = prod.get("nutriments") or {}
        nutr: dict[str, Any] = {}
        if isinstance(nutr_raw, dict):
            def _num(key: str) -> float | None:
                v = nutr_raw.get(key)
                if isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0:
                    return round(float(v), 2)
                return None

            kcal = _num("energy-kcal_100g")
            if kcal is None:
                kj = _num("energy-kj_100g") or _num("energy_100g")
                if kj is not None and kj > 0:
                    kcal = round(kj / 4.184, 1)
            sugar = _num("sugars_100g")
            carbs = _num("carbohydrates_100g")
            prot = _num("proteins_100g")
            fat = _num("fat_100g")
            sat_fat = _num("saturated-fat_100g")
            fiber = _num("fiber_100g")
            salt = _num("salt_100g")
            sodium_g = _num("sodium_100g")

            if kcal is not None and kcal <= 1000:
                nutr["energy_kcal"] = kcal
            if sugar is not None and sugar <= 100:
                nutr["sugar_g"] = sugar
            if carbs is not None and carbs <= 100:
                nutr["carbs_g"] = carbs
            if prot is not None and prot <= 100:
                nutr["protein_g"] = prot
            if fat is not None and fat <= 100:
                nutr["fat_g"] = fat
            if sat_fat is not None and sat_fat <= 100:
                nutr["saturated_fat_g"] = sat_fat
            if fiber is not None and fiber <= 100:
                nutr["fiber_g"] = fiber
            if sodium_g is not None and sodium_g <= 40:
                nutr["sodium_mg"] = round(sodium_g * 1000.0, 1)
            elif salt is not None and salt <= 100:
                nutr["salt_g"] = salt

            if nutr:
                per = str(prod.get("nutrition_data_per") or "100g").strip()
                nutr["basis"] = "100ml" if "ml" in per.lower() else "100g"
                ss = str(prod.get("serving_size") or "").strip()
                if ss and len(ss) <= 40:
                    nutr["serving_size"] = ss

        # 2. Ingredients (prefer Vietnamese; fall back to English only if target has no ingredients)
        ing_vi = str(prod.get("ingredients_text_vi") or "").strip()
        ing_any = str(prod.get("ingredients_text") or "").strip()
        cand_ing = ing_vi if len(ing_vi) >= 15 else ing_any
        cand_ing = re.sub(r"\s+", " ", cand_ing).strip()
        if len(cand_ing) > 550:
            cand_ing = cand_ing[:547].rsplit(" ", 1)[0] + "."

        # 3. Origin
        orig_raw = str(prod.get("origins") or "").strip()
        if orig_raw.lower() in ("vietnam", "việt nam", "en:vietnam"):
            orig_raw = "Việt Nam"
        elif orig_raw.startswith("en:"):
            orig_raw = orig_raw[3:].replace("-", " ").title()

        for p in by_barcode[code]:
            pid = str(p["product_id"])
            cur = records.get(pid)
            cur_ing = cur.get("ingredients") if cur else None
            cur_score = ingredient_specificity_score(cur_ing)

            want_nutr = bool(nutr) and not (cur and cur.get("nutrition"))
            # Adjust basis to 100ml for beverages
            if want_nutr:
                pname_l = (p.get("name") or "").lower()
                if any(w in pname_l for w in ("ml", "lít", "lit", "nước", "sữa tươi", "trà ", "bia ", "cà phê sữa")):
                    nutr = {**nutr, "basis": "100ml"}

            want_ing = (
                is_clean_label_ingredients(cand_ing)
                and (
                    (bool(ing_vi) and ingredient_specificity_score(cand_ing) > cur_score)
                    or (not cur_ing and cur_score == 0)
                )
            )
            want_orig = bool(orig_raw) and len(orig_raw) >= 3 and not (cur and cur.get("origin"))

            if not (want_nutr or want_ing or want_orig):
                continue

            rec, created = _ensure_record(records, p)
            if created:
                stats["new_records"] += 1
            if want_nutr:
                rec["nutrition"] = nutr
                rec["nutrition_source"] = "OpenFoodFacts"
                rec["nutrition_source_url"] = src_url
                stats["nutrition_added"] += 1
            if want_ing:
                if cur_ing:
                    stats["ing_upgraded"] += 1
                else:
                    stats["ing_added"] += 1
                rec["ingredients"] = cand_ing
                rec["ingredients_source"] = "OpenFoodFacts"
                rec["ingredients_source_url"] = src_url
                rec["ingredients_match_method"] = "OPENFOODFACTS_BARCODE"
            if want_orig:
                rec["origin"] = orig_raw
                rec["origin_source"] = "OpenFoodFacts"
                rec["origin_source_url"] = src_url
                stats["origin_added"] += 1

    print(f"  OpenFoodFacts stats: {stats}", flush=True)
    return stats


# ---------------------------------------------------------------------------
# Stage 2: Co.op Online (18,092 products across 12 sitemaps, structured JSON-LD + barcode!)
# ---------------------------------------------------------------------------
def load_cooponline_urls() -> list[str]:
    cache_p = CACHE_DIR / "cooponline_urls.json"
    if cache_p.exists():
        urls = json.loads(cache_p.read_text(encoding="utf-8"))
        if len(urls) > 10000:
            return urls
    idx_xml = fetch_text("https://cooponline.vn/sitemap-collection-products.xml", timeout=15)
    sub_sms = re.findall(r"<loc>(.*?)</loc>", idx_xml or "")
    urls: set[str] = set()

    def _fetch_sub(u: str) -> list[str]:
        xml = fetch_text(u, timeout=20)
        return re.findall(r"<loc>(.*?)</loc>", xml) if xml else []

    with concurrent.futures.ThreadPoolExecutor(max_workers=12) as ex:
        for res in ex.map(_fetch_sub, sub_sms):
            urls.update(res)
    out = sorted(urls)
    cache_p.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    return out


def fetch_cooponline_page(url: str) -> dict[str, Any] | None:
    raw_html = fetch_text(url, timeout=14)
    if not raw_html:
        return None
    out: dict[str, Any] = {
        "url": url,
        "name": "",
        "brand": "",
        "barcode": "",
        "ingredients": "",
        "usage": "",
        "storage": "",
        "origin": "",
        "summary": "",
        "nutrition": None,
    }
    props: dict[str, str] = {}

    # Parse __NEXT_DATA__ (contains exact barcode + structured attributes + description)
    m_nd = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', raw_html, re.S)
    if m_nd:
        try:
            nd = json.loads(m_nd.group(1))
            sp = nd.get("props", {}).get("pageProps", {}).get("serverProduct") or {}
            prod = sp.get("product") or {}
            pinfo = prod.get("productInfo") or {}
            pdet = prod.get("productDetail") or {}

            out["name"] = str(pinfo.get("name") or pinfo.get("productName") or "").strip()
            brands = pinfo.get("brands") or []
            if isinstance(brands, list) and brands and isinstance(brands[0], dict):
                out["brand"] = str(brands[0].get("name") or "").strip()
            elif isinstance(pinfo.get("brand"), dict):
                out["brand"] = str(pinfo["brand"].get("name") or "").strip()

            bc = str(pdet.get("barcode") or pinfo.get("barcode") or "").strip()
            if re.fullmatch(r"\d{8,14}", bc):
                out["barcode"] = bc

            for attr_list_key in ("attributes", "attributeGroups"):
                raw_attrs = pdet.get(attr_list_key) or []
                if isinstance(raw_attrs, list):
                    for a in raw_attrs:
                        if not isinstance(a, dict):
                            continue
                        if "attributes" in a and isinstance(a["attributes"], list):
                            for sub in a["attributes"]:
                                if isinstance(sub, dict) and sub.get("name") and sub.get("value"):
                                    props[str(sub["name"]).strip().lower()] = _strip_html(str(sub["value"]))
                        elif a.get("name") and a.get("value"):
                            props[str(a["name"]).strip().lower()] = _strip_html(str(a["value"]))

            desc_html = pdet.get("description") or ""
            short_html = pdet.get("shortDescription") or ""
            desc_plain = _strip_html(desc_html)
            short_plain = _strip_html(short_html)
            if len(desc_plain) >= 25:
                out["summary"] = desc_plain[:500]
            elif len(short_plain) >= 25:
                out["summary"] = short_plain[:500]

            # Try extracting inline nutrition from description
            nutr = _extract_inline_nutrition(desc_plain)
            if nutr:
                out["nutrition"] = nutr
        except Exception:
            pass

    # Also parse JSON-LD Product schema (super reliable fallback for additionalProperty!)
    for m in re.finditer(r'<script[^>]*application/ld\+json[^>]*>(.*?)</script>', raw_html, re.S):
        try:
            obj = json.loads(m.group(1))
            if isinstance(obj, dict) and obj.get("@type") == "Product":
                if not out["name"]:
                    out["name"] = str(obj.get("name") or "").strip()
                if not out["brand"]:
                    b = obj.get("brand")
                    if isinstance(b, dict):
                        out["brand"] = str(b.get("name") or "").strip()
                    elif isinstance(b, str):
                        out["brand"] = b.strip()
                for pv in obj.get("additionalProperty") or []:
                    if isinstance(pv, dict) and pv.get("name") and pv.get("value"):
                        k = str(pv["name"]).strip().lower()
                        v = _strip_html(str(pv["value"]))
                        if k not in props and _is_valid_text(v, 2):
                            props[k] = v
                if not out["summary"] and obj.get("description"):
                    out["summary"] = _strip_html(str(obj["description"]))[:500]
        except Exception:
            continue

    # Map structured properties
    for k in ("thành phần", "thành phần chính", "nguyên liệu", "chất liệu"):
        if _is_valid_text(props.get(k), 6):
            out["ingredients"] = props[k]
            break
    for k in ("cách sử dụng", "hướng dẫn sử dụng", "cách dùng", "hướng dẫn chế biến"):
        if _is_valid_text(props.get(k), 6):
            out["usage"] = props[k]
            break
    for k in ("bảo quản", "hướng dẫn bảo quản", "cách bảo quản"):
        if _is_valid_text(props.get(k), 8):
            out["storage"] = props[k]
            break
    for k in ("xuất xứ", "nơi sản xuất"):
        if _is_valid_text(props.get(k), 3):
            out["origin"] = props[k]
            break
    if not out["brand"] and _is_valid_text(props.get("thương hiệu"), 2):
        out["brand"] = props["thương hiệu"]

    if not out["name"]:
        return None
    return out


def fetch_text_urllib(url: str, timeout: int = 14) -> str | None:
    try:
        req = urllib.request.Request(url, headers=WEB_HEADERS)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            if r.status == 200:
                return r.read().decode("utf-8", "ignore")
    except Exception:
        return None
    return None


def _extract_size_sigs(text: str | None) -> set[str]:
    sigs: set[str] = set()
    s = (text or "").lower().replace(",", ".")
    # Normalize "1-5l" in slugs -> "1.5l"
    s = re.sub(r"(\d)-(\d)\s*(l|kg|g|ml)\b", r"\1.\2\3", s)
    for m in re.finditer(r"(\d+(?:\.\d+)?)\s*(kg|g|gr|gam|ml|lít|lit|l)\b", s):
        val = float(m.group(1))
        u = m.group(2)
        if u == "kg":
            sigs.add(f"{int(round(val * 1000))}g")
        elif u in ("g", "gr", "gam"):
            sigs.add(f"{int(round(val))}g")
        elif u in ("l", "lit", "lít"):
            sigs.add(f"{int(round(val * 1000))}ml")
        else:
            sigs.add(f"{int(round(val))}ml")
    return sigs
def _slug_match_tokens(name: str | None, brand: str | None = None) -> set[str]:
    raw = f"{name or ''} {brand or ''}"
    s = norm_text(raw)
    # Harmonize Vietnamese retail naming differences (Thanh Lam Mart vs Co.op Online / Long Chau)
    for src, dst in (
        ("pepsico", "pepsi"),
        ("gas", "ga"),
        ("giai khat", "ngot"),
        ("cafe", "ca phe"),
        ("socola", "chocolate"),
        ("lavie", "la vie"),
    ):
        s = re.sub(rf"\b{re.escape(src)}\b", dst, s)
    return content_tokens(s)


def enrich_from_cooponline(
    products: list[dict[str, Any]],
    records: dict[str, dict[str, Any]],
) -> dict[str, int]:
    print("\n=== STAGE 2: Co.op Online (18,092 products, 12 sitemaps) ===", flush=True)
    t0 = time.perf_counter()
    coop_urls = load_cooponline_urls()
    print(f"  Loaded {len(coop_urls)} Co.op Online URLs ({time.perf_counter()-t0:.1f}s)", flush=True)

    # Build inverted index over Co.op Online URL slugs
    coop_index: dict[str, list[int]] = {}
    coop_slugs: list[tuple[str, set[str], str, set[str], set[str]]] = []
    for idx, u in enumerate(coop_urls):
        slug = u.rstrip("/").split("/")[-1]
        slug = re.sub(r"--s\d+$", "", slug)
        nslug = norm_text(slug.replace("-", " "))
        toks = _slug_match_tokens(nslug)
        sz = _extract_size_sigs(slug)
        vr = extract_variants(nslug)
        coop_slugs.append((u, toks, nslug, sz, vr))
        for t in toks:
            coop_index.setdefault(t, []).append(idx)

    # Find candidate URLs for EVERY catalog SKU missing any of ingredients/usage/storage/origin/nutrition
    url_to_wm: dict[str, list[dict[str, Any]]] = {}
    wm_to_urls: dict[str, list[str]] = {}
    for p in products:
        pid = str(p["product_id"])
        cur = records.get(pid, {})
        needs_any = (
            ingredient_specificity_score(cur.get("ingredients")) < 4
            or not cur.get("usage")
            or not cur.get("storage")
            or not cur.get("origin")
            or not cur.get("nutrition")
        )
        if not needs_any:
            continue
        wm_norm = norm_text(p.get("name"))
        ptoks = _slug_match_tokens(p.get("name"), p.get("brand"))
        if not ptoks:
            continue
        brand_toks = _slug_match_tokens(p.get("brand"))
        wm_is_combo = "combo" in wm_norm.split()
        wm_sz = _extract_size_sigs(p.get("name"))
        wm_vr = extract_variants(wm_norm)
        wm_zero = bool(re.search(r"\b(?:khong calo|khong duong|zero|light)\b", wm_norm))
        counts: dict[int, int] = {}
        for t in ptoks:
            for ci in coop_index.get(t, ()):
                counts[ci] = counts.get(ci, 0) + 1
        if not counts:
            continue
        ranked: list[tuple[float, float, int, str]] = []
        for ci, ov in counts.items():
            if ov < 2:
                continue
            u, utoks, nslug, usz, uvr = coop_slugs[ci]
            if ("combo" in nslug.split()) != wm_is_combo:
                continue
            if brand_toks and not (brand_toks & utoks):
                continue
            u_zero = bool(re.search(r"\b(?:khong calo|khong duong|zero|light)\b", nslug))
            if wm_zero != u_zero:
                continue
            recall = ov / len(ptoks)
            jaccard = ov / max(1, len(ptoks | utoks))
            if (
                (ov >= 3 and recall >= 0.42)
                or (ov >= 2 and jaccard >= 0.34 and brand_toks)
                or (ov >= 2 and recall >= 0.58)
            ):
                size_bonus = 0.35 if (wm_sz and usz and (wm_sz & usz)) else 0.0
                var_penalty = -0.25 if (wm_vr != uvr and (wm_vr or uvr)) else 0.0
                ranked.append((recall + size_bonus + var_penalty, jaccard, ov, u))
        if ranked:
            ranked.sort(reverse=True)
            for _r, _j, _ov, u in ranked[:5]:
                url_to_wm.setdefault(u, []).append(p)
                wm_to_urls.setdefault(pid, []).append(u)

    unique_urls = list(url_to_wm.keys())
    print(f"  Candidate Co.op Online pages: {len(unique_urls)} unique URLs for {len(wm_to_urls)} SKUs", flush=True)

    # Load resumable page cache
    cache_jsonl = CACHE_DIR / "cooponline_pages.jsonl"
    pages: dict[str, dict[str, Any]] = {}
    if cache_jsonl.exists():
        for line in cache_jsonl.read_text(encoding="utf-8", errors="ignore").splitlines():
            if not line.strip():
                continue
            try:
                d = json.loads(line)
                if d.get("url"):
                    pages[d["url"]] = d
            except Exception:
                continue

    todo_urls = [u for u in unique_urls if u not in pages]
    print(f"  Cached={len(pages)}, fetching {len(todo_urls)} new Co.op Online pages...", flush=True)

    if todo_urls:
        t_f = time.perf_counter()
        with cache_jsonl.open("a", encoding="utf-8") as cf:
            batch_sz = 200
            for start in range(0, len(todo_urls), batch_sz):
                chunk = todo_urls[start : start + batch_sz]
                with concurrent.futures.ThreadPoolExecutor(max_workers=28) as ex:
                    results = list(ex.map(fetch_cooponline_page, chunk))
                for u, res in zip(chunk, results):
                    entry = res if res else {"url": u, "_miss": True}
                    pages[u] = entry
                    cf.write(json.dumps(entry, ensure_ascii=False) + "\n")
                cf.flush()
                done_n = min(start + batch_sz, len(todo_urls))
                ok_n = sum(1 for u in todo_urls[:done_n] if not pages.get(u, {}).get("_miss"))
                print(f"    CoopOnline [{done_n}/{len(todo_urls)}] ok={ok_n} elapsed={time.perf_counter()-t_f:.0f}s", flush=True)

    # Index fetched pages by exact EAN barcode
    by_barcode: dict[str, dict[str, Any]] = {}
    for pg in pages.values():
        if pg.get("_miss"):
            continue
        bc = pg.get("barcode") or ""
        if bc and len(bc) >= 8:
            by_barcode[bc] = pg

    stats = {
        "barcode_hits": 0, "title_hits": 0, "new_records": 0,
        "ing_added": 0, "ing_upgraded": 0, "usage_added": 0,
        "storage_added": 0, "origin_added": 0, "nutrition_added": 0,
    }

    for p in products:
        pid = str(p["product_id"])
        cur = records.get(pid)
        cur_ing = cur.get("ingredients") if cur else None
        cur_score = ingredient_specificity_score(cur_ing)
        needs_any = (
            cur_score < 4
            or not (cur and cur.get("usage"))
            or not (cur and cur.get("storage"))
            or not (cur and cur.get("origin"))
            or not (cur and cur.get("nutrition"))
        )
        if not needs_any:
            continue

        best_pg: dict[str, Any] | None = None
        best_method: str | None = None
        best_rank = -1

        bc = str(p.get("barcode") or "").strip()
        if bc and bc in by_barcode:
            best_pg = by_barcode[bc]
            best_method = "COOPONLINE_BARCODE"
            best_rank = 999

        if best_pg is None:
            for u in wm_to_urls.get(pid, ()):
                pg = pages.get(u)
                if not pg or pg.get("_miss"):
                    continue
                skus = {pg["barcode"]} if pg.get("barcode") else set()
                m = strict_verify_match(p, pg.get("name", ""), skus, pg.get("brand", ""))
                if not m:
                    continue
                rank = (
                    (100 if m == "BARCODE" else 0)
                    + ingredient_specificity_score(pg.get("ingredients")) * 2
                    + (3 if _is_valid_text(pg.get("storage"), 8) else 0)
                    + (2 if _is_valid_text(pg.get("usage"), 6) else 0)
                )
                if rank > best_rank:
                    best_pg = pg
                    best_method = f"COOPONLINE_{m}"
                    best_rank = rank
                    if m == "BARCODE":
                        break

        if not (best_pg and best_method):
            continue

        pg_ing = best_pg.get("ingredients") or ""
        # Accept concrete material/ingredient lists (including single material like "Nhựa PP cao cấp" for kitchenware when empty)
        want_ing = (
            _is_valid_text(pg_ing, 8)
            and (
                (is_clean_label_ingredients(pg_ing) and ingredient_specificity_score(pg_ing) > cur_score)
                or (not cur_ing and len(pg_ing) >= 10)
            )
        )
        pg_use = best_pg.get("usage") or ""
        want_use = not (cur and cur.get("usage")) and _is_valid_text(pg_use, 8)
        pg_sto = best_pg.get("storage") or ""
        want_sto = not (cur and cur.get("storage")) and _is_valid_text(pg_sto, 10)
        pg_ori = best_pg.get("origin") or ""
        want_ori = not (cur and cur.get("origin")) and _is_valid_text(pg_ori, 3)
        pg_nut = best_pg.get("nutrition")
        want_nut = not (cur and cur.get("nutrition")) and isinstance(pg_nut, dict) and bool(pg_nut)
        pg_sum = best_pg.get("summary") or ""
        want_sum = not (cur and cur.get("summary")) and _is_valid_text(pg_sum, 25)

        if not (want_ing or want_use or want_sto or want_ori or want_nut):
            continue

        if "BARCODE" in best_method:
            stats["barcode_hits"] += 1
        else:
            stats["title_hits"] += 1

        rec, created = _ensure_record(records, p)
        if created:
            stats["new_records"] += 1
        src_label = "thông tin nhãn sản phẩm (Co.op Online)"
        src_url = best_pg["url"]

        if want_ing:
            if cur_ing:
                stats["ing_upgraded"] += 1
            else:
                stats["ing_added"] += 1
            rec["ingredients"] = pg_ing
            rec["ingredients_source"] = src_label
            rec["ingredients_source_url"] = src_url
            rec["ingredients_match_method"] = best_method
        if want_use:
            rec["usage"] = pg_use
            rec["usage_source"] = src_label
            rec["usage_source_url"] = src_url
            stats["usage_added"] += 1
        if want_sto:
            rec["storage"] = pg_sto
            rec["storage_source"] = src_label
            rec["storage_source_url"] = src_url
            stats["storage_added"] += 1
        if want_ori:
            rec["origin"] = pg_ori
            rec["origin_source"] = src_label
            rec["origin_source_url"] = src_url
            stats["origin_added"] += 1
        if want_nut:
            rec["nutrition"] = pg_nut
            rec["nutrition_source"] = src_label
            rec["nutrition_source_url"] = src_url
            stats["nutrition_added"] += 1
        if want_sum:
            rec["summary"] = pg_sum

    print(f"  Co.op Online stats: {stats}", flush=True)
    return stats


# ---------------------------------------------------------------------------
# Stage 3: Expanded Long Chau (ALL 5 product sitemaps -> skincare, baby, health, hygiene)
# ---------------------------------------------------------------------------
def enrich_from_longchau_expanded(
    products: list[dict[str, Any]],
    records: dict[str, dict[str, Any]],
) -> dict[str, int]:
    print("\n=== STAGE 3: Expanded Long Chau (5 sitemaps) ===", flush=True)
    cache_u = CACHE_DIR / "longchau_urls.json"
    if cache_u.exists():
        lc_urls = json.loads(cache_u.read_text(encoding="utf-8"))
    else:
        lc_urls = []
    if len(lc_urls) < 100:
        lc_urls_set: set[str] = set()
        for sm in (
            "https://nhathuoclongchau.com.vn/sitemap_duoc-my-pham.xml",
            "https://nhathuoclongchau.com.vn/sitemap_cham-soc-ca-nhan.xml",
            "https://nhathuoclongchau.com.vn/sitemap_thuc-pham-chuc-nang.xml",
            "https://nhathuoclongchau.com.vn/sitemap_thuoc.xml",
            "https://nhathuoclongchau.com.vn/sitemap_trang-thiet-bi-y-te.xml",
        ):
            xml = fetch_text_urllib(sm, timeout=15)
            if xml:
                lc_urls_set.update(re.findall(r"<loc>(.*?)</loc>", xml))
        lc_urls = sorted(lc_urls_set)
        if lc_urls:
            cache_u.write_text(json.dumps(lc_urls, ensure_ascii=False), encoding="utf-8")
    print(f"  Loaded {len(lc_urls)} Long Chau URLs", flush=True)

    lc_index: dict[str, list[int]] = {}
    lc_slugs: list[tuple[str, set[str]]] = []
    for idx, u in enumerate(lc_urls):
        if not u.endswith(".html"):
            lc_slugs.append((u, set()))
            continue
        slug = u.rstrip("/").split("/")[-1].replace(".html", "")
        toks = content_tokens(slug.replace("-", " "))
        lc_slugs.append((u, toks))
        for t in toks:
            lc_index.setdefault(t, []).append(idx)

    cand_map: dict[str, list[dict[str, Any]]] = {}
    wm_to_urls: dict[str, list[str]] = {}
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
        ptoks = _slug_match_tokens(p.get("name"), p.get("brand"))
        brand_toks = _slug_match_tokens(p.get("brand"))
        if not ptoks:
            continue
        counts: dict[int, int] = {}
        for t in ptoks:
            for ci in lc_index.get(t, ()):
                counts[ci] = counts.get(ci, 0) + 1
        if not counts:
            continue
        scored = []
        for ci, ov in counts.items():
            if ov < 2:
                continue
            u, utoks = lc_slugs[ci]
            if brand_toks and not (brand_toks & utoks):
                continue
            rec_sc = ov / len(ptoks)
            jac = ov / max(1, len(ptoks | utoks))
            if (ov >= 3 and rec_sc >= 0.50) or (ov >= 2 and jac >= 0.45 and brand_toks):
                scored.append((rec_sc, jac, ov, u))
        if scored:
            scored.sort(reverse=True)
            for _r, _j, _o, u in scored[:3]:
                cand_map.setdefault(u, []).append(p)
                wm_to_urls.setdefault(pid, []).append(u)

    unique_urls = list(cand_map.keys())
    cache_jsonl = CACHE_DIR / "longchau_pages.jsonl"
    pages: dict[str, dict[str, Any]] = {}
    if cache_jsonl.exists():
        for line in cache_jsonl.read_text(encoding="utf-8", errors="ignore").splitlines():
            if line.strip():
                try:
                    d = json.loads(line)
                    if d.get("url"):
                        pages[d["url"]] = d
                except Exception:
                    pass

    todo = [u for u in unique_urls if u not in pages]
    print(f"  Candidate Long Chau pages: {len(unique_urls)} (fetching {len(todo)} new)", flush=True)

    def _fetch_lc(url: str) -> dict[str, Any]:
        raw = fetch_text_urllib(url, timeout=12)
        if not raw:
            return {"url": url, "_miss": True}
        m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', raw, re.S)
        if not m:
            return {"url": url, "_miss": True}
        try:
            nd = json.loads(m.group(1))
        except Exception:
            return {"url": url, "_miss": True}
        pp = nd.get("props", {}).get("pageProps", {})
        prod = pp.get("product") or pp.get("productDetail") or pp.get("data") or {}
        if not isinstance(prod, dict):
            return {"url": url, "_miss": True}
        name = str(prod.get("webName") or prod.get("name") or "").strip()
        brand = str((prod.get("brand") or {}).get("name") if isinstance(prod.get("brand"), dict) else prod.get("brand") or "").strip()

        ing_parts: list[str] = []
        raw_ings = prod.get("ingredient") or prod.get("ingredients")
        if isinstance(raw_ings, list):
            for item in raw_ings:
                if isinstance(item, dict) and item.get("name"):
                    part = str(item["name"]).strip()
                    if item.get("shortDescription"):
                        part += f" ({str(item['shortDescription']).strip()})"
                    ing_parts.append(part)
                elif isinstance(item, str) and item.strip():
                    ing_parts.append(item.strip())
        elif isinstance(raw_ings, str) and raw_ings.strip():
            ing_parts.append(_strip_html(raw_ings))
        ing_text = ", ".join(ing_parts)
        if ingredient_specificity_score(ing_text) < 2:
            desc_blob = json.dumps(prod, ensure_ascii=False)
            m_inci = re.search(
                r"(?:Danh sách thành phần(?: còn có)?|Thành phần đầy đủ|Ingredients?)\s*[:：]?\s*"
                r"((?:Aqua|Water|Glycerin|Sodium|Nước|Selenium)[^<\"\\]{25,500})",
                desc_blob,
                re.IGNORECASE,
            )
            if m_inci:
                ing_text = re.sub(r"\s+", " ", m_inci.group(1)).strip(" .,:;")

        usage_text = _strip_html(prod.get("dosage"))[:400]
        storage_text = _strip_html(prod.get("preservation"))
        if not storage_text:
            desc_plain = _strip_html(prod.get("description"))
            m_sto = re.search(
                r"(?:Bảo quản\s*[:：]?\s*)((?:Nơi khô|Bảo quản|Để nơi|Tránh ánh|Nhiệt độ)[^.]{12,240})",
                desc_plain,
                re.IGNORECASE,
            )
            if m_sto:
                storage_text = m_sto.group(1).strip()
        origin_text = str(prod.get("brandOrigin") or "").strip()
        summary_text = _strip_html(prod.get("shortDescription") or prod.get("usage"))[:450]
        return {
            "url": url, "name": name, "brand": brand,
            "ingredients": ing_text, "usage": usage_text,
            "storage": storage_text, "origin": origin_text, "summary": summary_text,
        }

    if todo:
        with cache_jsonl.open("a", encoding="utf-8") as cf:
            for start in range(0, len(todo), 150):
                chunk = todo[start : start + 150]
                with concurrent.futures.ThreadPoolExecutor(max_workers=24) as ex:
                    for res in ex.map(_fetch_lc, chunk):
                        pages[res["url"]] = res
                        cf.write(json.dumps(res, ensure_ascii=False) + "\n")
                cf.flush()

    stats = {"new_records": 0, "ing_added": 0, "ing_upgraded": 0, "usage_added": 0, "storage_added": 0, "origin_added": 0}
    src_label = "thông tin sản phẩm Nhà thuốc Long Châu"
    for p in products:
        pid = str(p["product_id"])
        urls = wm_to_urls.get(pid)
        if not urls:
            continue
        for u in urls:
            pg = pages.get(u)
            if not pg or pg.get("_miss"):
                continue
            m = strict_verify_match(p, pg.get("name", ""), set(), pg.get("brand", ""))
            if not m:
                continue
            cur = records.get(pid)
            cur_ing = cur.get("ingredients") if cur else None
            cur_score = ingredient_specificity_score(cur_ing)

            want_ing = is_clean_label_ingredients(pg.get("ingredients")) and ingredient_specificity_score(pg["ingredients"]) > cur_score
            want_use = not (cur and cur.get("usage")) and _is_valid_text(pg.get("usage"), 12)
            want_sto = not (cur and cur.get("storage")) and _is_valid_text(pg.get("storage"), 12)
            want_ori = not (cur and cur.get("origin")) and _is_valid_text(pg.get("origin"), 3)
            if not (want_ing or want_use or want_sto or want_ori):
                continue

            rec, created = _ensure_record(records, p)
            if created:
                stats["new_records"] += 1
            if want_ing:
                if cur_ing:
                    stats["ing_upgraded"] += 1
                else:
                    stats["ing_added"] += 1
                rec["ingredients"] = pg["ingredients"]
                rec["ingredients_source"] = src_label
                rec["ingredients_source_url"] = u
                rec["ingredients_match_method"] = f"LONGCHAU_{m}"
            if want_use:
                rec["usage"] = pg["usage"]
                rec["usage_source"] = src_label
                rec["usage_source_url"] = u
                stats["usage_added"] += 1
            if want_sto:
                rec["storage"] = pg["storage"]
                rec["storage_source"] = src_label
                rec["storage_source_url"] = u
                stats["storage_added"] += 1
            if want_ori:
                rec["origin"] = pg["origin"]
                rec["origin_source"] = src_label
                rec["origin_source_url"] = u
                stats["origin_added"] += 1
            break

    print(f"  Long Chau stats: {stats}", flush=True)
    return stats


# ---------------------------------------------------------------------------
# Stage 4: Kingfoodmart pass for remaining missing usage / storage / ingredients
# ---------------------------------------------------------------------------
def enrich_from_kingfoodmart_pass2(
    products: list[dict[str, Any]],
    records: dict[str, dict[str, Any]],
) -> dict[str, int]:
    print("\n=== STAGE 4: Kingfoodmart Pass 2 (remaining gaps) ===", flush=True)
    kf_urls = load_kingfoodmart_urls(CACHE_DIR / "kingfoodmart_product_urls.json")
    kf_index: dict[str, list[int]] = {}
    kf_slugs: list[tuple[str, set[str], str]] = []
    for idx, u in enumerate(kf_urls):
        slug = u.rstrip("/").split("/")[-1]
        nslug = norm_text(slug.replace("-", " "))
        toks = content_tokens(nslug)
        kf_slugs.append((u, toks, nslug))
        for t in toks:
            kf_index.setdefault(t, []).append(idx)

    wm_to_urls: dict[str, list[str]] = {}
    unique_urls_set: set[str] = set()
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
        wm_norm = norm_text(p.get("name"))
        ptoks = content_tokens(wm_norm)
        brand_toks = content_tokens(p.get("brand"))
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
            recall = ov / max(1, len(ptoks))
            jac = ov / max(1, len(ptoks | utoks))
            if (ov >= 3 and recall >= 0.50) or (ov >= 2 and jac >= 0.42 and brand_toks):
                ranked.append((recall, jac, ov, u))
        if ranked:
            ranked.sort(reverse=True)
            for _r, _j, _o, u in ranked[:3]:
                wm_to_urls.setdefault(pid, []).append(u)
                unique_urls_set.add(u)

    cache_jsonl = CACHE_DIR / "kingfoodmart_pages.jsonl"
    pages: dict[str, dict[str, Any]] = {}
    if cache_jsonl.exists():
        for line in cache_jsonl.read_text(encoding="utf-8", errors="ignore").splitlines():
            if line.strip():
                try:
                    d = json.loads(line)
                    if d.get("url"):
                        pages[d["url"]] = d
                except Exception:
                    pass

    todo = [u for u in sorted(unique_urls_set) if u not in pages]
    print(f"  Candidate KF pages: {len(unique_urls_set)} (fetching {len(todo)} new)", flush=True)
    if todo:
        with cache_jsonl.open("a", encoding="utf-8") as cf:
            for start in range(0, len(todo), 200):
                chunk = todo[start : start + 200]
                with concurrent.futures.ThreadPoolExecutor(max_workers=28) as ex:
                    for u, res in zip(chunk, ex.map(fetch_kf_fast, chunk)):
                        if res:
                            res["skus"] = sorted(res.get("skus") or [])
                            pages[u] = res
                            cf.write(json.dumps(res, ensure_ascii=False) + "\n")
                        else:
                            entry = {"url": u, "_miss": True}
                            pages[u] = entry
                            cf.write(json.dumps(entry, ensure_ascii=False) + "\n")
                cf.flush()

    kf_by_bc: dict[str, dict[str, Any]] = {}
    for pg in pages.values():
        if pg.get("_miss"):
            continue
        for sku in pg.get("skus") or []:
            if len(str(sku)) >= 8:
                kf_by_bc[str(sku)] = pg

    stats = {"new_records": 0, "ing_added": 0, "ing_upgraded": 0, "usage_added": 0, "storage_added": 0, "origin_added": 0}
    src_label = "thông tin nhãn sản phẩm (Kingfoodmart)"
    for p in products:
        pid = str(p["product_id"])
        cur = records.get(pid)
        cur_ing = cur.get("ingredients") if cur else None
        cur_score = ingredient_specificity_score(cur_ing)
        if not (
            cur_score < 4
            or not (cur and cur.get("usage"))
            or not (cur and cur.get("storage"))
            or not (cur and cur.get("origin"))
        ):
            continue
        bc = str(p.get("barcode") or "").strip()
        best_pg = kf_by_bc.get(bc) if bc else None
        best_m = "KINGFOODMART_BARCODE" if best_pg else None
        if best_pg is None:
            for u in wm_to_urls.get(pid, ()):
                pg = pages.get(u)
                if not pg or pg.get("_miss"):
                    continue
                m = strict_verify_match(p, pg.get("name", ""), set(pg.get("skus") or []), pg.get("brand", ""))
                if m:
                    best_pg = pg
                    best_m = f"KINGFOODMART_{m}"
                    break
        if not (best_pg and best_m):
            continue

        want_ing = is_clean_label_ingredients(best_pg.get("ingredients")) and ingredient_specificity_score(best_pg["ingredients"]) > cur_score
        want_use = not (cur and cur.get("usage")) and _is_valid_text(best_pg.get("usage"), 10)
        want_sto = not (cur and cur.get("storage")) and _is_valid_text(best_pg.get("storage"), 10)
        want_ori = not (cur and cur.get("origin")) and _is_valid_text(best_pg.get("origin"), 3)
        if not (want_ing or want_use or want_sto or want_ori):
            continue

        rec, created = _ensure_record(records, p)
        if created:
            stats["new_records"] += 1
        if want_ing:
            if cur_ing:
                stats["ing_upgraded"] += 1
            else:
                stats["ing_added"] += 1
            rec["ingredients"] = best_pg["ingredients"]
            rec["ingredients_source"] = src_label
            rec["ingredients_source_url"] = best_pg["url"]
            rec["ingredients_match_method"] = best_m
        if want_use:
            rec["usage"] = best_pg["usage"]
            rec["usage_source"] = src_label
            rec["usage_source_url"] = best_pg["url"]
            stats["usage_added"] += 1
        if want_sto:
            rec["storage"] = best_pg["storage"]
            rec["storage_source"] = src_label
            rec["storage_source_url"] = best_pg["url"]
            stats["storage_added"] += 1
        if want_ori:
            orig = best_pg["origin"].strip()
            if orig.upper() == "VIETNAM":
                orig = "Việt Nam"
            rec["origin"] = orig
            rec["origin_source"] = src_label
            rec["origin_source_url"] = best_pg["url"]
            stats["origin_added"] += 1

    print(f"  Kingfoodmart Pass 2 stats: {stats}", flush=True)
    return stats


# ---------------------------------------------------------------------------
# Stage 5: Mine unparsed usage/storage/nutrition clauses already inside summary
# ---------------------------------------------------------------------------
def mine_existing_summaries(records: dict[str, dict[str, Any]]) -> dict[str, int]:
    print("\n=== STAGE 5: Mining explicit usage/storage/nutrition from existing summaries ===", flush=True)
    stats = {"storage_mined": 0, "usage_mined": 0, "nutrition_mined": 0}
    sto_re = re.compile(
        r"((?:bảo quản(?:\s+ở|\s+tại|\s+nơi|\s+trong|\s+lạnh|\s*:)|"
        r"để nơi khô ráo|tránh ánh nắng trực tiếp|giữ lạnh ở nhiệt độ)[^.\n]{8,220}(?:\.[^.\n]{4,100})?)",
        re.I,
    )
    use_re = re.compile(
        r"((?:hướng dẫn sử dụng\s*:|cách dùng\s*:|cách sử dụng\s*:|"
        r"dùng trực tiếp|lắc đều trước khi uống|uống trực tiếp|ngon hơn khi uống lạnh|"
        r"pha với nước|cho nước sôi vào)[^.\n]{6,220}(?:\.[^.\n]{4,100})?)",
        re.I,
    )
    for rec in records.values():
        summ = str(rec.get("summary") or "")
        if not summ:
            continue
        if not rec.get("storage"):
            m = sto_re.search(summ)
            if m:
                rec["storage"] = m.group(1).strip()
                stats["storage_mined"] += 1
        if not rec.get("usage"):
            m = use_re.search(summ)
            if m:
                rec["usage"] = m.group(1).strip()
                stats["usage_mined"] += 1
        if not rec.get("nutrition"):
            nut = _extract_inline_nutrition(summ)
            if nut:
                rec["nutrition"] = nut
                rec["nutrition_source"] = "thông tin sản phẩm Thanh Lam Mart"
                stats["nutrition_mined"] += 1
    print(f"  Summary mining stats: {stats}", flush=True)
    return stats


# ---------------------------------------------------------------------------
# Stage 6: Same-formula sibling pack-size sharing (strictly verified same brand + exact variant!)
# ---------------------------------------------------------------------------
def propagate_same_formula_pack_siblings(
    products: list[dict[str, Any]],
    records: dict[str, dict[str, Any]],
) -> dict[str, int]:
    """Share verified formula facts ONLY between identical product SKUs that differ solely in pack size
    (e.g. Pepsi 390ml <-> Pepsi 1.5L, Coca 320ml <-> Coca 1.5L, Vinamilk không đường 180ml <-> 1L)."""
    print("\n=== STAGE 6: Same-formula sibling pack-size alignment ===", flush=True)

    def formula_key(p: dict[str, Any]) -> tuple[str, tuple[str, ...], str, tuple[str, ...]] | None:
        brand = norm_text(p.get("brand"))
        if not brand or len(brand) < 2:
            return None
        name_norm = norm_text(p.get("name"))
        if "combo" in name_norm.split():
            return None
        toks = sorted(_slug_match_tokens(p.get("name"), p.get("brand")))
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
        # Find best donor per field within identical-formula pack siblings
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

            want_ing = best_ing_score >= 4 and cur_score < 2 and bool(best_ing.get("ingredients"))
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

    print(f"  Same-formula sibling stats: {stats}", flush=True)
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
    print(f"BEFORE ({len(products)} SKUs): {before}", flush=True)

    enrich_from_openfoodfacts(products, records)
    enrich_from_cooponline(products, records)
    enrich_from_longchau_expanded(products, records)
    enrich_from_kingfoodmart_pass2(products, records)
    mine_existing_summaries(records)
    propagate_same_formula_pack_siblings(products, records)

    after = _counts()
    print(f"\nAFTER  ({len(products)} SKUs, {time.perf_counter()-t0:.1f}s): {after}", flush=True)
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

    # Verify Pepsi 1.5L TLM_10011241 right away!
    print("\nCHECK PEPSI 1.5L (TLM_10011241):", json.dumps(records.get("TLM_10011241"), ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()

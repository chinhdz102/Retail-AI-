"""High-precision back-of-pack ingredient enrichment from Kingfoodmart & Long Chau.

Upgrades missing or generic/marketing-blurb Thanh Lam Mart ingredient entries in
``data/thanh_lam_product_details.json`` without mutating ``data/thanh_lam_products.json``.

Safety & Precision Rules:
- Tier A (100% verified): Exact EAN-13 barcode match (`variant.sku == product.barcode`).
- Tier B (strictly verified): Same brand + same product form + zero flavor/sugar/variant
  conflicts + high content-token similarity (>= 0.72), rejecting combo packs unless
  the Thanh Lam Mart item is also a combo.
- Only writes/upgrades ``ingredients`` when the candidate is a concrete back-of-pack
  ingredient list (multiple concrete substances, percentages, or INS/additive codes)
  and strictly more specific than the existing Thanh Lam Mart string.
"""

from __future__ import annotations

import concurrent.futures
import json
import re
import time
import unicodedata
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CATALOG_PATH = ROOT / "data" / "thanh_lam_products.json"
DETAILS_PATH = ROOT / "data" / "thanh_lam_product_details.json"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "vi-VN,vi;q=0.9,en;q=0.8",
}

_STOP_TOKENS = frozenset(
    {
        "goi", "hop", "chai", "lon", "tui", "loc", "thung", "hu", "tuyp", "lo",
        "vi", "cai", "bich", "khay", "binh", "can", "ly", "to", "cuon", "mieng",
        "chiec", "thanh", "vien", "que", "day", "bo", "set", "va", "cua", "cho",
        "tu", "voi", "huong", "loai", "san", "pham", "moi", "dac", "biet",
        "anti", "dandruff", "shampoo", "conditioner", "wash", "cleanser", "lotion",
        "cream", "gel", "serum", "body", "facial", "face", "hair", "skin", "care",
        "1", "2", "3", "4", "5", "6", "8", "10", "12", "16", "18", "20", "24", "30",
    }
)
_SIZE_TOKEN_RE = re.compile(r"^\d+(?:[.,]\d+)?(?:g|gr|gam|kg|ml|l|lit|cm|mm|m|oz|pcs)?$")

# Compound brand normalizations so "chin su" == "chinsu", "aji mayo" == "ajimayo", etc.
_COMPOUND_BRANDS: tuple[tuple[str, str], ...] = (
    ("chin su", "chinsu"),
    ("coca cola", "cocacola"),
    ("aji mayo", "ajimayo"),
    ("th true milk", "thtruemilk"),
    ("th true", "thtrue"),
    ("nam ngu", "namngu"),
    ("hao hao", "haohao"),
    ("one one", "oneone"),
    ("choco pie", "chocopie"),
    ("o star", "ostar"),
    ("chupa chups", "chupachups"),
    ("red bull", "redbull"),
    ("dutch lady", "dutchlady"),
    ("nutri boost", "nutriboost"),
    ("head shoulders", "headshoulders"),
    ("x men", "xmen"),
    ("clear men", "clearmen"),
    ("la roche posay", "larocheposay"),
    ("phuc long", "phuclong"),
    ("trung nguyen", "trungnguyen"),
    ("vinh hao", "vinhhao"),
    ("kinh do", "kinhdo"),
    ("tho phat", "thophat"),
    ("ong gia ika", "onggiaika"),
    ("cau tre", "cautre"),
    ("vissan", "vissan"),
    ("acecook", "acecook"),
    ("masan", "masan"),
    ("unilever", "unilever"),
    ("nestle", "nestle"),
)

# Mutually exclusive product forms — never cross-match across these pairs.
_FORM_GROUPS: tuple[tuple[str, ...], ...] = (
    ("banh que", "banh xop", "banh quy", "banh bong lan", "banh bao", "banh gao", "banh mi", "banh pia", "banh trung thu", "banh snack", "banh cracker"),
    ("tuong ot", "tuong ca", "tuong den", "sa te", "nuoc mam", "nuoc tuong", "dau hao", "mayonnaise", "mu tat", "wasabi"),
    ("sua tuoi", "sua chua", "sua dac", "sua bot", "sua dau nanh", "sua hat", "sua bap", "sua trai cay"),
    ("mi nuoc", "mi xao", "mi tron", "mi ly", "mi chay", "pho", "bun", "mien", "chao", "hu tieu"),
    ("dau goi", "dau xa", "sua tam", "sua rua mat", "nuoc tay trang", "kem duong", "kem chong nang", "mat na", "lan ngan mui", "kem danh rang", "nuoc suc mieng"),
    ("nuoc giat", "bot giat", "nuoc xa", "nuoc rua chen", "nuoc lau san", "tay bon cau"),
    ("tra xanh", "tra dao", "tra chanh", "tra bi dao", "tra o long", "tra sua", "ca phe", "nuoc ngot", "nuoc tang luc", "nuoc khoang", "nuoc tinh khiet", "nuoc ep"),
)

# Flavor / sugar / variant descriptors that MUST match cleanly (no cross-flavor swaps).
_VARIANT_PHRASES: tuple[str, ...] = (
    "khong duong", "it duong", "co duong", "nguyen chat", "tach beo",
    "khong cay", "sieu cay", "cay nong", "cay ngot", "chua cay", "sa te", "sriracha",
    "la dua", "tra xanh", "pho mai", "ca phe", "hai san", "rong bien", "ca hoi",
    "thit bam", "thit nuong", "bo ham", "bo cay", "ga quay", "lau thai", "kim chi",
    "dau nanh", "dau xanh", "dau do", "hat sen", "hat dieu", "hanh nhan", "oc cho",
    "yen mach", "khoai mon", "khoai tay", "mat ong", "viet quat", "trai cay",
    "tu nhien", "truyen thong", "bac ha", "ngoc trai", "than hoat tinh", "nha dam",
    "hoa hong", "hoa cuc", "oai huong", "huong thao", "muoi ot", "chanh day",
    "socola", "chocolate", "cacao", "vani", "vanilla", "matcha", "barbecue", "bbq",
    "original", "classic", "zero", "light", "chay", "cay",
    "dau", "cam", "chanh", "xoai", "nho", "tao", "dao", "vai", "dua", "oi", "me",
    "bo", "ga", "tom", "heo", "cua", "muc", "ca", "trung", "tieu", "toi", "hanh", "gung",
    "bap", "ngo", "gao", "sua", "kem", "den", "trang", "do", "xanh", "vang",
)

_FLUFF_ONLY_RE = re.compile(
    r"(?:nguyên\s+liệu|thành\s+phần)\s+(?:tự\s+nhiên\s+)?(?:hảo\s+hạng\s+)?(?:sạch\s+)?"
    r"(?:được\s+)?(?:tuyển\s+chọn|chọn\s+lọc)",
    re.IGNORECASE,
)


def norm_text(text: str | None) -> str:
    s = unicodedata.normalize("NFD", (text or "").lower().replace("đ", "d"))
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    s = re.sub(r"[^a-z0-9]+", " ", s).strip()
    for src, dst in _COMPOUND_BRANDS:
        s = re.sub(rf"\b{re.escape(src)}\b", dst, s)
    return s


def content_tokens(text: str | None) -> set[str]:
    toks = set()
    for t in norm_text(text).split():
        if len(t) < 2 or t in _STOP_TOKENS or _SIZE_TOKEN_RE.match(t):
            continue
        toks.add(t)
    return toks


def extract_variants(normalized: str) -> set[str]:
    found: set[str] = set()
    padded = f" {normalized} "
    for phrase in _VARIANT_PHRASES:
        if f" {phrase} " in padded:
            found.add(phrase)
    # Normalize synonyms
    if "chocolate" in found:
        found.add("socola")
        found.discard("chocolate")
    if "vanilla" in found:
        found.add("vani")
        found.discard("vanilla")
    if "barbecue" in found:
        found.add("bbq")
        found.discard("barbecue")
    if "ngo" in found:
        found.add("bap")
        found.discard("ngo")
    # Remove single-word subsets already covered by multi-word phrases
    if "la dua" in found:
        found.discard("dua")
    if "dau nanh" in found or "dau xanh" in found or "dau do" in found:
        found.discard("dau")
    if "tra xanh" in found:
        found.discard("xanh")
    if "khong duong" in found or "it duong" in found or "co duong" in found:
        found.discard("duong")
    if "khong cay" in found or "sieu cay" in found or "cay ngot" in found or "chua cay" in found:
        found.discard("cay")
    if "muoi ot" in found:
        found.discard("ot")
    return found


def extract_form(normalized: str) -> tuple[int, str] | None:
    padded = f" {normalized} "
    for g_idx, forms in enumerate(_FORM_GROUPS):
        for f in forms:
            if f" {f} " in padded:
                return (g_idx, f)
    return None


def ingredient_specificity_score(ing: str | None) -> int:
    """Return a specificity score (0 = empty/fluff, >= 4 = detailed back-of-pack label)."""
    if not ing or not isinstance(ing, str):
        return 0
    s = ing.strip()
    if len(s) < 15:
        return 0
    commas = s.count(",") + s.count(";")
    has_pct = bool(re.search(r"\d+(?:[.,]\d+)?\s*%|g/kg|mg/", s, re.I))
    has_code = bool(re.search(r"\bINS\s*\d{3,4}|\(\d{3,4}[a-z]?\)|\bE\d{3}\b", s, re.I))
    is_fluff = bool(_FLUFF_ONLY_RE.search(s)) and commas <= 1 and not has_pct and not has_code
    if is_fluff:
        return 0
    score = commas
    if has_pct:
        score += 3
    if has_code:
        score += 3
    if len(s) >= 120 and commas >= 2:
        score += 1
    return score


def is_clean_label_ingredients(ing: str | None) -> bool:
    if not ing or not isinstance(ing, str):
        return False
    s = re.sub(r"<[^>]+>", " ", ing)
    s = re.sub(r"\s+", " ", s).strip()
    if len(s) < 15:
        return False
    # Reject placeholder / marketing-only blurbs
    if re.search(r"(?:đang cập nhật|xem trên bao bì|theo nhà sản xuất)", s, re.I):
        return False
    return ingredient_specificity_score(s) >= 2


def fetch_text(url: str, timeout: int = 12) -> str | None:
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status != 200:
                return None
            return resp.read().decode("utf-8", "ignore")
    except Exception:
        return None


def load_kingfoodmart_urls(cache_path: Path) -> list[str]:
    if cache_path.exists():
        urls = json.loads(cache_path.read_text(encoding="utf-8"))
        if len(urls) > 5000:
            return urls
    def _fetch_sm(idx: int) -> list[str]:
        xml = fetch_text(f"https://kingfoodmart.com/sitemap-all/sitemap-product/{idx}", timeout=10)
        return re.findall(r"<loc>(.*?)</loc>", xml) if xml else []

    urls: list[str] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as ex:
        for locs in ex.map(_fetch_sm, range(1, 104)):
            urls.extend(locs)
    urls = sorted(set(urls))
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(urls, ensure_ascii=False), encoding="utf-8")
    return urls


def fetch_kingfoodmart_product(url: str) -> dict[str, Any] | None:
    html = fetch_text(url, timeout=12)
    if not html:
        return None
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if not m:
        return None
    try:
        nd = json.loads(m.group(1))
    except json.JSONDecodeError:
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
            except json.JSONDecodeError:
                dj = {}
        else:
            dj = {}

    variants = data.get("variants") or []
    skus = set()
    variant_names: list[str] = []
    for v in variants:
        if isinstance(v, dict):
            sku = str(v.get("sku") or "").strip()
            if sku:
                skus.add(sku)
            vname = str(v.get("name") or "").strip()
            if vname:
                variant_names.append(vname)

    ing = dj.get("ingredients") if isinstance(dj, dict) else None
    if isinstance(ing, str):
        ing = re.sub(r"<[^>]+>", " ", ing)
        ing = re.sub(r"\s+", " ", ing).strip()
    else:
        ing = ""

    usages = dj.get("usages") or dj.get("uses") or ""
    if isinstance(usages, str):
        usages = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", usages)).strip()
    else:
        usages = ""

    storage = dj.get("preservation") or dj.get("storage") or dj.get("storageInstructions") or ""
    if isinstance(storage, str):
        storage = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", storage)).strip()
    else:
        storage = ""
    if not storage:
        for cand_blob in (dj.get("uses"), dj.get("introduction"), dj.get("highlights")):
            if isinstance(cand_blob, str) and "bảo quản" in cand_blob.casefold():
                plain_b = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", cand_blob)).strip()
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
    intro = dj.get("introduction") or dj.get("highlights") or ""
    if isinstance(intro, str):
        intro = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", intro)).strip()
    else:
        intro = ""
    name = str(data.get("name") or (variant_names[0] if variant_names else "")).strip()
    return {
        "url": url,
        "name": name,
        "variant_names": variant_names,
        "skus": skus,
        "brand": brand,
        "ingredients": ing,
        "usage": usages,
        "storage": storage,
        "origin": origin,
        "summary": intro,
    }


def strict_verify_match(
    wm_prod: dict[str, Any],
    ext_name: str,
    ext_skus: set[str],
    ext_brand: str,
) -> str | None:
    """Return 'BARCODE' or 'STRICT_TITLE' if verified match, else None."""
    wm_barcode = str(wm_prod.get("barcode") or "").strip()
    if wm_barcode and len(wm_barcode) >= 8 and wm_barcode in ext_skus:
        return "BARCODE"

    wm_norm = norm_text(wm_prod.get("name"))
    ext_norm = norm_text(ext_name)
    if not wm_norm or not ext_norm:
        return None

    # Never match a combo product to a single product (or vice versa)
    wm_is_combo = "combo" in wm_norm.split()
    ext_is_combo = "combo" in ext_norm.split()
    if wm_is_combo != ext_is_combo:
        return None

    # Brand must align when Thanh Lam Mart specifies a real brand
    wm_brand_norm = norm_text(wm_prod.get("brand"))
    wm_brand_toks = {t for t in wm_brand_norm.split() if len(t) >= 2 and t not in _STOP_TOKENS}
    ext_all_norm = norm_text(f"{ext_name} {ext_brand}")
    ext_all_toks = set(ext_all_norm.split())
    if wm_brand_toks and not (wm_brand_toks & ext_all_toks):
        return None

    # Product form group must NOT conflict (e.g. banh que != banh xop, tuong ot != tuong ca)
    wm_form = extract_form(wm_norm)
    ext_form = extract_form(ext_norm)
    if wm_form and ext_form and wm_form[0] == ext_form[0] and wm_form[1] != ext_form[1]:
        return None

    # Flavor / sugar / variant descriptors must NOT conflict!
    wm_vars = extract_variants(wm_norm)
    ext_vars = extract_variants(ext_norm)
    # Check sugar level explicitly
    sugar_tags = {"khong duong", "it duong", "co duong", "nguyen chat"}
    if (wm_vars & sugar_tags) != (ext_vars & sugar_tags):
        if (wm_vars & sugar_tags) or (ext_vars & sugar_tags):
            return None
    # Check spicy/flavor mismatches
    if wm_vars and ext_vars:
        # Reject if either side introduces a flavor the other does not have
        # (allow minor overlap like "kem" when base flavors match)
        diff_wm = wm_vars - ext_vars - {"kem", "tu nhien", "truyen thong"}
        diff_ext = ext_vars - wm_vars - {"kem", "tu nhien", "truyen thong"}
        if diff_wm or diff_ext:
            return None
    elif (wm_vars - {"kem", "tu nhien", "truyen thong"}) or (ext_vars - {"kem", "tu nhien", "truyen thong"}):
        # One has a specific flavor/variant and the other is plain/unflavored -> reject
        return None

    # Content token overlap check
    wm_toks = content_tokens(wm_prod.get("name"))
    ext_toks = content_tokens(ext_name)
    if not wm_toks or not ext_toks:
        return None
    overlap = len(wm_toks & ext_toks)
    recall = overlap / len(wm_toks)
    precision = overlap / len(ext_toks)
    if overlap >= 3 and recall >= 0.72 and precision >= 0.55:
        return "STRICT_TITLE"
    if overlap >= 2 and recall >= 0.85 and precision >= 0.75 and wm_brand_toks:
        return "STRICT_TITLE"
    return None


def enrich_from_longchau(
    products: list[dict[str, Any]],
    records: dict[str, dict[str, Any]],
) -> tuple[int, int, int, int]:
    """Enrich skincare, pharmacy & personal-care items (ingredients, usage, storage, origin)."""
    lc_urls: list[str] = []
    for sm in [
        "https://nhathuoclongchau.com.vn/sitemap_duoc-my-pham.xml",
        "https://nhathuoclongchau.com.vn/sitemap_cham-soc-ca-nhan.xml",
        "https://nhathuoclongchau.com.vn/sitemap_thuc-pham-chuc-nang.xml",
        "https://nhathuoclongchau.com.vn/sitemap_thuoc.xml",
    ]:
        xml = fetch_text(sm, timeout=12)
        if xml:
            lc_urls.extend(re.findall(r"<loc>(.*?)</loc>", xml))
    lc_urls = sorted(set(lc_urls))
    if not lc_urls:
        return 0, 0, 0, 0

    # Index LC URLs by slug tokens
    lc_index: dict[str, list[int]] = {}
    lc_slugs: list[tuple[str, set[str]]] = []
    for idx, u in enumerate(lc_urls):
        slug = u.rstrip("/").split("/")[-1].replace(".html", "")
        toks = content_tokens(slug.replace("-", " "))
        lc_slugs.append((u, toks))
        for t in toks:
            lc_index.setdefault(t, []).append(idx)

    # Match personal care, cosmetics, baby care & pharmacy items missing ANY key detail
    personal_kw = re.compile(
        r"(?:sữa rửa mặt|mặt nạ|kem dưỡng|gel chống nắng|kem chống nắng|dầu gội|sữa tắm|"
        r"dầu xả|nước tẩy trang|lăn ngăn mùi|xịt khử mùi|kem đánh răng|nước súc miệng|"
        r"sữa dưỡng thể|tẩy tế bào|nước rửa tay|khăn ướt|băng vệ sinh|tã |bỉm|"
        r"selsun|nivea|senka|pond|hatomugi|dove|lifebuoy|clear|sunsilk|colgate|"
        r"cetaphil|simple|romano|x-men|pantene|head|tresemme|rejoice|hazeline|"
        r"biore|garnier|vaseline|johnson|pigeon|sensodyne|listerine|p/s|close up|"
        r"oral-b|dettol|kotex|diana|laurier|huggies|bobby|pampers|merries|moony)",
        re.IGNORECASE,
    )
    cand_map: dict[str, list[tuple[dict[str, Any], float]]] = {}
    for p in products:
        pid = p["product_id"]
        cur = records.get(pid, {})
        needs_ing = ingredient_specificity_score(cur.get("ingredients")) < 4
        needs_usage = not cur.get("usage")
        needs_storage = not cur.get("storage")
        needs_origin = not cur.get("origin")
        if not (needs_ing or needs_usage or needs_storage or needs_origin):
            continue
        dept_blob = f"{p.get('name', '')} {p.get('category_hierarchy', '')}"
        if not personal_kw.search(dept_blob) and "hoá mỹ phẩm" not in dept_blob.casefold():
            continue
        ptoks = content_tokens(p["name"])
        brand_toks = content_tokens(p.get("brand"))
        counts: dict[int, int] = {}
        for t in ptoks:
            for ci in lc_index.get(t, ()):
                counts[ci] = counts.get(ci, 0) + 1
        if not counts:
            continue
        scored = []
        for ci, ov in counts.items():
            u, utoks = lc_slugs[ci]
            if brand_toks and not (brand_toks & utoks):
                continue
            rec_score = ov / max(1, len(ptoks))
            if (ov >= 3 and rec_score >= 0.50) or (ov >= 2 and rec_score >= 0.65 and brand_toks):
                scored.append((rec_score, ov, u))
        if scored:
            scored.sort(reverse=True)
            for rec_score, _ov, u in scored[:2]:
                cand_map.setdefault(u, []).append((p, rec_score))

    unique_urls = list(cand_map.keys())
    if not unique_urls:
        return 0, 0, 0, 0

    def _clean_html(raw: Any) -> str:
        if not isinstance(raw, str) or not raw.strip():
            return ""
        text = re.sub(r"</?(?:p|div|br|li|tr|h[1-6]|ul|ol)[^>]*>", ". ", raw, flags=re.I)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text).strip(" .")
        text = re.sub(r"\.\s*\.", ".", text)
        return text

    def _fetch_lc(url: str) -> tuple[str, dict[str, Any] | None]:
        html = fetch_text(url, timeout=12)
        if not html:
            return url, None
        m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
        if not m:
            return url, None
        try:
            nd = json.loads(m.group(1))
        except json.JSONDecodeError:
            return url, None
        pp = nd.get("props", {}).get("pageProps", {})
        prod = pp.get("product") or pp.get("productDetail") or pp.get("data") or {}
        if not isinstance(prod, dict):
            return url, None
        name = str(prod.get("webName") or prod.get("name") or "").strip()
        brand = str(
            (prod.get("brand") or {}).get("name")
            if isinstance(prod.get("brand"), dict)
            else prod.get("brand") or ""
        ).strip()

        # 1. Ingredients
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
            ing_parts.append(_clean_html(raw_ings))
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

        # 2. Usage instructions (Long Chau stores "Cách dùng" in `dosage`!)
        dosage_text = _clean_html(prod.get("dosage"))
        if dosage_text and len(dosage_text) > 380:
            dosage_text = dosage_text[:377].rsplit(" ", 1)[0].rstrip(" ,;:") + "."

        # 3. Storage instructions (from `preservation` / `guideline` / `description`)
        storage_text = _clean_html(prod.get("preservation"))
        if not storage_text:
            desc_plain = _clean_html(prod.get("description"))
            m_sto = re.search(
                r"(?:Bảo quản\s*[:：]?\s*)((?:Nơi khô|Bảo quản|Để nơi|Tránh ánh|Nhiệt độ)[^.]{12,240})",
                desc_plain,
                re.IGNORECASE,
            )
            if m_sto:
                storage_text = m_sto.group(1).strip()

        # 4. Origin & Summary
        origin_text = str(prod.get("brandOrigin") or "").strip()
        summary_text = _clean_html(prod.get("shortDescription") or prod.get("usage"))

        return url, {
            "url": url,
            "name": name,
            "brand": brand,
            "ingredients": ing_text,
            "usage": dosage_text,
            "storage": storage_text,
            "origin": origin_text,
            "summary": summary_text,
        }

    upgraded = 0
    added = 0
    lc_usage_added = 0
    lc_storage_added = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as ex:
        results = list(ex.map(_fetch_lc, unique_urls))

    page_by_url = {
        u: d
        for u, d in results
        if d
        and (
            is_clean_label_ingredients(d.get("ingredients"))
            or len(d.get("usage") or "") >= 15
            or len(d.get("storage") or "") >= 12
        )
    }
    for url, plist in cand_map.items():
        ext = page_by_url.get(url)
        if not ext:
            continue
        for p, _sc in plist:
            pid = p["product_id"]
            match_kind = strict_verify_match(p, ext["name"], set(), ext["brand"])
            if not match_kind:
                continue
            cur = records.get(pid)
            changed_any = False

            def _ensure_record() -> dict[str, Any]:
                nonlocal added
                if pid not in records:
                    records[pid] = {
                        "product_id": pid,
                        "product_name": p["name"],
                        "brand": p.get("brand") or "",
                        "barcode": str(p.get("barcode") or ""),
                        "source_url": p.get("seo_url") or "",
                    }
                    added += 1
                return records[pid]

            ext_ing = ext.get("ingredients") or ""
            if is_clean_label_ingredients(ext_ing):
                cur_score = ingredient_specificity_score(cur.get("ingredients") if cur else None)
                if ingredient_specificity_score(ext_ing) > cur_score:
                    rec = _ensure_record()
                    if cur and cur.get("ingredients"):
                        upgraded += 1
                    rec["ingredients"] = ext_ing
                    rec["ingredients_source"] = "thông tin sản phẩm Nhà thuốc Long Châu"
                    rec["ingredients_source_url"] = url
                    rec["ingredients_match_method"] = f"LONGCHAU_{match_kind}"
                    changed_any = True

            ext_usage = ext.get("usage") or ""
            if len(ext_usage) >= 15 and not (cur and cur.get("usage")):
                rec = _ensure_record()
                rec["usage"] = ext_usage
                rec["usage_source"] = "thông tin sản phẩm Nhà thuốc Long Châu"
                rec["usage_source_url"] = url
                lc_usage_added += 1
                changed_any = True

            ext_storage = ext.get("storage") or ""
            if len(ext_storage) >= 12 and not (cur and cur.get("storage")):
                rec = _ensure_record()
                rec["storage"] = ext_storage
                rec["storage_source"] = "thông tin sản phẩm Nhà thuốc Long Châu"
                rec["storage_source_url"] = url
                lc_storage_added += 1
                changed_any = True

            ext_origin = ext.get("origin") or ""
            if ext_origin and not (cur and cur.get("origin")):
                rec = _ensure_record()
                rec["origin"] = ext_origin
                rec["origin_source"] = "thông tin sản phẩm Nhà thuốc Long Châu"
                rec["origin_source_url"] = url
                changed_any = True

            ext_summary = ext.get("summary") or ""
            if len(ext_summary) >= 25 and not (cur and cur.get("summary")):
                rec = _ensure_record()
                rec["summary"] = ext_summary
                changed_any = True

            if changed_any:
                cur = records[pid]

    return upgraded, added, lc_usage_added, lc_storage_added


def main() -> None:
    t0 = time.perf_counter()
    products: list[dict[str, Any]] = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    details_doc: dict[str, Any] = json.loads(DETAILS_PATH.read_text(encoding="utf-8"))
    records: dict[str, dict[str, Any]] = details_doc["records"]

    before_with_ing = sum(1 for r in records.values() if r.get("ingredients"))
    before_detailed = sum(1 for r in records.values() if ingredient_specificity_score(r.get("ingredients")) >= 4)

    cache_path = ROOT / ".cache" / "kingfoodmart_product_urls.json"
    kf_urls = load_kingfoodmart_urls(cache_path)
    print(f"Loaded {len(kf_urls)} Kingfoodmart product URLs ({time.perf_counter() - t0:.1f}s)")

    # Build inverted index over non-combo Kingfoodmart slugs
    kf_index: dict[str, list[int]] = {}
    kf_slugs: list[tuple[str, set[str], str]] = []
    for idx, u in enumerate(kf_urls):
        slug = u.rstrip("/").split("/")[-1]
        norm_slug = norm_text(slug.replace("-", " "))
        toks = content_tokens(norm_slug)
        kf_slugs.append((u, toks, norm_slug))
        for t in toks:
            kf_index.setdefault(t, []).append(idx)

    # Identify all Thanh Lam Mart products missing detailed ingredients, usage, storage, or origin
    url_to_wm: dict[str, list[dict[str, Any]]] = {}
    wm_to_urls: dict[str, list[str]] = {}
    candidate_products = 0
    for p in products:
        pid = p["product_id"]
        cur = records.get(pid, {})
        needs_any = (
            ingredient_specificity_score(cur.get("ingredients")) < 4
            or not cur.get("usage")
            or not cur.get("storage")
            or not cur.get("origin")
        )
        if not needs_any:
            continue
        wm_norm = norm_text(p["name"])
        ptoks = content_tokens(wm_norm)
        if not ptoks:
            continue
        brand_toks = content_tokens(p.get("brand"))
        wm_is_combo = "combo" in wm_norm.split()
        counts: dict[int, int] = {}
        for t in ptoks:
            for ci in kf_index.get(t, ()):
                counts[ci] = counts.get(ci, 0) + 1
        if not counts:
            continue
        ranked: list[tuple[float, float, int, str]] = []
        for ci, ov in counts.items():
            if ov < 2:
                continue
            u, utoks, nslug = kf_slugs[ci]
            if ("combo" in nslug.split()) != wm_is_combo:
                continue
            if brand_toks and not (brand_toks & utoks):
                continue
            recall = ov / len(ptoks)
            jaccard = ov / max(1, len(ptoks | utoks))
            if (ov >= 3 and recall >= 0.58) or (ov >= 2 and jaccard >= 0.50 and brand_toks):
                ranked.append((recall, jaccard, ov, u))
        if ranked:
            candidate_products += 1
            ranked.sort(reverse=True)
            for _r, _j, _ov, u in ranked[:2]:
                url_to_wm.setdefault(u, []).append(p)
                wm_to_urls.setdefault(pid, []).append(u)

    unique_kf_urls = list(url_to_wm.keys())
    print(f"Fetching {len(unique_kf_urls)} unique Kingfoodmart pages for {candidate_products} candidate Thanh Lam Mart SKUs...")

    kf_pages: dict[str, dict[str, Any]] = {}
    t_fetch = time.perf_counter()
    with concurrent.futures.ThreadPoolExecutor(max_workers=24) as ex:
        for res in ex.map(fetch_kingfoodmart_product, unique_kf_urls):
            if res and res.get("url"):
                kf_pages[res["url"]] = res
    print(f"Fetched {len(kf_pages)} / {len(unique_kf_urls)} KF pages in {time.perf_counter() - t_fetch:.1f}s")

    # Build exact barcode index across all fetched KF pages (for ANY verified field!)
    kf_by_barcode: dict[str, dict[str, Any]] = {}
    for page in kf_pages.values():
        has_useful = (
            is_clean_label_ingredients(page.get("ingredients"))
            or len(page.get("usage") or "") >= 12
            or len(page.get("storage") or "") >= 12
            or bool(page.get("origin"))
        )
        if has_useful:
            for sku in page.get("skus", ()):
                if len(sku) >= 8:
                    kf_by_barcode[sku] = page

    kf_barcode_hits = 0
    kf_title_hits = 0
    kf_upgraded = 0
    kf_added = 0
    kf_usage_added = 0
    kf_storage_added = 0
    kf_origin_added = 0

    for p in products:
        pid = p["product_id"]
        cur = records.get(pid)
        cur_ing = cur.get("ingredients") if cur else None
        cur_score = ingredient_specificity_score(cur_ing)
        needs_any = (
            cur_score < 4
            or not (cur and cur.get("usage"))
            or not (cur and cur.get("storage"))
            or not (cur and cur.get("origin"))
        )
        if not needs_any:
            continue

        best_page: dict[str, Any] | None = None
        best_method: str | None = None
        best_score = -1

        # 1. Exact EAN-13 barcode match first!
        bc = str(p.get("barcode") or "").strip()
        if bc and bc in kf_by_barcode:
            best_page = kf_by_barcode[bc]
            best_method = "KINGFOODMART_BARCODE"
            best_score = ingredient_specificity_score(best_page.get("ingredients"))

        # 2. Strict brand + form + variant + token verified match
        if best_page is None:
            for u in wm_to_urls.get(pid, ()):
                page = kf_pages.get(u)
                if not page:
                    continue
                m = strict_verify_match(p, page["name"], page.get("skus", set()), page.get("brand", ""))
                if not m:
                    continue
                sc = ingredient_specificity_score(page.get("ingredients"))
                if m == "BARCODE" or best_page is None or sc > best_score:
                    best_page = page
                    best_method = f"KINGFOODMART_{m}"
                    best_score = sc
                    if m == "BARCODE":
                        break

        if best_page and best_method:
            page_has_ing = is_clean_label_ingredients(best_page.get("ingredients"))
            want_ing = page_has_ing and ingredient_specificity_score(best_page["ingredients"]) > cur_score
            want_usage = not (cur and cur.get("usage")) and len(best_page.get("usage") or "") >= 12
            want_storage = not (cur and cur.get("storage")) and len(best_page.get("storage") or "") >= 12
            want_origin = not (cur and cur.get("origin")) and bool(best_page.get("origin"))
            want_summary = not (cur and cur.get("summary")) and len(best_page.get("summary") or "") >= 25
            if not (want_ing or want_usage or want_storage or want_origin):
                continue

            if "BARCODE" in best_method:
                kf_barcode_hits += 1
            else:
                kf_title_hits += 1
            if pid not in records:
                records[pid] = {
                    "product_id": pid,
                    "product_name": p["name"],
                    "brand": p.get("brand") or "",
                    "barcode": bc,
                    "source_url": p.get("seo_url") or "",
                }
                kf_added += 1
            if want_ing:
                if cur_ing:
                    kf_upgraded += 1
                records[pid]["ingredients"] = best_page["ingredients"]
                records[pid]["ingredients_source"] = "thông tin nhãn sản phẩm (Kingfoodmart)"
                records[pid]["ingredients_source_url"] = best_page["url"]
                records[pid]["ingredients_match_method"] = best_method
            if want_usage:
                records[pid]["usage"] = best_page["usage"]
                records[pid]["usage_source"] = "thông tin nhãn sản phẩm (Kingfoodmart)"
                records[pid]["usage_source_url"] = best_page["url"]
                kf_usage_added += 1
            if want_storage:
                records[pid]["storage"] = best_page["storage"]
                records[pid]["storage_source"] = "thông tin nhãn sản phẩm (Kingfoodmart)"
                records[pid]["storage_source_url"] = best_page["url"]
                kf_storage_added += 1
            if want_origin:
                orig = best_page["origin"].strip()
                if orig.upper() == "VIETNAM":
                    orig = "Việt Nam"
                records[pid]["origin"] = orig
                records[pid]["origin_source"] = "thông tin nhãn sản phẩm (Kingfoodmart)"
                records[pid]["origin_source_url"] = best_page["url"]
                kf_origin_added += 1
            if want_summary:
                records[pid]["summary"] = best_page["summary"]

    # Source 2: Long Chau skincare / personal care / pharmacy enrichment
    lc_upgraded, lc_added, lc_usage_added, lc_storage_added = enrich_from_longchau(products, records)

    # Update summary stats (clean_thanh_lam_product_details.py will run right after to finalize QA)
    details_doc["generated_at"] = datetime.now(UTC).isoformat()
    details_doc["enriched_products"] = len(records)
    details_doc["field_counts"] = {
        field: sum(1 for r in records.values() if r.get(field))
        for field in ("ingredients", "usage", "storage", "summary", "origin")
    }
    DETAILS_PATH.write_text(json.dumps(details_doc, ensure_ascii=False, indent=2), encoding="utf-8")

    after_with_ing = sum(1 for r in records.values() if r.get("ingredients"))
    after_detailed = sum(1 for r in records.values() if ingredient_specificity_score(r.get("ingredients")) >= 4)
    print("\n=== ENRICHMENT SUMMARY ===")
    print(f"Elapsed: {time.perf_counter() - t0:.1f}s")
    print(
        f"Kingfoodmart: barcode={kf_barcode_hits}, strict_title={kf_title_hits} | "
        f"ing_upgraded={kf_upgraded}, new_records={kf_added}, "
        f"usage+={kf_usage_added}, storage+={kf_storage_added}, origin+={kf_origin_added}"
    )
    print(
        f"Long Chau: ing_upgraded={lc_upgraded}, new_records={lc_added}, "
        f"usage+={lc_usage_added}, storage+={lc_storage_added}"
    )
    print(f"Ingredients total: {before_with_ing} -> {after_with_ing}")
    print(f"Detailed back-of-pack ingredients (score>=4): {before_detailed} -> {after_detailed}")
    for pid in ("TLM_10141747", "TLM_10016912"):
        r = records.get(pid, {})
        print(f"Check {pid}: ing={str(r.get('ingredients'))[:100]} | usage={str(r.get('usage'))[:100]}")


if __name__ == "__main__":
    main()

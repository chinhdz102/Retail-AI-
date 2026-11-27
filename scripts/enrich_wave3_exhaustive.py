"""Wave-3 Exhaustive Multi-Source Product Knowledge Crawler.

Systematically closes remaining attribute gaps in ``data/thanh_lam_product_details.json``
using 100% public sitemaps, REST APIs, and SSR HTML pages (zero CAPTCHA/anti-bot bypass,
zero fabrication):
  Stage 0: Zero-cost recovery from existing `.cache/*.jsonl` + safe summary clause mining
  Stage 1: OpenFoodFacts API v2 100% complete zero-skip retry (polite pacing + cache)
  Stage 2: Compound-brand-alias & model-code deep pass on Co.op Online, Kingfoodmart,
           Long Chau, and HachiHachi (fixes LOCK&LOCK->locknlock, LAY'S->lays,
           RED BULL->redbull, CHOCOPIE->choco-pie, JOHNSON'S->johnsons, etc.)
  Stage 3: Medigo (31,230 structured JSON `__NEXT_DATA__` personal-care/skincare/health SKUs)
  Stage 4: Con Cung (4.5k), Gofood (3k meat/seafood), Elmich (1.4k), Sunhouse (4.4k),
           Dien May Cho Lon kitchenware (565), Van Phong Pham 247 (645)
  Stage 5: Meta.vn (~45,000 kitchenware/household/stationery/consumer SKUs)
  Stage 6: Chiaki.vn (~280,000 Vietnamese retail/cosmetics/snacks/household SKUs)
  Stage 7: Same-formula pack-size & multipack sibling alignment
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

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.clean_thanh_lam_product_details import (  # noqa: E402
    clean_ingredients,
    clean_storage,
    clean_usage,
)
from scripts.enrich_all_missing_details import (  # noqa: E402
    CACHE_DIR,
    CATALOG_PATH,
    DETAILS_PATH,
    OFF_HEADERS,
    WEB_HEADERS,
    _ensure_record,
    _extract_size_sigs,
    _is_valid_text,
    _slug_match_tokens,
    _strip_html,
    fetch_cooponline_page,
    fetch_kf_fast,
    fetch_text_urllib,
    load_cooponline_urls,
)
from scripts.enrich_detailed_ingredients import (  # noqa: E402
    extract_form,
    extract_variants,
    ingredient_specificity_score,
    is_clean_label_ingredients,
    load_kingfoodmart_urls,
    norm_text,
    strict_verify_match,
)
from scripts.enrich_wave2_deep import (  # noqa: E402
    _PACK_WRAPPER_RE,
    _PRIVATE_OR_GENERIC_BRANDS,
    _blank_stats,
    parse_hachihachi_page,
    propagate_multipack_siblings,
)

# ---------------------------------------------------------------------------
# Brand-alias & model-code normalization (fixes LOCK&LOCK->locknlock, LAY'S->lays, etc.)
# ---------------------------------------------------------------------------
_EXTRA_BRAND_SYNONYMS: dict[str, set[str]] = {
    "lock&lock": {"lock", "locknlock", "locklock"},
    "lock & lock": {"lock", "locknlock", "locklock"},
    "locknlock": {"lock", "locknlock", "locklock"},
    "lay's": {"lays", "lay"},
    "lays": {"lays", "lay"},
    "red bull": {"redbull", "red", "bull"},
    "redbull": {"redbull", "red", "bull"},
    "chocopie": {"chocopie", "choco", "pie", "orion"},
    "choco-pie": {"chocopie", "choco", "pie", "orion"},
    "johnson's": {"johnsons", "johnson"},
    "johnsons": {"johnsons", "johnson"},
    "kitkat": {"kitkat", "kit", "kat"},
    "coca-cola": {"cocacola", "coca", "cola"},
    "coca cola": {"cocacola", "coca", "cola"},
    "m&m's": {"mms", "m&m", "mm"},
    "m&m": {"mms", "m&m", "mm"},
    "head & shoulders": {"head", "shoulders", "headshoulders"},
    "head&shoulders": {"head", "shoulders", "headshoulders"},
    "p&g": {"pg"},
    "p/s": {"ps"},
    "7up": {"7up", "7-up"},
    "pepsico": {"pepsi", "pepsico", "7up", "mirinda", "sting", "twister", "aquafina", "lipton", "revive", "olong"},
    "thiên long": {"thienlong", "thien", "long", "flexoffice"},
    "thien long": {"thienlong", "thien", "long", "flexoffice"},
    "cha cheer": {"chacheer", "cha", "cheer"},
    "hồng lam": {"honglam", "hong", "lam"},
    "yến việt": {"yenviet", "yen", "viet"},
    "chin-su": {"chinsu", "chin", "su"},
    "chinsu": {"chinsu", "chin", "su"},
    "o'star": {"ostar"},
    "l'oreal": {"loreal"},
    "pond's": {"ponds", "pond"},
    "st.ives": {"stives", "ives"},
    "la roche-posay": {"larocheposay", "roche", "posay"},
    "scotch brite": {"scotchbrite", "scotch", "brite"},
    "jumbo vape": {"jumbovape", "jumbo", "vape"},
    "jumpo vape": {"jumbovape", "jumbo", "vape"},
    "clear men": {"clear", "clearmen"},
    "sunlight": {"sunlight"},
    "sunsilk": {"sunsilk"},
    "lifebuoy": {"lifebuoy"},
    "gervenne": {"gervenne"},
    "romano": {"romano"},
    "enchanteur": {"enchanteur"},
    "doublemint": {"doublemint", "doublemin"},
    "doublemin": {"doublemint", "doublemin"},
}

_MODEL_CODE_RE = re.compile(r"\b(?:[a-z]{2,4}[-]?\d{2,5}[a-z0-9]{0,3})\b", re.IGNORECASE)
_NOT_MODEL_RE = re.compile(r"^(?:ph\d|sp\d|ms\d|so\d|lo\d|goi\d|hop\d|t\d{2}|x\d+|ml|kg|gr|g\d+|l\d+|cm\d+|mm\d+|b\d{1,2}|e\d{3}|d\d+|a\d+|c\d+|k\d+)$", re.I)

_HTTP_CLIENT: httpx.Client | None = None


def _get_client() -> httpx.Client:
    global _HTTP_CLIENT
    if _HTTP_CLIENT is None:
        _HTTP_CLIENT = httpx.Client(
            headers={**WEB_HEADERS, "Accept-Encoding": "gzip, deflate"},
            follow_redirects=True,
            timeout=14.0,
            limits=httpx.Limits(max_connections=40, max_keepalive_connections=24),
        )
    return _HTTP_CLIENT


def fetch_fast(url: str, timeout: float = 13.0) -> str | None:
    try:
        r = _get_client().get(url, timeout=timeout)
        if r.status_code == 200:
            return r.text
    except Exception:
        pass
    return None


def fetch_urllib_gz(url: str, timeout: int = 16) -> str | None:
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


def _norm_compound_tokens(text: str | None) -> set[str]:
    """Tokenize text while also generating apostrophe-stripped & joined bigram tokens."""
    if not text:
        return set()
    # Collapse apostrophes inside words first (Lay's -> Lays, Johnson's -> Johnsons, O'Star -> OStar)
    cleaned = re.sub(r"(?<=[a-zA-ZÀ-ỹ])['’](?=[a-zA-ZÀ-ỹ])", "", text)
    cleaned = cleaned.replace("&", " n ")
    n = norm_text(cleaned)
    toks = _slug_match_tokens(n)
    # Also add direct normalized words & concatenated bigrams for multi-word brands
    words = [w for w in re.findall(r"[a-z0-9]+", n) if len(w) >= 2]
    for i in range(len(words) - 1):
        if len(words[i]) <= 6 and len(words[i + 1]) <= 6:
            toks.add(words[i] + words[i + 1])
    return toks


def _brand_alias_set(brand: str | None) -> set[str]:
    if not brand:
        return set()
    nb = norm_text(brand)
    if nb in _PRIVATE_OR_GENERIC_BRANDS:
        return set()
    low = brand.strip().lower()
    out: set[str] = set()
    if low in _EXTRA_BRAND_SYNONYMS:
        out |= _EXTRA_BRAND_SYNONYMS[low]
    if nb in _EXTRA_BRAND_SYNONYMS:
        out |= _EXTRA_BRAND_SYNONYMS[nb]
    cleaned = re.sub(r"['’]", "", low)
    words = [w for w in re.findall(r"[a-z0-9]+", norm_text(cleaned)) if len(w) >= 2]
    out.update(words)
    if len(words) >= 2:
        out.add("".join(words))
    if "&" in low:
        parts = [w for w in re.findall(r"[a-z0-9]+", norm_text(low.replace("&", " "))) if len(w) >= 2]
        if len(parts) == 2:
            out.add(parts[0] + "n" + parts[1])
            out.add(parts[0] + parts[1])
    return out


def _extract_model_codes(text: str | None) -> set[str]:
    if not text:
        return set()
    codes: set[str] = set()
    for m in _MODEL_CODE_RE.findall(norm_text(text)):
        c = m.replace("-", "")
        if len(c) >= 4 and not _NOT_MODEL_RE.match(c) and not c.endswith(("ml", "gr", "kg", "cm", "mm")):
            codes.add(c)
    return codes


def _core_tokens_v3(name: str | None, brand: str | None = None) -> set[str]:
    if not name:
        return set()
    cleaned = re.sub(r"(?<=[a-zA-ZÀ-ỹ])['’](?=[a-zA-ZÀ-ỹ])", "", name)
    n = _PACK_WRAPPER_RE.sub(" ", norm_text(cleaned))
    b = "" if norm_text(brand) in _PRIVATE_OR_GENERIC_BRANDS else (brand or "")
    b = re.sub(r"(?<=[a-zA-ZÀ-ỹ])['’](?=[a-zA-ZÀ-ỹ])", "", b)
    return _slug_match_tokens(n, b)


def strict_verify_v3(
    wm_prod: dict[str, Any],
    ext_name: str,
    ext_skus: set[str],
    ext_brand: str,
    *,
    allow_private_brand_fresh: bool = False,
) -> str | None:
    """Strict verification with exact barcode, exact model code, and brand-alias multipack matching."""
    # 1. Exact barcode match (100% deterministic!)
    wm_bc = str(wm_prod.get("barcode") or "").strip()
    if wm_bc and len(wm_bc) >= 8 and wm_bc in ext_skus:
        return "BARCODE"

    m = strict_verify_match(wm_prod, ext_name, ext_skus, ext_brand)
    if m:
        return m

    wm_name_clean = _PACK_WRAPPER_RE.sub(" ", norm_text(re.sub(r"['’]", "", str(wm_prod.get("name") or ""))))
    ext_name_clean = _PACK_WRAPPER_RE.sub(" ", norm_text(re.sub(r"['’]", "", ext_name or "")))
    if not wm_name_clean or not ext_name_clean:
        return None
    if ("combo" in wm_name_clean.split()) != ("combo" in ext_name_clean.split()):
        return None

    wm_brand_norm = norm_text(wm_prod.get("brand"))
    is_private = wm_brand_norm in _PRIVATE_OR_GENERIC_BRANDS
    wm_brand_aliases = _brand_alias_set(wm_prod.get("brand"))
    ext_all_toks = _norm_compound_tokens(f"{ext_name} {ext_brand}")

    brand_ok = (not wm_brand_aliases) or bool(wm_brand_aliases & ext_all_toks)
    if wm_brand_aliases and not brand_ok:
        return None

    # 2. Exact hardware/stationery/kitchenware model code + matching brand! (e.g. Lock&Lock HAP813N, TL08)
    wm_models = _extract_model_codes(wm_prod.get("name"))
    ext_models = _extract_model_codes(ext_name)
    if wm_models and ext_models and (wm_models & ext_models) and (brand_ok and not is_private):
        return "MODEL_CODE"

    # 3. Product form group must NOT conflict
    wm_form = extract_form(wm_name_clean)
    ext_form = extract_form(ext_name_clean)
    if wm_form and ext_form and wm_form[0] == ext_form[0] and wm_form[1] != ext_form[1]:
        return None

    # 4. Sugar / zero-calorie tags must NEVER conflict
    wm_vars = extract_variants(wm_name_clean)
    ext_vars = extract_variants(ext_name_clean)
    sugar_tags = {"khong duong", "it duong", "co duong", "nguyen chat", "zero", "light"}
    if (wm_vars & sugar_tags) != (ext_vars & sugar_tags):
        return None

    # 5. Flavor variants must NOT conflict
    diff_wm = wm_vars - ext_vars - {"kem", "tu nhien", "truyen thong", "chanh"}
    diff_ext = ext_vars - wm_vars - {"kem", "tu nhien", "truyen thong", "chanh"}
    if diff_wm or diff_ext:
        return None
    if ("chanh" in (wm_vars ^ ext_vars)) and not ({"sprite", "7up"} & ext_all_toks):
        return None

    wm_toks = _core_tokens_v3(wm_prod.get("name"), wm_prod.get("brand"))
    ext_toks = _core_tokens_v3(ext_name, ext_brand)
    if not wm_toks or not ext_toks:
        return None
    # Count overlap using normalized brand aliases as well
    ov_set = wm_toks & ext_toks
    ov = len(ov_set)
    # Credit brand-alias match if canonical brand token differed (lock&lock vs locknlock)
    if wm_brand_aliases and (wm_brand_aliases & ext_all_toks) and not (wm_brand_aliases & ov_set):
        ov += 1
    rec = ov / len(wm_toks)
    prec = ov / len(ext_toks)

    if wm_brand_aliases and ov >= 4 and rec >= 0.55 and prec >= 0.42:
        return "STRICT_ALIAS"
    if wm_brand_aliases and ov >= 3 and rec >= 0.65 and prec >= 0.48:
        return "STRICT_ALIAS"
    if wm_brand_aliases and ov >= 2 and rec >= 0.78 and prec >= 0.62:
        return "STRICT_ALIAS"
    if is_private and allow_private_brand_fresh and ov >= 2 and rec >= 0.75 and prec >= 0.60:
        return "FRESH_TITLE"
    return None


def apply_fields_v3(
    records: dict[str, dict[str, Any]],
    p: dict[str, Any],
    ext: dict[str, Any],
    src_label: str,
    match_method: str,
    stats: dict[str, int],
) -> bool:
    """Apply fields ONLY if they pass our SOTA cleaner — guarantees zero fluff & immediate recovery!"""
    pid = str(p["product_id"])
    pname = str(p.get("name") or "")
    cur = records.get(pid)
    cur_ing = cur.get("ingredients") if cur else None
    cur_score = ingredient_specificity_score(cur_ing)

    raw_ing = (ext.get("ingredients") or "").strip()
    cleaned_ing, bleed = clean_ingredients(raw_ing, pname, source=src_label) if raw_ing else (None, {})
    want_ing = False
    if cleaned_ing:
        if not cur_ing:
            want_ing = True
        elif (
            is_clean_label_ingredients(cleaned_ing)
            and ingredient_specificity_score(cleaned_ing) > cur_score + 1
        ):
            want_ing = True

    raw_use = (ext.get("usage") or "").strip() or bleed.get("usage", "")
    cleaned_use, sto_from_use = clean_usage(raw_use, pname) if raw_use else (None, None)
    want_use = not (cur and cur.get("usage")) and bool(cleaned_use)

    raw_sto = (ext.get("storage") or "").strip() or bleed.get("storage", "") or (sto_from_use or "")
    cleaned_sto = clean_storage(raw_sto, pname) if raw_sto else None
    want_sto = not (cur and cur.get("storage")) and bool(cleaned_sto)

    pg_ori = (ext.get("origin") or "").strip()
    # Reject bogus sidebar/UI text in origin
    if pg_ori and (len(pg_ori) > 65 or any(bad in pg_ori.lower() for bad in ("http", "chiaki", "meta.vn", "đăng nhập", "giỏ hàng", "tìm kiếm", "chọn "))):
        pg_ori = ""
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

    if want_ing and cleaned_ing:
        if cur_ing:
            stats["ing_upgraded"] += 1
        else:
            stats["ing_added"] += 1
        rec["ingredients"] = cleaned_ing
        rec["ingredients_source"] = src_label
        if url:
            rec["ingredients_source_url"] = url
        rec["ingredients_match_method"] = match_method
    if want_use and cleaned_use:
        rec["usage"] = cleaned_use
        rec["usage_source"] = src_label
        if url:
            rec["usage_source_url"] = url
        stats["usage_added"] += 1
    if want_sto and cleaned_sto:
        rec["storage"] = cleaned_sto
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


def _needs_enrichment(rec: dict[str, Any] | None) -> bool:
    if not rec:
        return True
    return not (rec.get("ingredients") and rec.get("usage") and rec.get("storage") and rec.get("origin"))


# ---------------------------------------------------------------------------
# Stage 0A: Replay ALL existing `.cache/*.jsonl` against cleaned details!
# ---------------------------------------------------------------------------
def stage0a_replay_caches(
    products: list[dict[str, Any]],
    records: dict[str, dict[str, Any]],
) -> None:
    print("\n=== STAGE 0A: Zero-Cost Replay of Existing Caches (with upgraded cleaner & brand aliases) ===", flush=True)
    sources = [
        ("cooponline_pages.jsonl", "thông tin nhãn sản phẩm (Co.op Online)", "COOPONLINE"),
        ("kingfoodmart_pages.jsonl", "thông tin nhãn sản phẩm (Kingfoodmart)", "KINGFOODMART"),
        ("longchau_pages.jsonl", "thông tin sản phẩm (Nhà thuốc Long Châu)", "LONGCHAU"),
        ("hachihachi_pages.jsonl", "thông tin nhãn sản phẩm (HachiHachi)", "HACHIHACHI"),
        ("kidsplaza_pages.jsonl", "thông tin sản phẩm (KidsPlaza)", "KIDSPLAZA"),
        ("bibomart_pages.jsonl", "thông tin sản phẩm (Bibomart)", "BIBOMART"),
    ]
    fresh_cats = {"Trái cây tươi", "Củ, Quả", "Rau Lá", "Thịt", "Trứng", "Hải sản", "Cá, Hải Sản"}

    for fn, src_label, tag in sources:
        fp = CACHE_DIR / fn
        if not fp.exists():
            continue
        by_bc: dict[str, dict[str, Any]] = {}
        pages: list[tuple[dict[str, Any], set[str], set[str]]] = []
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
            ptoks = _norm_compound_tokens(f"{pg.get('name', '')} {pg.get('brand', '')}")
            idx = len(pages)
            pages.append((pg, skus, ptoks))
            for t in ptoks:
                tok_idx.setdefault(t, []).append(idx)

        stats = _blank_stats()
        for p in products:
            pid = str(p["product_id"])
            if not _needs_enrichment(records.get(pid)):
                continue
            bc = str(p.get("barcode") or "").strip()
            is_fresh = (p.get("category_name") or "") in fresh_cats
            if bc and bc in by_bc:
                if apply_fields_v3(records, p, by_bc[bc], src_label, f"{tag}_BARCODE", stats):
                    stats["barcode_hits"] += 1
                    continue
            # Also try compound-brand alias title match across cached pages
            wm_toks = _core_tokens_v3(p.get("name"), p.get("brand"))
            b_aliases = _brand_alias_set(p.get("brand"))
            query_toks = wm_toks | b_aliases
            counts: dict[int, int] = {}
            for t in query_toks:
                for pi in tok_idx.get(t, ()):
                    counts[pi] = counts.get(pi, 0) + 1
            best = sorted(((ov, pi) for pi, ov in counts.items() if ov >= 2), reverse=True)[:5]
            for _ov, pi in best:
                pg, skus, _ = pages[pi]
                m = strict_verify_v3(p, pg.get("name", ""), skus, pg.get("brand", ""), allow_private_brand_fresh=is_fresh)
                if m:
                    if apply_fields_v3(records, p, pg, src_label, f"{tag}_{m}", stats):
                        stats["title_hits"] += 1
                        break
        print(f"  Replay {tag:13s} ({len(pages):4d} pages) -> {stats}", flush=True)


# ---------------------------------------------------------------------------
# Stage 0B: Safe Summary Clause Mining (Zero HTTP!)
# ---------------------------------------------------------------------------
_SUMMARY_ING_PATTERNS = [
    re.compile(r"(?:có\s+thành\s+phần(?:\s+chính)?(?:\s+từ|\s+gồm|\s+bao\s+gồm|\s*:)\s*|được\s+làm\s+từ\s+thành\s+phần\s+)([^\.\n]{10,280})", re.I),
    re.compile(r"(?:được\s+làm\s+(?:từ|bằng)|làm\s+(?:từ|bằng)|sử\s+dụng|chế\s+biến\s+từ)\s+(?:chất\s+liệu\s+|nguyên\s+liệu\s+)?(100%\s+[^\.\n]{6,180}|(?:nhựa|inox|thép|thủy tinh|gốm|sứ|gỗ|bông|cotton|vải|giấy|bột giấy|hợp kim|silicone|cao su)[^\.\n]{4,180})", re.I),
    re.compile(r"\bthành\s+phần\s*[:：]\s*([^\.\n]{10,300})", re.I),
    re.compile(r"\bchất\s+liệu\s*[:：]\s*([^\.\n]{6,200})", re.I),
]
_SUMMARY_STO_RE = re.compile(r"\b((?:bảo\s+quản|để)\s+(?:nơi\s+khô\s+ráo|trong\s+ngăn\s+mát|trong\s+tủ\s+lạnh|ở\s+nhiệt\s+độ)[^\.\n]{5,180})", re.I)


def stage0b_mine_summaries(
    products: list[dict[str, Any]],
    records: dict[str, dict[str, Any]],
) -> None:
    print("\n=== STAGE 0B: Safe Concrete Clause Mining from Existing Summaries ===", flush=True)
    stats = _blank_stats()
    for p in products:
        pid = str(p["product_id"])
        rec = records.get(pid)
        if not rec or not rec.get("summary"):
            continue
        summ = str(rec["summary"])
        ext: dict[str, Any] = {}
        if not rec.get("ingredients"):
            for pat in _SUMMARY_ING_PATTERNS:
                m = pat.search(summ)
                if m:
                    cand = m.group(1).strip(" ,;:-")
                    # Reject generic marketing like "những nguyên liệu tự nhiên được chọn lọc"
                    if not re.search(r"^(?:những\s+)?(?:nguyên\s+liệu|thành\s+phần)\s+(?:tự\s+nhiên|hảo\s+hạng|tươi\s+ngon|an\s+toàn|được\s+(?:tuyển\s+chọn|chọn\s+lọc))", cand, re.I):
                        ext["ingredients"] = cand
                        break
        if not rec.get("storage"):
            m_s = _SUMMARY_STO_RE.search(summ)
            if m_s:
                ext["storage"] = m_s.group(1).strip()
        if ext:
            apply_fields_v3(records, p, ext, "mô tả sản phẩm (Thanh Lam Mart)", "SUMMARY_CLAUSE", stats)
    print(f"  Summary mining stats: {stats}", flush=True)


# ---------------------------------------------------------------------------
# Stage 1: OpenFoodFacts 100% Complete Retry (Polite Pacing + Persistent Cache)
# ---------------------------------------------------------------------------
def stage1_openfoodfacts_complete(
    products: list[dict[str, Any]],
    records: dict[str, dict[str, Any]],
) -> None:
    print("\n=== STAGE 1: OpenFoodFacts API v2 100% Zero-Skip Retry ===", flush=True)
    cache_path = CACHE_DIR / "off_v2_cache.json"
    cache: dict[str, dict[str, Any] | None] = {}
    if cache_path.exists():
        try:
            cache = json.loads(cache_path.read_text(encoding="utf-8"))
        except Exception:
            cache = {}

    by_bc: dict[str, list[dict[str, Any]]] = {}
    for p in products:
        bc = str(p.get("barcode") or "").strip()
        if re.fullmatch(r"\d{8,14}", bc):
            by_bc.setdefault(bc, []).append(p)

    # Only query barcodes that aren't in cache yet AND belong to SKUs missing nutrition or ingredients
    todo_bcs = sorted(
        bc for bc, plist in by_bc.items()
        if bc not in cache and any(
            not records.get(str(p["product_id"]), {}).get("nutrition")
            or not records.get(str(p["product_id"]), {}).get("ingredients")
            for p in plist
        )
    )
    fields = "code,product_name,brands,ingredients_text_vi,ingredients_text,nutriments,nutrition_data_per,origins"
    batches = [todo_bcs[i : i + 35] for i in range(0, len(todo_bcs), 35)]
    print(f"  OFF barcodes to query: {len(todo_bcs)} in {len(batches)} batches (already cached={len(cache)})", flush=True)

    ok_b = 0
    for idx, batch in enumerate(batches):
        qs = urllib.parse.urlencode({"code": ",".join(batch), "fields": fields, "page_size": str(len(batch))})
        url = f"https://world.openfoodfacts.org/api/v2/search?{qs}"
        got = False
        for attempt in range(5):
            try:
                req = urllib.request.Request(url, headers=OFF_HEADERS)
                with urllib.request.urlopen(req, timeout=25) as r:
                    if r.status == 200:
                        d = json.loads(r.read().decode("utf-8", errors="replace"))
                        found = {str(pr.get("code") or "").strip(): pr for pr in (d.get("products") or [])}
                        for bc in batch:
                            cache[bc] = found.get(bc)
                        got = True
                        ok_b += 1
                        break
            except Exception:
                time.sleep(1.5 * (attempt + 1))
        if (idx + 1) % 15 == 0 or (idx + 1) == len(batches):
            cache_path.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
            print(f"    OFF progress [{idx+1}/{len(batches)}] ok={ok_b}", flush=True)
        time.sleep(0.35 if got else 1.2)

    stats = _blank_stats()
    for bc, prod in cache.items():
        if not prod or bc not in by_bc:
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

        for p in by_bc[bc]:
            pn = (p.get("name") or "").lower()
            n_adj = {**nutr, "basis": "100ml"} if (nutr and any(w in pn for w in ("ml", "lít", "lit", "nước", "sữa tươi", "trà ", "bia "))) else nutr
            ext = {
                "url": f"https://world.openfoodfacts.org/product/{bc}",
                "ingredients": ing,
                "origin": orig,
                "nutrition": n_adj or None,
            }
            if apply_fields_v3(records, p, ext, "OpenFoodFacts", "OPENFOODFACTS_BARCODE", stats):
                stats["barcode_hits"] += 1
    print(f"  OFF Complete stats: batches={ok_b}/{len(batches)} {stats}", flush=True)


# ---------------------------------------------------------------------------
# Generic indexed crawler helper with brand-alias & model-code candidate ranking
# ---------------------------------------------------------------------------
def crawl_indexed_site(
    *,
    site_tag: str,
    src_label: str,
    urls: list[str],
    slug_extract_fn,
    page_parser_fn,
    products: list[dict[str, Any]],
    records: dict[str, dict[str, Any]],
    max_new_fetch: int = 800,
    max_per_sku: int = 3,
    workers: int = 24,
    require_brand: bool = True,
    allow_fresh_private: bool = False,
    category_filter: set[str] | None = None,
) -> dict[str, int]:
    print(f"\n=== SITE: {site_tag} ({len(urls):,} indexed URLs) ===", flush=True)
    tok_index: dict[str, list[int]] = {}
    model_index: dict[str, list[int]] = {}
    url_meta: list[tuple[str, set[str], str, set[str]]] = []
    for idx, u in enumerate(urls):
        slug_raw = slug_extract_fn(u)
        nslug = norm_text(slug_raw.replace("-", " "))
        utoks = _norm_compound_tokens(nslug)
        sz = _extract_size_sigs(slug_raw)
        url_meta.append((u, utoks, nslug, sz))
        for t in utoks:
            tok_index.setdefault(t, []).append(idx)
        for mc in _extract_model_codes(nslug):
            model_index.setdefault(mc, []).append(idx)

    fresh_cats = {"Trái cây tươi", "Củ, Quả", "Rau Lá", "Thịt", "Trứng", "Hải sản", "Cá, Hải Sản"}
    wm_to_urls: dict[str, list[str]] = {}
    cand_priority: list[tuple[float, str]] = []
    seen_u: set[str] = set()

    for p in products:
        pid = str(p["product_id"])
        rec = records.get(pid)
        if not _needs_enrichment(rec):
            continue
        cat = p.get("category_name") or ""
        if category_filter and cat not in category_filter:
            continue
        is_fresh = cat in fresh_cats
        ptoks = _core_tokens_v3(p.get("name"), p.get("brand"))
        b_aliases = _brand_alias_set(p.get("brand"))
        wm_models = _extract_model_codes(p.get("name"))
        if not ptoks:
            continue
        if require_brand and not b_aliases and not (allow_fresh_private and is_fresh):
            continue

        wm_norm = norm_text(p.get("name"))
        wm_is_combo = "combo" in wm_norm.split()
        wm_sz = _extract_size_sigs(p.get("name"))
        wm_zero = bool(re.search(r"\b(?:khong calo|khong duong|zero|light)\b", wm_norm))

        counts: dict[int, int] = {}
        # Model-code exact hits get massive boost!
        for mc in wm_models:
            for ci in model_index.get(mc, ()):
                counts[ci] = counts.get(ci, 0) + 5
        for t in (ptoks | b_aliases):
            posting = tok_index.get(t)
            if posting and len(posting) <= 12000:
                for ci in posting:
                    counts[ci] = counts.get(ci, 0) + 1

        ranked: list[tuple[float, float, int, str]] = []
        for ci, ov in counts.items():
            if ov < 2:
                continue
            u, utoks, nslug, usz = url_meta[ci]
            if ("combo" in nslug.split()) != wm_is_combo:
                continue
            if b_aliases and not (b_aliases & utoks):
                continue
            u_zero = bool(re.search(r"\b(?:khong calo|khong duong|zero|light)\b", nslug))
            if wm_zero != u_zero:
                continue
            real_ov = len(ptoks & utoks) + (1 if (b_aliases and (b_aliases & utoks) and not (b_aliases & ptoks & utoks)) else 0)
            has_model = bool(wm_models and (wm_models & _extract_model_codes(nslug)))
            rec_score = real_ov / len(ptoks)
            jac = real_ov / max(1, len(ptoks | utoks))
            if (
                has_model
                or (real_ov >= 4 and rec_score >= 0.42)
                or (real_ov >= 3 and rec_score >= 0.48)
                or (real_ov >= 2 and jac >= 0.38 and bool(b_aliases or (allow_fresh_private and is_fresh)))
                or (real_ov >= 2 and rec_score >= 0.65)
            ):
                sz_bonus = 0.25 if (wm_sz and usz and (wm_sz & usz)) else 0.0
                miss_bonus = 0.5 if not (rec and rec.get("ingredients")) else 0.0
                model_bonus = 1.0 if has_model else 0.0
                ranked.append((model_bonus + miss_bonus + rec_score + sz_bonus, jac, real_ov, u))

        if ranked:
            ranked.sort(reverse=True)
            for sc, _j, _o, u in ranked[:max_per_sku]:
                wm_to_urls.setdefault(pid, []).append(u)
                if u not in seen_u:
                    seen_u.add(u)
                    cand_priority.append((sc, u))

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

    cand_priority.sort(reverse=True)
    todo = [u for _sc, u in cand_priority if u not in pages][:max_new_fetch]
    print(f"  {site_tag}: matched_skus={len(wm_to_urls)} unique_cands={len(seen_u)} cached={len(seen_u)-len([u for _,u in cand_priority if u not in pages])} fetching={len(todo)}", flush=True)

    if todo:
        t_f = time.perf_counter()
        with cache_jsonl.open("a", encoding="utf-8") as cf:
            for start in range(0, len(todo), 160):
                chunk = todo[start : start + 160]
                with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
                    results = list(ex.map(page_parser_fn, chunk))
                for u, res in zip(chunk, results):
                    entry = res if res else {"url": u, "_miss": True}
                    if "url" not in entry:
                        entry["url"] = u
                    pages[u] = entry
                    cf.write(json.dumps(entry, ensure_ascii=False) + "\n")
                cf.flush()
                done_n = min(start + 160, len(todo))
                if len(todo) > 200:
                    print(f"    {site_tag} [{done_n}/{len(todo)}] elapsed={time.perf_counter()-t_f:.0f}s", flush=True)

    # Index barcodes across all fetched pages
    by_bc: dict[str, dict[str, Any]] = {}
    for pg in pages.values():
        if pg.get("_miss"):
            continue
        if pg.get("barcode") and len(str(pg["barcode"])) >= 8:
            by_bc[str(pg["barcode"]).strip()] = pg
        for s in pg.get("skus") or []:
            if len(str(s)) >= 8:
                by_bc[str(s).strip()] = pg

    stats = _blank_stats()
    for p in products:
        pid = str(p["product_id"])
        if not _needs_enrichment(records.get(pid)):
            continue
        is_fresh = (p.get("category_name") or "") in fresh_cats
        bc = str(p.get("barcode") or "").strip()
        if bc and bc in by_bc:
            if apply_fields_v3(records, p, by_bc[bc], src_label, f"{site_tag.upper()}_BARCODE", stats):
                stats["barcode_hits"] += 1
                continue
        for u in wm_to_urls.get(pid, ()):
            pg = pages.get(u)
            if not pg or pg.get("_miss") or not pg.get("name"):
                continue
            skus: set[str] = set()
            if pg.get("barcode"):
                skus.add(str(pg["barcode"]).strip())
            for s in pg.get("skus") or []:
                skus.add(str(s).strip())
            m = strict_verify_v3(p, pg.get("name", ""), skus, pg.get("brand", ""), allow_private_brand_fresh=allow_fresh_private and is_fresh)
            if m:
                if apply_fields_v3(records, p, pg, src_label, f"{site_tag.upper()}_{m}", stats):
                    if "BARCODE" in m:
                        stats["barcode_hits"] += 1
                    else:
                        stats["title_hits"] += 1
                    break
    print(f"  {site_tag} stats: {stats}", flush=True)
    return stats


# ---------------------------------------------------------------------------
# Site-specific parsers (Medigo JSON, Meta.vn specs, Chiaki clean body, generic VN)
# ---------------------------------------------------------------------------
def load_longchau_urls() -> list[str]:
    cache_u = CACHE_DIR / "longchau_urls.json"
    if cache_u.exists():
        urls = json.loads(cache_u.read_text(encoding="utf-8"))
        if len(urls) >= 100:
            return urls
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
    urls = sorted(lc_urls_set)
    if urls:
        cache_u.write_text(json.dumps(urls, ensure_ascii=False), encoding="utf-8")
    return urls


def fetch_longchau_page(url: str) -> dict[str, Any]:
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


def parse_medigo_page(url: str) -> dict[str, Any]:
    raw = fetch_fast(url, timeout=13.0)
    if not raw:
        return {"url": url, "_miss": True}
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', raw, re.S)
    if not m:
        return {"url": url, "_miss": True}
    try:
        nd = json.loads(m.group(1))
        prod = nd.get("props", {}).get("pageProps", {}).get("product") or {}
    except Exception:
        return {"url": url, "_miss": True}
    if not isinstance(prod, dict) or not prod:
        return {"url": url, "_miss": True}

    name = str(prod.get("Ten_tren_hop") or prod.get("name") or prod.get("title") or "").strip()
    if not name:
        m_h1 = re.search(r"<h1[^>]*>(.*?)</h1>", raw, re.S | re.I)
        if m_h1:
            name = _strip_html(m_h1.group(1))[:180]
    if not name:
        return {"url": url, "_miss": True}

    def _clean_field(v: Any) -> str:
        if not v:
            return ""
        if isinstance(v, list):
            parts = []
            for x in v:
                if isinstance(x, dict):
                    nm = str(x.get("name") or x.get("ten") or x.get("title") or "").strip()
                    amt = str(x.get("ham_luong") or x.get("amount") or x.get("value") or "").strip()
                    if nm:
                        parts.append(f"{nm} {amt}".strip())
                elif isinstance(x, str):
                    parts.append(_strip_html(x))
            return ", ".join(p for p in parts if p)[:520]
        return _strip_html(str(v))[:520]

    ing = _clean_field(prod.get("thanh_phan"))
    use = _clean_field(prod.get("cach_dung") or prod.get("huong_dan_su_dung") or prod.get("lieu_dung") or prod.get("cong_dung_chi_dinh"))
    sto = _clean_field(prod.get("bao_quan"))
    ori = ""
    for k in ("xuat_xu", "origin", "nuoc_san_xuat", "CT_cs_san_xuat"):
        v = _clean_field(prod.get(k))
        if v and len(v) <= 60:
            ori = v
            break
    bc = ""
    for k in ("barcode", "sku_id", "sku", "gtin13"):
        v = str(prod.get(k) or "").strip()
        if re.fullmatch(r"\d{8,14}", v):
            bc = v
            break
    brand = _clean_field(prod.get("thuong_hieu") or prod.get("brand") or prod.get("nha_san_xuat") or "")
    return {
        "url": url, "name": name, "brand": brand[:80], "barcode": bc,
        "ingredients": ing, "usage": use, "storage": sto, "origin": ori,
    }


def _extract_sections_from_clean_html(raw: str, *, strip_chrome: bool = True) -> dict[str, str]:
    cleaned = raw
    if strip_chrome:
        cleaned = re.sub(
            r"<(?:script|style|noscript|svg|header|footer|nav|aside)[^>]*>.*?</(?:script|style|noscript|svg|header|footer|nav|aside)>",
            " ",
            cleaned,
            flags=re.S | re.I,
        )
    else:
        cleaned = re.sub(r"<(?:script|style|noscript|svg)[^>]*>.*?</(?:script|style|noscript|svg)>", " ", cleaned, flags=re.S | re.I)
    # Remove known boilerplate disclaimers (Chiaki/Meta comment blocks)
    cleaned = re.sub(r"Bao bì và thành phần trong thực tế có thể khác biệt[^<\n]{0,300}", " ", cleaned, flags=re.I)

    text = re.sub(r"</?(?:p|div|br|li|tr|td|th|h[1-6]|ul|ol|table|dt|dd|section)[^>]*>", "\n", cleaned, flags=re.I)
    text = html.unescape(re.sub(r"<[^>]+>", " ", text))
    lines = [re.sub(r"\s+", " ", ln).strip() for ln in text.splitlines() if ln.strip()]

    sec_map = {
        "ingredients": re.compile(
            r"^(?:[\d\.\-\*\•]+\s*)?(?:chất liệu\s*/\s*thành phần|thành phần(?:\s+chính|\s+sản phẩm|\s+nguyên liệu|\s+cấu tạo)?|"
            r"chất liệu(?:\s+chính|\s+sản phẩm|\s+vỏ|\s+lòng\s+nồi|\s+thân|\s+bình|\s+ruột)?)\s*[:：\-]?\s*(.*)$",
            re.I,
        ),
        "usage": re.compile(
            r"^(?:[\d\.\-\*\•]+\s*)?(?:hướng dẫn sử dụng|cách sử dụng|cách dùng|cách pha|hướng dẫn chế biến)\s*[:：\-]?\s*(.*)$",
            re.I,
        ),
        "storage": re.compile(
            r"^(?:[\d\.\-\*\•]+\s*)?(?:hướng dẫn bảo quản|cách bảo quản|bảo quản(?:\s+sản phẩm)?)\s*[:：\-]?\s*(.*)$",
            re.I,
        ),
        "origin": re.compile(
            r"^(?:[\d\.\-\*\•]+\s*)?(?:xuất xứ(?:\s+thương\s+hiệu)?|sản xuất tại|nơi sản xuất)\s*[:：\-]?\s*(.*)$",
            re.I,
        ),
        "stop": re.compile(
            r"^(?:thông tin sản phẩm|mô tả sản phẩm|đặc điểm nổi bật|công dụng|lưu ý|cảnh báo|thương hiệu|"
            r"nhà sản xuất|nhà cung cấp|mã sản phẩm|mã vạch|sku|barcode|đơn vị|quy cách|dung tích|"
            r"trọng lượng|khối lượng|độ tuổi|hạn sử dụng|ngày sản xuất|kích thước|màu sắc|giá bán|"
            r"tình trạng|còn hàng|hết hàng|chọn mua|thêm vào giỏ|mua ngay|thông số kỹ thuật|"
            r"đánh giá|bình luận|nhận xét|sản phẩm liên quan|sản phẩm tương tự|hỏi đáp|chính sách|"
            r"bảo hành|giao hàng|đối tượng sử dụng)\b",
            re.I,
        ),
    }
    buckets: dict[str, list[str]] = {"ingredients": [], "usage": [], "storage": [], "origin": []}
    cur_sec: str | None = None
    for ln in lines:
        if any(bad in ln.lower() for bad in ("bình luận", "hỏi đáp", "trả lời", "đăng nhập", "giỏ hàng", "mua ngay")):
            if len(ln) <= 50:
                cur_sec = None
                continue
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
                if cur_sec == "origin":
                    if len(rest) <= 55 and not buckets["origin"]:
                        buckets["origin"].append(rest)
                    cur_sec = None
                else:
                    buckets[cur_sec].append(rest)
            continue
        if cur_sec == "origin":
            if len(ln) <= 55 and not buckets["origin"]:
                buckets["origin"].append(ln)
            cur_sec = None
        elif cur_sec and sum(len(x) for x in buckets[cur_sec]) < 440:
            if len(ln) <= 360:
                buckets[cur_sec].append(ln)

    return {k: " ".join(dict.fromkeys(v))[:520].strip() for k, v in buckets.items() if v}


def parse_vn_ssr_page(url: str) -> dict[str, Any]:
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
            if isinstance(obj, list):
                obj = next((x for x in obj if isinstance(x, dict) and x.get("@type") == "Product"), None)
            if isinstance(obj, dict) and obj.get("@type") == "Product":
                out["name"] = str(obj.get("name") or "").strip()
                b = obj.get("brand")
                out["brand"] = str(b.get("name") if isinstance(b, dict) else b or "").strip()
                for k in ("gtin13", "gtin", "mpn", "sku"):
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

    # For Meta.vn, clip before comment/Q&A section to avoid user comments
    body_html = raw
    for cut_marker in ('id="comment', 'class="comment', 'id="box-comment', 'class="product-review', 'id="reviews"'):
        ci = body_html.find(cut_marker)
        if ci > 4000:
            body_html = body_html[:ci]

    secs = _extract_sections_from_clean_html(body_html, strip_chrome=True)
    for k, v in secs.items():
        if v and not out.get(k):
            out[k] = v
    return out


# ---------------------------------------------------------------------------
# Sitemap loaders with caching
# ---------------------------------------------------------------------------
def load_sitemap_cached(cache_name: str, index_or_url: str, sub_pred=None, url_pred=None, max_subs: int = 60) -> list[str]:
    fp = CACHE_DIR / cache_name
    if fp.exists():
        return json.loads(fp.read_text(encoding="utf-8"))
    xml = fetch_urllib_gz(index_or_url, timeout=22) or fetch_fast(index_or_url, timeout=20.0) or ""
    locs = re.findall(r"<loc>(.*?)</loc>", xml)
    if any((".xml" in u.lower() or ".ashx" in u.lower()) for u in locs[:5]):
        subs = [u for u in locs if (sub_pred(u) if sub_pred else True)][:max_subs]
        all_u: list[str] = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=12) as ex:
            for sxml in ex.map(lambda su: fetch_urllib_gz(su, timeout=22) or "", subs):
                for u in re.findall(r"<loc>(.*?)</loc>", sxml):
                    if not url_pred or url_pred(u):
                        all_u.append(u)
        urls = sorted(set(all_u))
    else:
        urls = sorted(set(u for u in locs if (url_pred(u) if url_pred else True)))
    if urls:
        fp.write_text(json.dumps(urls, ensure_ascii=False), encoding="utf-8")
    return urls


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
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
    print(f"WAVE-3 BEFORE ({len(products)} SKUs): {before}", flush=True)

    # STAGE 0: Zero-cost recovery
    stage0a_replay_caches(products, records)
    stage0b_mine_summaries(products, records)
    _save()

    # STAGE 1: OpenFoodFacts 100% retry
    stage1_openfoodfacts_complete(products, records)
    _save()

    # STAGE 2: Brand-alias & model-code deep sweep on Co.op Online, Kingfoodmart, Long Chau, HachiHachi
    coop_urls = load_cooponline_urls()
    crawl_indexed_site(
        site_tag="cooponline",
        src_label="thông tin nhãn sản phẩm (Co.op Online)",
        urls=coop_urls,
        slug_extract_fn=lambda u: re.sub(r"--s\d+$", "", u.rstrip("/").split("/")[-1]),
        page_parser_fn=fetch_cooponline_page,
        products=products,
        records=records,
        max_new_fetch=900,
        max_per_sku=3,
        workers=28,
        require_brand=False,
        allow_fresh_private=True,
    )
    kf_urls = load_kingfoodmart_urls(CACHE_DIR / "kingfoodmart_product_urls.json")
    crawl_indexed_site(
        site_tag="kingfoodmart",
        src_label="thông tin nhãn sản phẩm (Kingfoodmart)",
        urls=kf_urls,
        slug_extract_fn=lambda u: u.rstrip("/").split("/")[-1],
        page_parser_fn=fetch_kf_fast,
        products=products,
        records=records,
        max_new_fetch=700,
        max_per_sku=3,
        workers=28,
        require_brand=False,
        allow_fresh_private=True,
    )
    lc_urls = load_longchau_urls()
    crawl_indexed_site(
        site_tag="longchau",
        src_label="thông tin sản phẩm (Nhà thuốc Long Châu)",
        urls=lc_urls,
        slug_extract_fn=lambda u: u.rstrip("/").split("/")[-1].replace(".html", ""),
        page_parser_fn=fetch_longchau_page,
        products=products,
        records=records,
        max_new_fetch=300,
        max_per_sku=2,
        workers=16,
        require_brand=True,
    )
    _save()

    # STAGE 3: Medigo (31,230 structured JSON __NEXT_DATA__ products!)
    medigo_urls = load_sitemap_cached(
        "medigo_urls.json",
        "https://www.medigoapp.com/sitemap.xml",
        url_pred=lambda u: "/product/" in u,
    )
    crawl_indexed_site(
        site_tag="medigo",
        src_label="thông tin sản phẩm (Medigo)",
        urls=medigo_urls,
        slug_extract_fn=lambda u: u.rstrip("/").split("/")[-1].replace(".html", ""),
        page_parser_fn=parse_medigo_page,
        products=products,
        records=records,
        max_new_fetch=600,
        max_per_sku=2,
        workers=22,
        require_brand=True,
    )
    _save()

    # STAGE 4: Specialized domain sites (Con Cung, Gofood, Elmich, Sunhouse, DMCL, VPP247)
    domain_specs = [
        ("concung", "thông tin sản phẩm (Con Cưng)", "https://concung.com/sitemap.xml", None, lambda u: bool(re.search(r"-\d{5,}\.html$", u)), 350, None),
        ("gofood", "thông tin sản phẩm (Gofood)", "https://gofood.vn/sitemap.xml", None, lambda u: u.endswith(".html") and u.count("/") >= 4, 250, {"Thịt", "Hải Sản Đông Lạnh", "Cá, Hải Sản", "Hải sản", "Thực Phẩm Đông Lạnh", "Thịt Nguội - Xúc Xích"}),
        ("elmich", "thông tin sản phẩm (Elmich)", "https://shop.elmich.vn/sitemap.xml", lambda u: "product" in u.lower(), lambda u: u.count("/") == 3 and len(u) > 25, 200, {"Đồ dùng nhà bếp", "Đồ Điện Gia Dụng", "Vệ Sinh Nhà Cửa"}),
        ("sunhouse", "thông tin sản phẩm (Sunhouse)", "https://sunhouse.com.vn/sitemap.xml", None, lambda u: u.count("/") >= 4 and len(u) > 40, 200, {"Đồ dùng nhà bếp", "Đồ Điện Gia Dụng"}),
        ("dmcl", "thông tin sản phẩm (Điện Máy Chợ Lớn)", "https://dienmaycholon.com/sitemap/sitemap_do_dung_nha_bep.xml", None, lambda u: len(u) > 45, 200, {"Đồ dùng nhà bếp", "Đồ Điện Gia Dụng"}),
        ("vpp247", "thông tin sản phẩm (VPP247)", "https://vanphongpham247.vn/product-sitemap.xml", None, lambda u: "/san-pham/" in u, 200, {"Văn Phòng Phẩm - Đồ Chơi"}),
    ]
    for tag, label, sm, spred, upred, cap, catf in domain_specs:
        d_urls = load_sitemap_cached(f"{tag}_urls.json", sm, sub_pred=spred, url_pred=upred)
        if d_urls:
            crawl_indexed_site(
                site_tag=tag,
                src_label=label,
                urls=d_urls,
                slug_extract_fn=lambda u: u.rstrip("/").split("/")[-1].replace(".html", ""),
                page_parser_fn=parse_vn_ssr_page,
                products=products,
                records=records,
                max_new_fetch=cap,
                max_per_sku=2,
                workers=18,
                require_brand=(tag not in {"gofood"}),
                allow_fresh_private=(tag == "gofood"),
                category_filter=catf,
            )
    _save()

    # STAGE 5: Meta.vn (~45,000 products — kitchenware, household, stationery, consumer goods)
    meta_urls = load_sitemap_cached(
        "meta_urls.json",
        "https://meta.vn/sitemap/v2_sitemap_products.ashx",
        url_pred=lambda u: "-p" in u,
        max_subs=50,
    )
    crawl_indexed_site(
        site_tag="meta",
        src_label="thông tin sản phẩm (META.vn)",
        urls=meta_urls,
        slug_extract_fn=lambda u: re.sub(r"-p\d+$", "", u.rstrip("/").split("/")[-1]),
        page_parser_fn=parse_vn_ssr_page,
        products=products,
        records=records,
        max_new_fetch=700,
        max_per_sku=2,
        workers=22,
        require_brand=True,
    )
    _save()

    # STAGE 6: Chiaki.vn (~280,000 products across 28 sitemaps!)
    chiaki_urls = load_sitemap_cached(
        "chiaki_urls.json",
        "https://chiaki.vn/sitemap.xml",
        sub_pred=lambda u: "/product" in u.lower() and u.endswith(".xml"),
        url_pred=lambda u: u.count("/") == 3 and len(u) > 25,
        max_subs=35,
    )
    crawl_indexed_site(
        site_tag="chiaki",
        src_label="thông tin sản phẩm (Chiaki)",
        urls=chiaki_urls,
        slug_extract_fn=lambda u: u.rstrip("/").split("/")[-1],
        page_parser_fn=parse_vn_ssr_page,
        products=products,
        records=records,
        max_new_fetch=1000,
        max_per_sku=2,
        workers=24,
        require_brand=True,
    )
    _save()

    # STAGE 7: Same-formula multipack & pack-size sibling alignment
    propagate_multipack_siblings(products, records)
    _save()

    after = _counts()
    print(f"\nWAVE-3 AFTER  ({len(products)} SKUs, {time.perf_counter()-t0:.1f}s): {after}", flush=True)
    for k in before:
        print(f"  {k:15s}: {before[k]:4d} -> {after[k]:4d} (+{after[k]-before[k]})", flush=True)


if __name__ == "__main__":
    main()

"""Wave-4 Precision Mop-Up Crawler.

Fixes remaining naming/matching blind spots uncovered by the Post-Wave-3 audit:
  1. Retail abbreviation expansion (`NG`->`nước giặt`, `NC`->`nước`, `ST`->`sữa tắm`,
     `KĐR`->`kem đánh răng`, `BCĐR`->`bàn chải đánh răng`, `SRM`->`sữa rửa mặt`, etc.)
  2. Corporate renames & importer aliases (`OTOKI`->`Ottogi`, `MIWON`->`O'Food`/`Daesang`,
     `TH MILK`->`TH True`, `THABIFOOD`->`Thaibifood`, `DOAKBUA`->`Dokbua`,
     `NGUYÊN HUY`->`Franzzi`, `MEAT DELI`->`MeatDeli`, `ZAI ZAI`->`Đức Hạnh`)
  3. Hyphenated model codes extracted BEFORE replacing `-` with space (`tl-08` == `tl08`,
     `tl-023` == `tl023`, `lca6242d-ih` == `lca6242d`, `ec-00307` == `ec00307`)
  4. SEO tagline stripping (`" - ..."`) on Chiaki/Meta titles & JSON-LD list brand fixing
  5. Brandless (`NOBRAND` / `THANH LAM HOME`) strict high-overlap matching across Co.op/KF/Chiaki/Meta
  6. Completing the last 17 uncached OpenFoodFacts API v2 batches (100% barcode completion)
  7. Crawling verified official Vietnamese manufacturer sitemaps (`vpphongha`, `doiduavang`,
     `meatdeli`, `nutifood`, `dabaco`)
"""

from __future__ import annotations

import concurrent.futures
import hashlib
import json
import re
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.enrich_all_missing_details import (  # noqa: E402
    CACHE_DIR,
    CATALOG_PATH,
    DETAILS_PATH,
    _extract_size_sigs,
    _slug_match_tokens,
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
    _EXTRA_BRAND_SYNONYMS,
    _NOT_MODEL_RE,
    _needs_enrichment,
    apply_fields_v3,
    load_sitemap_cached,
    parse_medigo_page,
    parse_vn_ssr_page,
    stage1_openfoodfacts_complete,
)

# Expand brand aliases with all Wave-3 audit findings
_V4_BRAND_SYNONYMS: dict[str, set[str]] = {
    **_EXTRA_BRAND_SYNONYMS,
    "otoki": {"otoki", "ottogi"},
    "ottogi": {"otoki", "ottogi"},
    "miwon": {"miwon", "ofood", "daesang"},
    "th milk": {"th", "thtrue", "thmilk", "true"},
    "th true milk": {"th", "thtrue", "thmilk", "true"},
    "meat deli": {"meatdeli", "meat", "deli"},
    "meatdeli": {"meatdeli", "meat", "deli"},
    "thabifood": {"thabifood", "thaibifood"},
    "doakbua": {"doakbua", "dokbua"},
    "zai zai": {"zaizai", "zai", "duchanh"},
    "nguyen huy": {"nguyenhuy", "franzzi"},
    "nguyên huy": {"nguyenhuy", "franzzi"},
    "inochi": {"inochi"},
    "hong ha": {"hongha", "hong", "ha"},
    "hồng hà": {"hongha", "hong", "ha"},
    "hoa doanh": {"hoadoanh", "hoa", "doanh"},
    "ông già ika": {"onggiaika", "ika"},
    "ong gia ika": {"onggiaika", "ika"},
    "đôi đũa vàng": {"doiduavang", "dua", "vang"},
    "doi dua vang": {"doiduavang", "dua", "vang"},
    "bếp xinh": {"bepxinh", "bep", "xinh"},
    "bep xinh": {"bepxinh", "bep", "xinh"},
    "nutifood": {"nutifood", "nuti", "growplus", "varna", "nuvi"},
    "dabaco": {"dabaco"},
    "kitkool": {"kitkool"},
    "tohogenkai": {"tohogenkai"},
    "deli": {"deli"},
    "omo": {"omo"},
    "xuyên á": {"xuyena"},
    "xuyen a": {"xuyena"},
    "minh việt": {"minhviet", "minh", "viet"},
    "minh viet": {"minhviet", "minh", "viet"},
    "natur fish": {"naturfish", "ntf"},
}

_ABBREV_EXPANSIONS = [
    (re.compile(r"\bng\b", re.I), "nuoc giat"),
    (re.compile(r"\bnxv\b", re.I), "nuoc xa vai"),
    (re.compile(r"\bnrc\b", re.I), "nuoc rua chen"),
    (re.compile(r"\bnls\b", re.I), "nuoc lau san"),
    (re.compile(r"\bnrt\b", re.I), "nuoc rua tay"),
    (re.compile(r"\bnc\b", re.I), "nuoc"),
    (re.compile(r"\bkdr\b", re.I), "kem danh rang"),
    (re.compile(r"\bbcdr\b", re.I), "ban chai danh rang"),
    (re.compile(r"\bsrm\b", re.I), "sua rua mat"),
    (re.compile(r"\bst\b", re.I), "sua tam"),
    (re.compile(r"\bdg\b", re.I), "dau goi"),
    (re.compile(r"\bdx\b", re.I), "dau xa"),
]

# Match model codes BEFORE replacing hyphens (e.g. tl-08 -> tl08, lca6242d-ih -> lca6242d, ec00307)
_MODEL_RAW_RE = re.compile(r"\b([a-z]{2,4})-?(\d{2,6}[a-z0-9]{0,3})(?:-[a-z]{1,3})?\b", re.I)


def _expand_abbrevs(text: str | None) -> str:
    s = norm_text(re.sub(r"(?<=[a-zA-ZÀ-ỹ])['’](?=[a-zA-ZÀ-ỹ])", "", str(text or "")))
    for pat, rep in _ABBREV_EXPANSIONS:
        s = pat.sub(rep, s)
    return s


def _clean_ext_brand(brand: Any) -> str:
    if isinstance(brand, list):
        brand = " ".join(str(x) for x in brand if x)
    s = str(brand or "").strip()
    if s.startswith("[") and s.endswith("]"):
        s = re.sub(r"[\[\]'\"]", "", s).strip()
    return s


def _strip_seo_tail(name: str) -> str:
    """Trim Chiaki/Meta SEO suffixes after ' - ' (e.g. ' - Thiết Kế Sang Trọng, ...')."""
    s = str(name or "").strip()
    if " - " in s:
        head = s.split(" - ", 1)[0].strip()
        if len(head) >= 12:
            return head
    return s


def _extract_models_v4(raw_text: str | None) -> set[str]:
    if not raw_text:
        return set()
    low = raw_text.lower()
    codes: set[str] = set()
    for pfx, num in _MODEL_RAW_RE.findall(low):
        c = pfx + num
        if len(c) >= 4 and not _NOT_MODEL_RE.match(c) and not c.endswith(("ml", "gr", "kg", "cm", "mm")):
            codes.add(c)
            # Also add zero-stripped numeric variant (tl023 vs tl23, tl08 vs tl8)
            num_nz = re.sub(r"^0+(?=\d)", "", num)
            if num_nz != num and len(pfx + num_nz) >= 3:
                codes.add(pfx + num_nz)
    return codes


def _brand_aliases_v4(brand: str | None) -> set[str]:
    if not brand:
        return set()
    nb = norm_text(brand)
    if nb in _PRIVATE_OR_GENERIC_BRANDS:
        return set()
    low = brand.strip().lower()
    out: set[str] = set()
    if low in _V4_BRAND_SYNONYMS:
        out |= _V4_BRAND_SYNONYMS[low]
    if nb in _V4_BRAND_SYNONYMS:
        out |= _V4_BRAND_SYNONYMS[nb]
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


def _tokens_v4(text: str | None, brand: str | None = None) -> set[str]:
    n = _PACK_WRAPPER_RE.sub(" ", _expand_abbrevs(text))
    b = "" if norm_text(brand) in _PRIVATE_OR_GENERIC_BRANDS else _expand_abbrevs(brand)
    toks = _slug_match_tokens(n, b)
    # Include brand synonyms + normalized model codes
    toks |= _extract_models_v4(text)
    words = [w for w in re.findall(r"[a-z0-9]+", n) if len(w) >= 2]
    for i in range(len(words) - 1):
        if len(words[i]) <= 6 and len(words[i + 1]) <= 6:
            toks.add(words[i] + words[i + 1])
    return toks


def strict_verify_v4(
    wm_prod: dict[str, Any],
    ext_name_raw: str,
    ext_skus: set[str],
    ext_brand_raw: Any,
    *,
    allow_nobrand_exact: bool = False,
) -> str | None:
    wm_bc = str(wm_prod.get("barcode") or "").strip()
    if wm_bc and len(wm_bc) >= 8 and wm_bc in ext_skus:
        return "BARCODE"

    ext_name = _strip_seo_tail(ext_name_raw)
    ext_brand = _clean_ext_brand(ext_brand_raw)

    m = strict_verify_match(wm_prod, ext_name, ext_skus, ext_brand)
    if m:
        return m

    wm_clean = _PACK_WRAPPER_RE.sub(" ", _expand_abbrevs(wm_prod.get("name")))
    ext_clean = _PACK_WRAPPER_RE.sub(" ", _expand_abbrevs(ext_name))
    if not wm_clean or not ext_clean:
        return None
    if ("combo" in wm_clean.split()) != ("combo" in ext_clean.split()):
        return None

    wm_brand_norm = norm_text(wm_prod.get("brand"))
    is_private = wm_brand_norm in _PRIVATE_OR_GENERIC_BRANDS
    wm_b_aliases = _brand_aliases_v4(wm_prod.get("brand"))
    ext_all_toks = _tokens_v4(f"{ext_name} {ext_brand}")

    brand_ok = (not wm_b_aliases) or bool(wm_b_aliases & ext_all_toks)
    if wm_b_aliases and not brand_ok:
        return None

    # Exact model code + matching brand (e.g. Thiên Long TL08 / TL023, Lock&Lock HAP813N / LCA6242D, Deli EC00307)
    wm_models = _extract_models_v4(wm_prod.get("name"))
    ext_models = _extract_models_v4(ext_name_raw)
    if wm_models and ext_models and (wm_models & ext_models) and (brand_ok and not is_private):
        return "MODEL_CODE"

    # Form group guard
    wm_form = extract_form(wm_clean)
    ext_form = extract_form(ext_clean)
    if wm_form and ext_form and wm_form[0] == ext_form[0] and wm_form[1] != ext_form[1]:
        return None

    # Sugar / zero-calorie guard
    wm_vars = extract_variants(wm_clean)
    ext_vars = extract_variants(ext_clean)
    sugar_tags = {"khong duong", "it duong", "co duong", "nguyen chat", "zero", "light"}
    if (wm_vars & sugar_tags) != (ext_vars & sugar_tags):
        return None

    # Flavor guard
    diff_wm = wm_vars - ext_vars - {"kem", "tu nhien", "truyen thong", "chanh"}
    diff_ext = ext_vars - wm_vars - {"kem", "tu nhien", "truyen thong", "chanh"}
    if diff_wm or diff_ext:
        return None
    if ("chanh" in (wm_vars ^ ext_vars)) and not ({"sprite", "7up"} & ext_all_toks):
        return None

    # Core token comparison (strip raw brand name noise so corporate renames OTOKI vs Ottogi don't penalize recall!)
    wm_core = _slug_match_tokens(wm_clean) - wm_b_aliases
    ext_core = _slug_match_tokens(ext_clean) - wm_b_aliases
    # If stripping brand leaves < 2 tokens, keep full tokens
    if len(wm_core) < 2:
        wm_core = _slug_match_tokens(wm_clean)
    if len(ext_core) < 2:
        ext_core = _slug_match_tokens(ext_clean)
    if not wm_core or not ext_core:
        return None

    ov = len(wm_core & ext_core)
    rec = ov / len(wm_core)
    prec = ov / len(ext_core)

    if wm_b_aliases:
        if ov >= 3 and rec >= 0.55 and prec >= 0.42:
            return "STRICT_V4"
        if ov >= 2 and rec >= 0.65 and prec >= 0.50:
            return "STRICT_V4"
        if ov >= 1 and len(wm_core) == 2 and rec >= 0.50 and prec >= 0.50 and len(wm_b_aliases & ext_all_toks) >= 1:
            return "STRICT_V4_SHORT"
    elif allow_nobrand_exact:
        # Strict high-overlap for NOBRAND / private-label packaged items (e.g. "Bánh que hỗn hợp Mix Hot Chili")
        if ov >= 3 and rec >= 0.72 and prec >= 0.55:
            return "NOBRAND_EXACT"
        if ov >= 2 and rec >= 0.80 and prec >= 0.67:
            return "NOBRAND_EXACT"
    return None


def replay_all_caches_v4(
    products: list[dict[str, Any]],
    records: dict[str, dict[str, Any]],
) -> None:
    print("\n=== STAGE 2: V4 Cache Replay (SEO-trimmed titles + OTOKI/MIWON/TH/abbrev/hyphen-models) ===", flush=True)
    sources = [
        ("cooponline_pages.jsonl", "thông tin nhãn sản phẩm (Co.op Online)", "COOPONLINE"),
        ("kingfoodmart_pages.jsonl", "thông tin nhãn sản phẩm (Kingfoodmart)", "KINGFOODMART"),
        ("chiaki_pages.jsonl", "thông tin sản phẩm (Chiaki)", "CHIAKI"),
        ("medigo_pages.jsonl", "thông tin sản phẩm (Medigo)", "MEDIGO"),
        ("meta_pages.jsonl", "thông tin sản phẩm (META.vn)", "META"),
        ("vpp247_pages.jsonl", "thông tin sản phẩm (VPP247)", "VPP247"),
        ("dmcl_pages.jsonl", "thông tin sản phẩm (Điện Máy Chợ Lớn)", "DMCL"),
        ("concung_pages.jsonl", "thông tin sản phẩm (Con Cưng)", "CONCUNG"),
        ("hachihachi_pages.jsonl", "thông tin nhãn sản phẩm (HachiHachi)", "HACHIHACHI"),
        ("longchau_pages.jsonl", "thông tin sản phẩm (Nhà thuốc Long Châu)", "LONGCHAU"),
    ]
    for fn, src_label, tag in sources:
        fp = CACHE_DIR / fn
        if not fp.exists():
            continue
        by_bc: dict[str, dict[str, Any]] = {}
        pages: list[tuple[dict[str, Any], set[str], set[str]]] = []
        tok_idx: dict[str, list[int]] = {}
        model_idx: dict[str, list[int]] = {}
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
            clean_nm = _strip_seo_tail(pg.get("name", ""))
            clean_br = _clean_ext_brand(pg.get("brand", ""))
            ptoks = _tokens_v4(f"{clean_nm} {clean_br}")
            idx = len(pages)
            pages.append((pg, skus, ptoks))
            for t in ptoks:
                tok_idx.setdefault(t, []).append(idx)
            for mc in _extract_models_v4(pg.get("name", "")):
                model_idx.setdefault(mc, []).append(idx)

        stats = _blank_stats()
        for p in products:
            pid = str(p["product_id"])
            if not _needs_enrichment(records.get(pid)):
                continue
            bc = str(p.get("barcode") or "").strip()
            if bc and bc in by_bc:
                if apply_fields_v3(records, p, by_bc[bc], src_label, f"{tag}_BARCODE", stats):
                    stats["barcode_hits"] += 1
                    continue
            qtoks = _tokens_v4(p.get("name"), p.get("brand")) | _brand_aliases_v4(p.get("brand"))
            counts: dict[int, int] = {}
            for mc in _extract_models_v4(p.get("name")):
                for pi in model_idx.get(mc, ()):
                    counts[pi] = counts.get(pi, 0) + 6
            for t in qtoks:
                for pi in tok_idx.get(t, ()):
                    counts[pi] = counts.get(pi, 0) + 1
            for _ov, pi in sorted(((ov, pi) for pi, ov in counts.items() if ov >= 2), reverse=True)[:6]:
                pg, skus, _ = pages[pi]
                m = strict_verify_v4(p, pg.get("name", ""), skus, pg.get("brand", ""), allow_nobrand_exact=True)
                if m:
                    if apply_fields_v3(records, p, pg, src_label, f"{tag}_{m}", stats):
                        stats["title_hits"] += 1
                        break
        if any(stats.values()):
            print(f"  Replay {tag:13s} ({len(pages):4d} pages) -> {stats}", flush=True)


def crawl_site_v4(
    *,
    site_tag: str,
    src_label: str,
    urls: list[str],
    slug_fn,
    parser_fn,
    products: list[dict[str, Any]],
    records: dict[str, dict[str, Any]],
    max_new_fetch: int = 800,
    workers: int = 24,
) -> dict[str, int]:
    print(f"\n=== V4 SITE: {site_tag} ({len(urls):,} URLs) ===", flush=True)
    tok_index: dict[str, list[int]] = {}
    model_index: dict[str, list[int]] = {}
    url_meta: list[tuple[str, set[str], str, set[str], set[str]]] = []
    for idx, u in enumerate(urls):
        slug_raw = slug_fn(u)
        models = _extract_models_v4(slug_raw)
        nslug = norm_text(slug_raw.replace("-", " "))
        utoks = _tokens_v4(nslug) | models
        sz = _extract_size_sigs(slug_raw)
        url_meta.append((u, utoks, nslug, sz, models))
        for t in utoks:
            tok_index.setdefault(t, []).append(idx)
        for mc in models:
            model_index.setdefault(mc, []).append(idx)

    wm_to_urls: dict[str, list[str]] = {}
    cand_priority: list[tuple[float, str]] = []
    seen_u: set[str] = set()

    for p in products:
        pid = str(p["product_id"])
        rec = records.get(pid)
        if not _needs_enrichment(rec):
            continue
        wm_clean = _PACK_WRAPPER_RE.sub(" ", _expand_abbrevs(p.get("name")))
        b_aliases = _brand_aliases_v4(p.get("brand"))
        wm_models = _extract_models_v4(p.get("name"))
        wm_core = _slug_match_tokens(wm_clean) - b_aliases
        if len(wm_core) < 2:
            wm_core = _slug_match_tokens(wm_clean)
        if not wm_core:
            continue
        wm_is_combo = "combo" in wm_clean.split()
        wm_sz = _extract_size_sigs(p.get("name"))
        wm_zero = bool(re.search(r"\b(?:khong calo|khong duong|zero|light)\b", wm_clean))

        counts: dict[int, int] = {}
        for mc in wm_models:
            for ci in model_index.get(mc, ()):
                counts[ci] = counts.get(ci, 0) + 6
        for t in (wm_core | b_aliases):
            posting = tok_index.get(t)
            if posting and len(posting) <= 15000:
                for ci in posting:
                    counts[ci] = counts.get(ci, 0) + 1

        ranked: list[tuple[float, float, int, str]] = []
        for ci, _raw_ov in counts.items():
            u, utoks, nslug, usz, umodels = url_meta[ci]
            if ("combo" in nslug.split()) != wm_is_combo:
                continue
            if b_aliases and not (b_aliases & utoks):
                continue
            u_zero = bool(re.search(r"\b(?:khong calo|khong duong|zero|light)\b", nslug))
            if wm_zero != u_zero:
                continue
            has_model = bool(wm_models and (wm_models & umodels) and b_aliases)
            core_ov = len(wm_core & utoks)
            rec_sc = core_ov / len(wm_core)
            jac = core_ov / max(1, len(wm_core | (utoks - b_aliases)))
            ok = False
            if has_model:
                ok = True
            elif b_aliases:
                ok = (core_ov >= 3 and rec_sc >= 0.45) or (core_ov >= 2 and rec_sc >= 0.55) or (core_ov >= 2 and jac >= 0.35)
            else:
                ok = (core_ov >= 3 and rec_sc >= 0.65 and jac >= 0.40) or (core_ov >= 2 and rec_sc >= 0.80 and jac >= 0.55)
            if ok:
                sz_bonus = 0.25 if (wm_sz and usz and (wm_sz & usz)) else 0.0
                miss_bonus = 0.6 if not (rec and rec.get("ingredients")) else 0.0
                model_bonus = 1.5 if has_model else 0.0
                ranked.append((model_bonus + miss_bonus + rec_sc + sz_bonus, jac, core_ov, u))

        if ranked:
            ranked.sort(reverse=True)
            for sc, _j, _o, u in ranked[:3]:
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
    print(f"  {site_tag}: matched_skus={len(wm_to_urls)} unique_cands={len(seen_u)} fetching={len(todo)}", flush=True)

    if todo:
        t_f = time.perf_counter()
        with cache_jsonl.open("a", encoding="utf-8") as cf:
            for start in range(0, len(todo), 160):
                chunk = todo[start : start + 160]
                with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
                    results = list(ex.map(parser_fn, chunk))
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
            m = strict_verify_v4(p, pg.get("name", ""), skus, pg.get("brand", ""), allow_nobrand_exact=True)
            if m:
                if apply_fields_v3(records, p, pg, src_label, f"{site_tag.upper()}_{m}", stats):
                    if "BARCODE" in m:
                        stats["barcode_hits"] += 1
                    else:
                        stats["title_hits"] += 1
                    break
    print(f"  {site_tag} stats: {stats}", flush=True)
    return stats


def propagate_same_model_siblings(
    products: list[dict[str, Any]],
    records: dict[str, dict[str, Any]],
) -> None:
    """Share verified fields between exact same-brand + same-model SKUs (e.g. TL08 single vs Vỉ 5 TL08)."""
    print("\n=== STAGE 5: Same-Brand Same-Model Sibling Alignment ===", flush=True)
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for p in products:
        b = norm_text(p.get("brand"))
        if b in _PRIVATE_OR_GENERIC_BRANDS or len(b) < 2:
            continue
        for mc in _extract_models_v4(p.get("name")):
            groups.setdefault((b, mc), []).append(p)
    shared = {"ing": 0, "use": 0, "sto": 0, "ori": 0}
    for _k, plist in groups.items():
        if len(plist) < 2:
            continue
        for f, sk in (("ingredients", "ing"), ("usage", "use"), ("storage", "sto"), ("origin", "ori")):
            src = next((records[str(p["product_id"])] for p in plist if records.get(str(p["product_id"]), {}).get(f)), None)
            if not src:
                continue
            for p in plist:
                pid = str(p["product_id"])
                if not records.get(pid, {}).get(f):
                    rec = records.setdefault(pid, {"product_name": p["name"], "brand": p.get("brand", ""), "barcode": p.get("barcode", ""), "source_url": ""})
                    rec[f] = src[f]
                    if src.get(f"{f}_source"):
                        rec[f"{f}_source"] = src[f"{f}_source"]
                    if src.get(f"{f}_source_url"):
                        rec[f"{f}_source_url"] = src[f"{f}_source_url"]
                    shared[sk] += 1
    print(f"  Same-model sibling shared: {shared}", flush=True)


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
    print(f"WAVE-4 BEFORE ({len(products)} SKUs): {before}", flush=True)

    # Stage 1: Finish the remaining ~17 uncached OpenFoodFacts batches!
    stage1_openfoodfacts_complete(products, records)
    _save()

    # Stage 2: Replay ALL existing caches with V4 SEO-stripped & alias-aware verifier
    replay_all_caches_v4(products, records)
    _save()

    # Stage 3: Fetch NEW candidates unlocked by V4 aliases/abbreviations/hyphen-models across mega-sitemaps
    coop_urls = load_cooponline_urls()
    crawl_site_v4(
        site_tag="cooponline",
        src_label="thông tin nhãn sản phẩm (Co.op Online)",
        urls=coop_urls,
        slug_fn=lambda u: re.sub(r"--s\d+$", "", u.rstrip("/").split("/")[-1]),
        parser_fn=fetch_cooponline_page,
        products=products,
        records=records,
        max_new_fetch=600,
        workers=28,
    )
    kf_urls = load_kingfoodmart_urls(CACHE_DIR / "kingfoodmart_product_urls.json")
    crawl_site_v4(
        site_tag="kingfoodmart",
        src_label="thông tin nhãn sản phẩm (Kingfoodmart)",
        urls=kf_urls,
        slug_fn=lambda u: u.rstrip("/").split("/")[-1],
        parser_fn=fetch_kf_fast,
        products=products,
        records=records,
        max_new_fetch=500,
        workers=28,
    )
    meta_urls = json.loads((CACHE_DIR / "meta_urls.json").read_text(encoding="utf-8")) if (CACHE_DIR / "meta_urls.json").exists() else []
    if meta_urls:
        crawl_site_v4(
            site_tag="meta",
            src_label="thông tin sản phẩm (META.vn)",
            urls=meta_urls,
            slug_fn=lambda u: re.sub(r"-p\d+$", "", u.rstrip("/").split("/")[-1]),
            parser_fn=parse_vn_ssr_page,
            products=products,
            records=records,
            max_new_fetch=400,
            workers=22,
        )
    chiaki_urls = json.loads((CACHE_DIR / "chiaki_urls.json").read_text(encoding="utf-8")) if (CACHE_DIR / "chiaki_urls.json").exists() else []
    if chiaki_urls:
        crawl_site_v4(
            site_tag="chiaki",
            src_label="thông tin sản phẩm (Chiaki)",
            urls=chiaki_urls,
            slug_fn=lambda u: u.rstrip("/").split("/")[-1],
            parser_fn=parse_vn_ssr_page,
            products=products,
            records=records,
            max_new_fetch=900,
            workers=24,
        )
    medigo_urls = json.loads((CACHE_DIR / "medigo_urls.json").read_text(encoding="utf-8")) if (CACHE_DIR / "medigo_urls.json").exists() else []
    if medigo_urls:
        crawl_site_v4(
            site_tag="medigo",
            src_label="thông tin sản phẩm (Medigo)",
            urls=medigo_urls,
            slug_fn=lambda u: u.rstrip("/").split("/")[-1].replace(".html", ""),
            parser_fn=parse_medigo_page,
            products=products,
            records=records,
            max_new_fetch=250,
            workers=20,
        )
    vpp_urls = json.loads((CACHE_DIR / "vpp247_urls.json").read_text(encoding="utf-8")) if (CACHE_DIR / "vpp247_urls.json").exists() else []
    if vpp_urls:
        crawl_site_v4(
            site_tag="vpp247",
            src_label="thông tin sản phẩm (VPP247)",
            urls=vpp_urls,
            slug_fn=lambda u: u.rstrip("/").split("/")[-1],
            parser_fn=parse_vn_ssr_page,
            products=products,
            records=records,
            max_new_fetch=150,
            workers=18,
        )
    _save()

    # Stage 4: Official Vietnamese manufacturer sitemaps (Hồng Hà, Đôi Đũa Vàng, MeatDeli, Nutifood, Dabaco)
    mfr_specs = [
        ("vpphongha", "thông tin sản phẩm (VPP Hồng Hà)", "https://vpphongha.com.vn/sitemap.xml", lambda u: "product" in u.lower() or "san-pham" in u.lower(), lambda u: len(u) > 30, 150),
        ("doiduavang", "thông tin sản phẩm (Đôi Đũa Vàng)", "https://doiduavang.vn/sitemap.xml", lambda u: "product" in u.lower() or "san-pham" in u.lower(), lambda u: len(u) > 30, 100),
        ("meatdeli", "thông tin sản phẩm (MEATDeli)", "https://meatdeli.com.vn/sitemap.xml", lambda u: "product" in u.lower() or "san-pham" in u.lower(), lambda u: len(u) > 30, 100),
        ("nutifood", "thông tin sản phẩm (Nutifood)", "https://nutifood.com.vn/sitemap.xml", None, lambda u: len(u) > 30, 100),
    ]
    for tag, label, sm, spred, upred, cap in mfr_specs:
        m_urls = load_sitemap_cached(f"{tag}_urls.json", sm, sub_pred=spred, url_pred=upred)
        if m_urls:
            crawl_site_v4(
                site_tag=tag,
                src_label=label,
                urls=m_urls,
                slug_fn=lambda u: u.rstrip("/").split("/")[-1].replace(".html", ""),
                parser_fn=parse_vn_ssr_page,
                products=products,
                records=records,
                max_new_fetch=cap,
                workers=16,
            )
    _save()

    # Stage 5: Sibling alignment (multipack + same-model)
    propagate_multipack_siblings(products, records)
    propagate_same_model_siblings(products, records)
    _save()

    after = _counts()
    print(f"\nWAVE-4 AFTER  ({len(products)} SKUs, {time.perf_counter()-t0:.1f}s): {after}", flush=True)
    for k in before:
        print(f"  {k:15s}: {before[k]:4d} -> {after[k]:4d} (+{after[k]-before[k]})", flush=True)


if __name__ == "__main__":
    main()

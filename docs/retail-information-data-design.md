# Retail information data and retrieval design

**Scope:** promotions, catalog additions, receipts, store and company facts, and
store-specific nearby places. General “Q&A about nearby places” is removed from the
customer assistant. The remaining nearby-place path is only a branch-scoped lookup of
attributed, verified snapshots when the user names or is bound to a specific store.

## What the repository currently has

`data/winmart_products.json` contains 3,099 product rows. It has product identifiers,
category, brand, regular/sale price, a free-text `discount_promotion`, stock, a crawler
`scraped_at`, and a placeholder shelf. It has no product launch date, receipt event,
physical branch profile, company profile, or coordinates. The current PostgreSQL
`store_product_snapshots` stores a per-product `discount_promotion` and source timestamp;
the importer assigns the single source code `WINMART_WEB` and marks shelves `SIMULATED`.
The latest catalog scrape is 2026-09-15; 580 rows contain a promotion string. These are
historical snapshot records, not confirmation that an offer is still running on 2026-10-18.

The timestamp on a crawl describes when the source snapshot was collected. It is not
evidence that a product was launched or physically received on that date.

## Research-backed modeling choices

1. **Keep product master facts separate from store snapshots and lifecycle events.**
   GS1's Global Data Model groups attributes into global core, global category, regional
   category, and country/local layers. Required product attributes can therefore vary
   by category and market; an absent ingredient, nutrition, use, or safety field must not
   be inferred from a generic description. See the [GS1 Global Data Model](https://www.gs1.org/services/gdsn/global-data-model)
   and its [lifecycle attributes](https://www.gs1.org/standards/gs1-global-data-model-attribute-implementation-guide/18).
2. **Use GTIN and GLN only when the source actually supplies them.** Keep internal product
   and store codes stable, and retain barcode/GTIN or GLN as external identifiers when
   verified. Do not fabricate a GS1 identifier from a local SKU or branch code. GS1
   defines GLN for legal entities and locations, including individual physical sites;
   identifiers for a site and its sublocations should remain distinguishable. See the
   [GS1 GLN overview](https://www.gs1.org/standards/id-keys/gln) and
   [physical location guidance](https://www.gs1.org/standards/id-keys/gln/physical-location).
3. **Treat receiving as an event, not a stock inference.** GS1 EPCIS records the what,
   when, where, and why of business events; a receiving event identifies the received
   items, event time, business location, and receiving step. A difference between two
   stock snapshots alone does not prove a delivery occurred. See the [GS1 EPCIS standard](https://ref.gs1.org/standards/epcis/2.0.1/).
4. **Model campaigns as structured, time-bounded records.** The present text field can
   support a product-level recorded offer, but not start/end times, terms, store scope,
   eligibility, or current-active status. Until those fields arrive from a verified
   promotion feed, responses must say “recorded in the catalog snapshot” and abstain on
   whether a campaign is still active.
5. **Separate chain, branch, and approved knowledge.** A company/brand profile belongs
   to an organization record; hours, address, and coordinates belong to a physical store
   record. Model each organization statement as a claim linked to its own source IDs;
   retain whether the basis is an official public source or team/user-provided context.
   This small provenance model follows the W3C PROV idea of attributing generated facts
   to entities, activities, and responsible agents, without claiming full PROV-O
   conformance. See [W3C PROV-O](https://www.w3.org/TR/prov-o/).
6. **Keep nearby lookups store-scoped.** General local-place Q&A and per-turn geocoding
   are out of scope. A specific-store lookup requires the kiosk-to-store mapping,
   verified coordinates, an attributed POI source, and an unexpired cached snapshot.

## Proposed data contract

| Data | Source of truth | Minimum fields for an agent answer |
|---|---|---|
| Product promotion record | Retail promotion/POS feed | Product or campaign ID, description, start/end, eligibility, store scope, source and captured time |
| Newly listed product | Two consecutive complete catalog imports | Product ID, current dataset version, previous dataset version, first-seen-in-catalog time |
| New stock receipt | Inventory/POS receiving events | Event ID, product, quantity, store, occurred-at time, receiving step, source document |
| Company facts | Approved company source or explicitly attributed team context | Organization ID, claim ID, localized claim, source IDs, source basis, verified-at date |
| Store facts | Store master system | Internal store ID, optional verified GLN, chain ID, name, address, hours, contact, coordinates, source and verified-at date |
| Nearby places | Curated/provider snapshot for a store | Store ID, place name/type, address, coordinates or distance, provider/source, captured-at date |

## Conversation behavior

- A named product promotion uses its product snapshot. A broad offer question lists only
  recorded product-level offers and states their data date and missing validity terms.
- “New product” means **newly present in the latest catalog compared with the previous
  complete import**. It does not mean newly launched in the market.
- “Newly stocked” means a receipt event was recorded. If receipt events are absent, ask a
  staff member or say the system cannot verify it; never infer a delivery from stock counts.
- Company questions are matched to an explicitly named entity. If the visitor only says
  “the company,” ask whether they mean the WinMart operator, FPT Corporation, or the
  ChatVRM/FPT project. If they ask who operates the retail store/chain, use the verified
  WinCommerce profile. An explicit ChatVRM/FPT project mention resolves to project facts;
  FPT alone resolves to the company profile.
- Store-specific answers require a `store_id`. Near-store place results require that
  store's verified coordinates; without them, ask which branch the visitor means.
- A generic nearby-place request gets only a scope/clarification response. It does not
  invoke a general search, geocoder, or local-trivia knowledge source.

## Runtime coverage checked on 2026-10-18

| Requested capability | Current state | What the agent can safely say |
|---|---|---|
| Product-level promotion and discount lookup | `list_product_promotions` reads snapshot text with its capture date. `list_active_store_promotions` uses structured campaigns and a fresh, complete branch feed when configured. | Structured campaign results are current only after checking dates, branch scope, and feed status. Snapshot-only text is reported as historical and cannot prove current validity. |
| Newly listed products | `list_new_catalog_products` compares two imports explicitly marked complete; import completeness requires accepted plus quarantined rows to equal the source row count. Demo data has no import history. | Report only catalog additions when two complete versions are comparable; do not claim a product launch date. |
| Products newly received into stock | `list_recent_stock_receipts` reads receiving events for the kiosk-mapped branch; the schema stores event, item, quantity, time, step, and source document. | Only event records from a fresh receiving feed are reported. The agent abstains if the branch mapping/feed is missing; an empty result is called “none” only when the feed certifies a complete seven-day window. |
| Store address, hours, and contact | `get_store_information` reads the verified branch profile mapped from the session kiosk. The current repository has no branch seed data. | Return only current, source-stamped store master fields; otherwise say the kiosk is not mapped to a verified branch. |
| Company introduction | `get_organization_profile` resolves distinct WinCommerce, FPT, and ChatVRM project entities. Each claim points to its own source IDs in `data/verified_organization_facts.json`. | Public claims include source links and verification dates. Project claims are marked as team-provided. “Đồ án FPT/ChatVRM” resolves to the project; FPT alone resolves to FPT Corporation. |
| General Q&A about nearby places | Removed from this customer agent; only a safe clarification guard remains. | No general place search or geocoding is run. |
| Nearby places for a specific store | `list_nearby_places_for_store` reads only attributed, unexpired nearby-place snapshots for the kiosk-mapped branch. The current repository has no branch coordinates or POI snapshot. | Require verified branch coordinates and a fresh attributed snapshot. Do not call a public geocoder on every customer turn. |

The PostgreSQL endpoint was unavailable during this check. API behavior and repository guardrails
were tested with the demo repository and mocked PostgreSQL rows; no live branch, promotion,
receiving, or nearby-place feed was verified. The demo catalog intentionally has no import
history or branch data. Production needs a verified `retail_stores` row, a matching
`retail_kiosks` row, and source records/feed-health rows before those answers can return facts.

## Public company sources found for the demo

- WinCommerce's official recruitment site identifies WinCommerce as a Masan Group member
  operating WinMart and WinMart+/WiN: [About WinCommerce](https://tuyendung.winmart.vn/home).
- WinMart's official transaction policy identifies the website owner as WinCommerce and
  a member of Masan Group: [WinMart transaction policy](https://winmart.vn/info/transaction-policy).
- FPT's official pages provide its company description and contact information:
  [FPT About Us](https://fpt.com/en/about-us), [FPT Contact](https://fpt.com/en/contact).

These are separate entities. A retail assistant should answer WinMart/WinCommerce facts
for the catalog's chain by default and answer FPT or ChatVRM only when the question points
to those profiles. The project association and team responsibilities come only from the
user-provided context; they do not establish an FPT sponsorship, ownership, or partnership.
The source list is a starting point, not a substitute for the store operator's approved
branch data.

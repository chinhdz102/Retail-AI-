# Retail information data and retrieval design

**Scope:** promotions, catalog additions, receipts, store and company facts, and
store-specific nearby places. The standalone feature “Q&A about nearby places” is
excluded; near-store lookup is only a store-scoped fact lookup.

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

1. **Keep product master facts separate from lifecycle dates.** GS1's product lifecycle
   model distinguishes a product-information effective date from first order, first ship,
   consumer delivery, and end-availability dates. A changed/crawled product record must
   not be labeled a “new product” without a lifecycle or catalog-version comparison.
   See the [GS1 Global Data Model lifecycle attributes](https://www.gs1.org/standards/gs1-global-data-model-attribute-implementation-guide/18).
2. **Treat receiving as an event, not a stock inference.** GS1 EPCIS records the what,
   when, where, and why of business events; a receiving event identifies the received
   items, time, location, and receiving step. A difference between two stock snapshots
   alone does not prove a delivery occurred. See the [GS1 EPCIS standard](https://ref.gs1.org/standards/epcis/2.0.1/)
   and [EPCIS/CBV implementation guide](https://www.gs1.org/standards/epcis-and-cbv-implementation-guideline/current-standardd).
3. **Model campaigns as structured, time-bounded records.** The present text field can
   support a product-level recorded offer, but not start/end times, terms, store scope,
   eligibility, or current-active status. Until those fields arrive from a verified
   promotion feed, responses must say “recorded in the catalog snapshot” and abstain on
   whether a campaign is still active.
4. **Separate chain, branch, and approved knowledge.** A company/brand profile belongs
   to an organization record; hours, address, and coordinates belong to a physical store
   record. Longer approved explanations can be stored as source-linked knowledge. Each
   fact needs a source URL and review timestamp. Do not merge WinCommerce, FPT, and a
   particular retail branch into one profile.
5. **Require a branch anchor for nearby-place answers.** A store-scoped nearby lookup
   needs verified store coordinates and a sourced/cacheable place record. Public OSM
   Nominatim has strict usage limits and asks clients to identify themselves, attribute
   the data, and cache results; it should not be called on every turn as an unbounded
   fallback. See the [Nominatim usage policy](https://operations.osmfoundation.org/policies/nominatim/).

## Proposed data contract

| Data | Source of truth | Minimum fields for an agent answer |
|---|---|---|
| Product promotion record | Retail promotion/POS feed | Product or campaign ID, description, start/end, eligibility, store scope, source and captured time |
| Newly listed product | Two consecutive complete catalog imports | Product ID, current dataset version, previous dataset version, first-seen-in-catalog time |
| New stock receipt | Inventory/POS receiving events | Event ID, product, quantity, store, occurred-at time, receiving step, source document |
| Company facts | Approved company source | Organization ID, claim, source URL, verified-at date, language |
| Store facts | Store master system | Store ID, chain ID, name, address, hours, contact, coordinates, source and verified-at date |
| Nearby places | Curated/provider snapshot for a store | Store ID, place name/type, address, coordinates or distance, provider/source, captured-at date |

## Conversation behavior

- A named product promotion uses its product snapshot. A broad offer question lists only
  recorded product-level offers and states their data date and missing validity terms.
- “New product” means **newly present in the latest catalog compared with the previous
  complete import**. It does not mean newly launched in the market.
- “Newly stocked” means a receipt event was recorded. If receipt events are absent, ask a
  staff member or say the system cannot verify it; never infer a delivery from stock counts.
- Company questions are matched to an explicitly named entity. If the visitor only says
  “the company,” clarify between the retail chain and another named company such as FPT.
- Store-specific answers require a `store_id`. Near-store place results require that
  store's verified coordinates; without them, ask which branch the visitor means.

## Runtime coverage checked on 2026-10-18

| Requested capability | Current state | What the agent can safely say |
|---|---|---|
| Product-level promotion and discount lookup | `list_product_promotions` reads snapshot text with its capture date. `list_active_store_promotions` uses structured campaigns and a fresh, complete branch feed when configured. | Structured campaign results are current only after checking dates, branch scope, and feed status. Snapshot-only text is reported as historical and cannot prove current validity. |
| Newly listed products | `list_new_catalog_products` compares two imports explicitly marked complete; import completeness requires accepted plus quarantined rows to equal the source row count. Demo data has no import history. | Report only catalog additions when two complete versions are comparable; do not claim a product launch date. |
| Products newly received into stock | `list_recent_stock_receipts` reads receiving events for the kiosk-mapped branch; the schema stores event, item, quantity, time, step, and source document. | Only event records from a fresh receiving feed are reported. The agent abstains if the branch mapping/feed is missing; an empty result is called “none” only when the feed certifies a complete seven-day window. |
| Store address, hours, and contact | `get_store_information` reads the verified branch profile mapped from the session kiosk. The current repository has no branch seed data. | Return only current, source-stamped store master fields; otherwise say the kiosk is not mapped to a verified branch. |
| Company introduction | `get_organization_profile` resolves separate source-backed WinCommerce and FPT profiles from `data/verified_organization_facts.json`; the ChatVRM project facts are explicitly user-provided. | Include the official source link and verification date for public company facts. Ask which entity the user means when it is ambiguous; do not imply a formal FPT relationship with the project. |
| General Q&A about nearby places | Excluded from this customer agent | Do not add a general local-trivia tool. |
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
for the catalog's chain by default and answer FPT only when the customer explicitly asks
about FPT or the project. The source list is a starting point, not a substitute for the
store operator's approved branch data.

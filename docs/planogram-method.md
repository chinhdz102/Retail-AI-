# WinMart taxonomy-based planogram draft

## What this is

This is a reproducible **location proposal**, not verified store inventory or an
installation-ready planogram. The source file's existing `shelf` values are crawler
placeholders; the catalog importer records them as `shelf_source=SIMULATED`. The
generated locations therefore live in a separate, versioned file and never replace
those source values.

When the PostgreSQL catalog is on the same dataset version as the map, the customer
agent can look up a product's proposed locator. It reads the assignment by product ID
and only uses it when the catalog's `dataset_version` matches
`source_dataset_sha256`. The reply expands codes such as `A06-B01-L04-P02` into aisle,
bay, level, and slot, and labels the result as proposed because the physical shelf has
not been checked. A missing assignment or version mismatch stays unavailable.

The SVG overview is rendered deterministically from the catalog snapshot and its
assignment records, not from generated image content. Each aisle-end sign uses the
catalog's aisle code and `sub_department` label; each colored SKU block carries the
catalog product name, ID, and locator in its SVG hover title. The rendered file prints
the source dataset version so it can be compared with the database import version.

## Research basis

Retail shelf-space allocation research treats a planogram as a constrained placement
problem: product location and facings depend on the available fixtures, product
dimensions, demand, margins, and merchandising constraints. When those inputs are
missing, claiming a sales- or profit-optimal placement would be unsupported.

The baseline uses **hierarchical product families**: department → sub-department →
category → brand → product name. This follows work on shelf-space allocation with
multi-level product families, which models product sequencing and family grouping as
explicit merchandising constraints. For shelf levels, the layout fills the central
levels first and rotates evenly through levels rather than assigning extra space to
products presumed to be popular. Research has measured vertical and horizontal
position effects, but the dataset has no store-specific demand or margin values with
which to rank products. The exact five-level/eight-slot bay dimensions below are
planning assumptions used to create stable coordinates, not measured WinMart fixtures.

Sources:

- Bianchi-Aguiar, T. et al. (2018), “Allocating products on shelves under merchandising rules: Multi-level product families with display directions,” *Omega*, 76, 47–62. [DOI](https://doi.org/10.1016/j.omega.2017.04.002)
- Hübner, A., Düsterhöft, T. & Ostermeier, M. (2021), “Shelf space dimensioning and product allocation in retail stores,” *European Journal of Operational Research*, 292(1), 155–171. [DOI](https://doi.org/10.1016/j.ejor.2020.10.030)
- Hansen, J. M., Raut, S. & Swami, S. (2010), “Retail Shelf Allocation: A Comparative Analysis of Heuristic and Meta-Heuristic Approaches,” *Journal of Retailing*, 86(1), 94–105. [DOI](https://doi.org/10.1016/j.jretai.2010.01.004)
- Pizzi, G. & Scarpi, D. (2016), “The effect of shelf layout on satisfaction and perceived assortment size: An empirical assessment,” *Journal of Retailing and Consumer Services*, 28, 67–77. [DOI](https://doi.org/10.1016/j.jretconser.2015.08.012)

## Assignment rule

1. Use only rows accepted by the existing catalog importer. Rows quarantined because
   their sale price exceeds their regular price are listed with no location.
2. Keep each department and sub-department in its own aisle group.
3. Keep each category block together where possible. Within a category, sort by brand,
   then normalized product name, then product ID.
4. Use eight bays per illustrative aisle. Each bay has five levels and eight locator
   slots per level. Fill the central levels first (`L3`, `L2`, `L4`, then `L1`, `L5`),
   rotating products through levels to avoid using stock as a popularity proxy.
5. Give each accepted SKU one unique location code, `Axx-Byy-Lzz-Pww`. This is a
   **locator only**; it does not specify the number of facings or units to stock.

The category hierarchy supports same-family grouping. There are no transaction
baskets, so the output does not infer complementary-item adjacency or cross-sell
patterns. Stock count is not treated as sales velocity.

## Coverage and limits

The current source contains 3,099 catalog rows, 3,081 rows accepted by the importer,
18 quarantined rows, 74 categories, and 5 sub-departments. Thirty accepted products
have no brand; they are still assigned using product name and product ID.

The source does not provide verified fixture dimensions, SKU package dimensions or
weight, cold-chain requirements, shelf capacity, store traffic, sales velocity,
transaction affinity, margin, or replenishment cadence. Before installing this layout,
a store operator must validate shelf/aisle dimensions, chilled/frozen and fresh-food
handling, heavy/bulky item placement, and safety/accessibility. The generated
3D-style SVG is an accurate locator schematic for the proposed coordinates, but its
fixture geometry is illustrative.

## Rebuild

```powershell
uv run python scripts/generate_planogram.py
```

Generated files:

- `data/winmart_planogram_assignments.json` — metadata, aisle summaries, and assignments
- `data/winmart_planogram_assignments.csv` — spreadsheet-friendly assignment list
- `data/winmart_planogram_3d.svg` — data-derived isometric shelf schematic
- `data/winmart_planogram_3d_concept.png` — original generated presentation concept
- `data/winmart_planogram_3d_labeled.png` — labeled concept with aisle groups and the location-code legend; it does not encode exact SKU positions

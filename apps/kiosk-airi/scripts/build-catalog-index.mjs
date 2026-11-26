import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const appDir = path.resolve(__dirname, '..')
const repoRoot = path.resolve(appDir, '..', '..')
const sourcePath = path.join(repoRoot, 'data', 'thanh_lam_products.json')
const syncedSalesPath = path.join(repoRoot, 'data', 'synced_sales_30d.json')
const outPath = path.join(appDir, 'public', 'catalog-index.json')

function safeImageUrl(raw) {
  if (typeof raw !== 'string') return null
  const cleaned = raw.trim()
  if (!cleaned || !/^https?:\/\//i.test(cleaned)) return null
  if (/[\s\x00-\x1f]/.test(cleaned)) return null
  try {
    const u = new URL(cleaned)
    return u.toString()
  } catch {
    return null
  }
}

function toInt(val) {
  const n = Number(val)
  return Number.isFinite(n) ? Math.round(n) : 0
}

function departmentOf(item, category) {
  const hier = typeof item.category_hierarchy === 'string' ? item.category_hierarchy : ''
  const parts = hier.split('>').map((s) => s.trim()).filter(Boolean)
  if (parts.length >= 3) return parts[2]
  if (parts.length === 2) return parts[1]
  if (parts.length === 1) return parts[0]
  return category || 'Khác'
}

function main() {
  if (!fs.existsSync(sourcePath)) {
    console.log('Catalog snapshot not found, skipping static catalog-index.json generation.')
    return
  }
  const raw = JSON.parse(fs.readFileSync(sourcePath, 'utf-8'))
  const records = Array.isArray(raw) ? raw : (Array.isArray(raw?.products) ? raw.products : [])
  let syncedSalesCounts = {}
  let salesDataSource = null
  if (fs.existsSync(syncedSalesPath)) {
    const rawSales = JSON.parse(fs.readFileSync(syncedSalesPath, 'utf-8'))
    if (rawSales && typeof rawSales.counts === 'object' && rawSales.counts) {
      syncedSalesCounts = rawSales.counts
      salesDataSource = 'CATALOG_MONTHLY'
    }
  }

  const seen = new Set()
  const deptTotals = new Map()
  const deptSubCounts = new Map()
  const entries = []
  let latestTimestamp = '2026-09-15T00:00:00+00:00'

  for (const item of records) {
    if (!item || typeof item !== 'object') continue
    const productId = String(item.product_id || '').trim()
    const name = String(item.name || '').replace(/\s+/g, ' ').trim()
    if (!productId || !name || seen.has(productId)) continue

    const basePrice = toInt(item.price_vnd)
    const salePrice = toInt(item.sale_price_vnd)
    const priceVnd = salePrice > 0 && (basePrice <= 0 || salePrice <= basePrice) ? salePrice : basePrice
    if (priceVnd <= 0) continue

    const listPriceVnd = basePrice > priceVnd ? basePrice : null
    seen.add(productId)

    const category = String(item.category_name || item.category || 'Khác').trim() || 'Khác'
    const dept = departmentOf(item, category)
    const brand = typeof item.brand === 'string' && item.brand.trim() ? item.brand.trim() : null
    const promotion = typeof item.discount_promotion === 'string' && item.discount_promotion.trim()
      ? item.discount_promotion.trim()
      : null
    const shelf = typeof item.shelf === 'string' && item.shelf.trim() ? item.shelf.trim() : null
    const imageUrl = safeImageUrl(item.image_url)
    const stock = Math.max(0, toInt(item.stock))
    const uom = typeof item.uom === 'string' && item.uom.trim() ? item.uom.trim() : null
    if (typeof item.scraped_at === 'string' && item.scraped_at > latestTimestamp) {
      latestTimestamp = item.scraped_at
    }

    deptTotals.set(dept, (deptTotals.get(dept) || 0) + 1)
    let subMap = deptSubCounts.get(dept)
    if (!subMap) {
      subMap = new Map()
      deptSubCounts.set(dept, subMap)
    }
    subMap.set(category, (subMap.get(category) || 0) + 1)

    const discountRatio = listPriceVnd && listPriceVnd > priceVnd
      ? (listPriceVnd - priceVnd) / listPriceVnd
      : (promotion ? 0.01 : 0)

    const rawSold = syncedSalesCounts[productId]
    const sold30d = typeof rawSold === 'number' && Number.isFinite(rawSold) ? Math.max(0, Math.round(rawSold)) : null

    entries.push({
      productId,
      name,
      dept,
      category,
      brand,
      priceVnd,
      listPriceVnd,
      promotion,
      shelf,
      imageUrl,
      stock,
      uom,
      sold30d,
      hasImgRank: imageUrl ? 0 : 1,
      hasPromoRank: discountRatio > 0 ? 0 : 1,
    })
  }

  const departments = Array.from(deptTotals.entries())
    .sort((a, b) => (b[1] - a[1]) || a[0].localeCompare(b[0], 'vi'))
    .map(([name, count]) => ({
      name,
      count,
      subcategories: Array.from(deptSubCounts.get(name).entries())
        .sort((a, b) => (b[1] - a[1]) || a[0].localeCompare(b[0], 'vi'))
        .map(([subName, subCount]) => ({ name: subName, count: subCount })),
    }))

  const deptIdx = new Map(departments.map((d, i) => [d.name, i]))

  entries.sort((a, b) =>
    (a.hasImgRank - b.hasImgRank)
    || ((b.sold30d ?? 0) - (a.sold30d ?? 0))
    || (a.hasPromoRank - b.hasPromoRank)
    || a.name.localeCompare(b.name, 'vi')
    || a.productId.localeCompare(b.productId),
  )

  const rows = entries.map((e) => [
    e.productId,
    e.name,
    deptIdx.get(e.dept) ?? 0,
    e.category,
    e.brand,
    e.priceVnd,
    e.listPriceVnd,
    e.promotion,
    e.shelf,
    e.imageUrl,
    e.stock,
    e.uom,
    e.sold30d,
  ])

  const payload = {
    total: rows.length,
    data_source: 'thanh_lam_json_snapshot',
    stock_is_current: false,
    data_timestamp: latestTimestamp,
    sales_data_source: salesDataSource,
    departments,
    rows,
  }

  fs.mkdirSync(path.dirname(outPath), { recursive: true })
  const serialized = JSON.stringify(payload)
  fs.writeFileSync(outPath, serialized, 'utf-8')
  console.log(
    `Static catalog index prepared: ${rows.length} products, ${departments.length} departments (${Math.round(Buffer.byteLength(serialized) / 1024)} KiB raw)`,
  )
}

main()

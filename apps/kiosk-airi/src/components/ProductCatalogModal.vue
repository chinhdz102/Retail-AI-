<script setup lang="ts">
import { computed, nextTick, onUnmounted, ref, shallowRef, watch } from 'vue'
import {
  PhArrowDown as ArrowDown,
  PhCaretRight as CaretRight,
  PhFire as Fire,
  PhImage as ImageIcon,
  PhMagnifyingGlass as MagnifyingGlass,
  PhMinus as Minus,
  PhPlus as Plus,
  PhShoppingBagOpen as ShoppingBagOpen,
  PhSparkle as Sparkle,
  PhSquaresFour as SquaresFour,
  PhTag as Tag,
  PhWarningCircle as WarningCircle,
  PhX as CloseIcon,
} from '@phosphor-icons/vue'

import { browseProductCatalog, fetchProductCatalogIndex } from '../api'
import { formatShelfLocation } from '../utils/shelfLocation'
import type {
  AssistedCartItem,
  CatalogDepartmentSummary,
  CatalogSortMode,
  LanguageCode,
  ProductCard,
  ProductCatalogIndexResponse,
} from '../types'

interface IndexedCatalogItem {
  product: ProductCard
  deptName: string
  categoryName: string
  searchText: string
  discountRatio: number
  sold: number
  cleanTitle: string
}

interface CompiledClientCatalog {
  totalCatalog: number
  departments: CatalogDepartmentSummary[]
  all: IndexedCatalogItem[]
  byDept: Map<string, IndexedCatalogItem[]>
  byDeptCat: Map<string, IndexedCatalogItem[]>
}

const props = defineProps<{
  open: boolean
  assistedItems: AssistedCartItem[]
  assistedTotalCount: number
  assistedTotalVnd: number
  language: LanguageCode
}>()

const emit = defineEmits<{
  close: []
  'add-item': [product: ProductCard]
  'update-qty': [productId: string, quantity: number]
  'open-cart': []
  'ask-mai': [prompt: string]
}>()

const BATCH_SIZE = 16
const departments = shallowRef<CatalogDepartmentSummary[]>([])
const localCatalog = shallowRef<CompiledClientCatalog | null>(null)
const remoteItems = shallowRef<ProductCard[]>([])
const remoteTotal = ref(0)
const remoteTotalCatalog = ref(0)
const remoteHasMore = ref(false)
const visibleLimit = ref(BATCH_SIZE)

const activeDepartment = ref<string | null>(null)
const activeCategory = ref<string | null>(null)
const searchQuery = ref('')
const sortMode = ref<CatalogSortMode>('popular')
const isLoading = ref(false)
const isLoadingMore = ref(false)
const loadError = ref<string | null>(null)
const brokenImages = ref<Record<string, boolean>>({})
const detailProduct = ref<ProductCard | null>(null)
const gridScrollRef = ref<HTMLElement | null>(null)
const loadMoreSentinel = ref<HTMLElement | null>(null)

let searchDebounceTimer: ReturnType<typeof setTimeout> | undefined
let activeRequestSeq = 0
let activeAbortController: AbortController | null = null
let sentinelObserver: IntersectionObserver | null = null

const sortOptions: Array<{ value: CatalogSortMode; label: string }> = [
  { value: 'popular', label: 'Bán chạy' },
  { value: 'promo', label: 'Khuyến mãi' },
  { value: 'price_asc', label: 'Giá thấp → cao' },
  { value: 'price_desc', label: 'Giá cao → thấp' },
]

function foldSearchText(value: string): string {
  return value
    .replace(/đ/g, 'd')
    .replace(/Đ/g, 'd')
    .normalize('NFD')
    .replace(/[\u0300-\u036f]/g, '')
    .toLowerCase()
    .replace(/\s+/g, ' ')
    .trim()
}

function compileClientCatalog(raw: ProductCatalogIndexResponse): CompiledClientCatalog {
  const all: IndexedCatalogItem[] = new Array(raw.rows.length)
  const byDept = new Map<string, IndexedCatalogItem[]>()
  const byDeptCat = new Map<string, IndexedCatalogItem[]>()

  for (let i = 0; i < raw.rows.length; i += 1) {
    const row = raw.rows[i]
    const deptName = raw.departments[row[2]]?.name ?? row[3] ?? 'Khác'
    const price = row[5]
    const listPrice = row[6]
    const promo = row[7]
    const sold = row[12] ?? 0
    const product: ProductCard = {
      product_id: row[0],
      name: row[1],
      category: row[3],
      brand: row[4],
      price_vnd: price,
      list_price_vnd: listPrice,
      promotion: promo,
      shelf: row[8],
      image_url: row[9],
      stock: row[10],
      stock_is_current: raw.stock_is_current,
      uom: row[11],
      data_timestamp: raw.data_timestamp,
      data_source: raw.data_source,
      demo_units_sold_30d: row[12],
      sales_data_source: row[12] !== null ? (raw.sales_data_source ?? 'CATALOG_MONTHLY') : null,
    }
    const discountRatio =
      listPrice && listPrice > price
        ? (listPrice - price) / listPrice
        : promo
          ? 0.01
          : 0
    const entry: IndexedCatalogItem = {
      product,
      deptName,
      categoryName: row[3],
      searchText: foldSearchText(`${row[1]} ${row[4] ?? ''} ${row[3]} ${deptName}`),
      discountRatio,
      sold,
      cleanTitle: row[1].replace(/\s+/g, ' ').trim(),
    }
    all[i] = entry

    let deptBucket = byDept.get(deptName)
    if (!deptBucket) {
      deptBucket = []
      byDept.set(deptName, deptBucket)
    }
    deptBucket.push(entry)

    const dcKey = `${deptName}\u0000${row[3]}`
    let catBucket = byDeptCat.get(dcKey)
    if (!catBucket) {
      catBucket = []
      byDeptCat.set(dcKey, catBucket)
    }
    catBucket.push(entry)
  }

  return {
    totalCatalog: raw.total,
    departments: raw.departments,
    all,
    byDept,
    byDeptCat,
  }
}

const qtyByProductId = computed(() => {
  const map: Record<string, number> = {}
  for (const entry of props.assistedItems) {
    map[entry.product.product_id] = entry.quantity
  }
  return map
})

const currentDepartmentObj = computed(() => {
  if (!activeDepartment.value) return null
  return departments.value.find((dept) => dept.name === activeDepartment.value) ?? null
})

const visibleSubcategories = computed(() => {
  const dept = currentDepartmentObj.value
  if (!dept || dept.subcategories.length <= 1) return []
  return dept.subcategories
})

const localFilteredList = computed<IndexedCatalogItem[]>(() => {
  const catalog = localCatalog.value
  if (!catalog) return []

  let base: IndexedCatalogItem[]
  if (activeDepartment.value && activeCategory.value) {
    base = catalog.byDeptCat.get(`${activeDepartment.value}\u0000${activeCategory.value}`) ?? []
  } else if (activeDepartment.value) {
    base = catalog.byDept.get(activeDepartment.value) ?? []
  } else {
    base = catalog.all
  }

  const q = foldSearchText(searchQuery.value)
  let filtered = base
  if (q) {
    const tokens = q.split(' ')
    filtered = base.filter((entry) => {
      for (let i = 0; i < tokens.length; i += 1) {
        if (!entry.searchText.includes(tokens[i])) return false
      }
      return true
    })
  }

  if (sortMode.value === 'popular') {
    return filtered
  }
  const sorted = filtered.slice()
  if (sortMode.value === 'price_asc') {
    sorted.sort((a, b) => a.product.price_vnd - b.product.price_vnd)
  } else if (sortMode.value === 'price_desc') {
    sorted.sort((a, b) => b.product.price_vnd - a.product.price_vnd)
  } else if (sortMode.value === 'promo') {
    sorted.sort((a, b) => (b.discountRatio - a.discountRatio) || (b.sold - a.sold))
  } else {
    sorted.sort((a, b) => a.cleanTitle.localeCompare(b.cleanTitle, 'vi'))
  }
  return sorted
})

const totalCatalog = computed(() =>
  localCatalog.value ? localCatalog.value.totalCatalog : remoteTotalCatalog.value,
)

const total = computed(() =>
  localCatalog.value ? localFilteredList.value.length : remoteTotal.value,
)

const visibleItems = computed<ProductCard[]>(() => {
  if (localCatalog.value) {
    return localFilteredList.value.slice(0, visibleLimit.value).map((entry) => entry.product)
  }
  return remoteItems.value
})

const hasMore = computed(() =>
  localCatalog.value
    ? visibleLimit.value < localFilteredList.value.length
    : remoteHasMore.value,
)

function qtyOf(productId: string): number {
  return qtyByProductId.value[productId] ?? 0
}

function formatVnd(amount: number) {
  return new Intl.NumberFormat(props.language === 'en' ? 'en-US' : 'vi-VN', {
    maximumFractionDigits: 0,
  }).format(amount) + (props.language === 'en' ? ' VND' : 'đ')
}

function cleanName(name: string) {
  return name.replace(/\s+/g, ' ').trim()
}

function hasImage(product: ProductCard) {
  return Boolean(product.image_url && !brokenImages.value[product.product_id])
}

function onImgError(productId: string) {
  brokenImages.value = { ...brokenImages.value, [productId]: true }
}

function discountPercent(product: ProductCard): number | null {
  if (!product.list_price_vnd || product.list_price_vnd <= product.price_vnd) return null
  const pct = Math.round(((product.list_price_vnd - product.price_vnd) / product.list_price_vnd) * 100)
  return pct >= 2 ? pct : null
}

function resetGridScroll() {
  visibleLimit.value = BATCH_SIZE
  if (gridScrollRef.value) {
    gridScrollRef.value.scrollTop = 0
  }
}

async function ensureCatalogLoaded() {
  if (localCatalog.value) return
  loadError.value = null
  isLoading.value = true
  try {
    if (typeof fetchProductCatalogIndex === 'function') {
      const rawIndex = await fetchProductCatalogIndex()
      if (rawIndex && Array.isArray(rawIndex.rows) && rawIndex.rows.length > 0) {
        const compiled = compileClientCatalog(rawIndex)
        localCatalog.value = compiled
        departments.value = compiled.departments
        isLoading.value = false
        return
      }
    }
  } catch {
    // Fall back transparently to paginated endpoint below.
  }
  await fetchRemoteCatalog(false)
}

async function fetchRemoteCatalog(append = false) {
  const requestSeq = ++activeRequestSeq
  activeAbortController?.abort()
  const controller = new AbortController()
  activeAbortController = controller

  if (append) {
    isLoadingMore.value = true
  } else {
    isLoading.value = true
    loadError.value = null
  }

  try {
    const res = await browseProductCatalog({
      department: activeDepartment.value ?? undefined,
      category: activeCategory.value ?? undefined,
      q: searchQuery.value.trim() || undefined,
      sort: sortMode.value,
      offset: append ? remoteItems.value.length : 0,
      limit: BATCH_SIZE,
      signal: controller.signal,
    })
    if (requestSeq !== activeRequestSeq) return
    if (res.departments.length) {
      departments.value = res.departments
    }
    remoteTotal.value = res.total
    remoteTotalCatalog.value = res.total_catalog
    remoteHasMore.value = res.has_more
    remoteItems.value = append ? [...remoteItems.value, ...res.items] : res.items
    if (!append && gridScrollRef.value) {
      gridScrollRef.value.scrollTop = 0
    }
  } catch (error) {
    if (requestSeq !== activeRequestSeq) return
    if (error instanceof DOMException && error.name === 'AbortError') return
    loadError.value = error instanceof Error && error.message
      ? error.message
      : 'Chưa tải được danh mục sản phẩm. Bạn thử tải lại nhé.'
  } finally {
    if (requestSeq === activeRequestSeq) {
      isLoading.value = false
      isLoadingMore.value = false
    }
  }
}

function handleLoadMore() {
  if (!hasMore.value) return
  if (localCatalog.value) {
    visibleLimit.value += BATCH_SIZE
    return
  }
  if (!isLoadingMore.value) {
    void fetchRemoteCatalog(true)
  }
}

function selectDepartment(deptName: string | null) {
  if (activeDepartment.value === deptName && activeCategory.value === null) return
  activeDepartment.value = deptName
  activeCategory.value = null
  resetGridScroll()
  if (!localCatalog.value) {
    void fetchRemoteCatalog(false)
  }
}

function selectSubcategory(subName: string | null) {
  if (activeCategory.value === subName) return
  activeCategory.value = subName
  resetGridScroll()
  if (!localCatalog.value) {
    void fetchRemoteCatalog(false)
  }
}

function selectSort(mode: CatalogSortMode) {
  if (sortMode.value === mode) return
  sortMode.value = mode
  resetGridScroll()
  if (!localCatalog.value) {
    void fetchRemoteCatalog(false)
  }
}

function onSearchInput(event: Event) {
  searchQuery.value = (event.target as HTMLInputElement).value
  if (localCatalog.value) {
    resetGridScroll()
    return
  }
  if (searchDebounceTimer) clearTimeout(searchDebounceTimer)
  searchDebounceTimer = setTimeout(() => {
    void fetchRemoteCatalog(false)
  }, 220)
}

function clearSearch() {
  searchQuery.value = ''
  resetGridScroll()
  if (searchDebounceTimer) clearTimeout(searchDebounceTimer)
  if (!localCatalog.value) {
    void fetchRemoteCatalog(false)
  }
}

function askMaiAboutProduct(product: ProductCard) {
  const name = cleanName(product.name)
  detailProduct.value = null
  emit('ask-mai', `Cho mình biết thông tin, thành phần và công dụng của ${name}?`)
}

function teardownSentinelObserver() {
  sentinelObserver?.disconnect()
  sentinelObserver = null
}

function setupSentinelObserver() {
  teardownSentinelObserver()
  if (
    !props.open
    || typeof IntersectionObserver === 'undefined'
    || !loadMoreSentinel.value
    || !gridScrollRef.value
  ) {
    return
  }
  sentinelObserver = new IntersectionObserver(
    (entries) => {
      if (entries.some((entry) => entry.isIntersecting)) {
        handleLoadMore()
      }
    },
    { root: gridScrollRef.value, rootMargin: '280px 0px' },
  )
  sentinelObserver.observe(loadMoreSentinel.value)
}

function onKeydown(event: KeyboardEvent) {
  if (event.key === 'Escape' && props.open) {
    event.preventDefault()
    if (detailProduct.value) {
      detailProduct.value = null
    } else {
      emit('close')
    }
  }
}

watch(() => props.open, async (isOpen) => {
  if (isOpen) {
    if (typeof window !== 'undefined') window.addEventListener('keydown', onKeydown)
    if (!localCatalog.value && (!remoteItems.value.length || loadError.value)) {
      await ensureCatalogLoaded()
    }
    await nextTick()
    setupSentinelObserver()
  } else {
    detailProduct.value = null
    teardownSentinelObserver()
    if (typeof window !== 'undefined') window.removeEventListener('keydown', onKeydown)
  }
})

watch([loadMoreSentinel, hasMore], async () => {
  await nextTick()
  if (props.open && hasMore.value) {
    setupSentinelObserver()
  } else {
    teardownSentinelObserver()
  }
})

onUnmounted(() => {
  if (searchDebounceTimer) clearTimeout(searchDebounceTimer)
  activeAbortController?.abort()
  teardownSentinelObserver()
  if (typeof window !== 'undefined') window.removeEventListener('keydown', onKeydown)
})
</script>

<template>
  <Teleport to="body">
    <Transition name="catalog-modal">
      <div
        v-if="open"
        class="catalog-overlay"
        role="dialog"
        aria-modal="true"
        aria-label="Danh mục tất cả sản phẩm"
        @click.self="emit('close')"
      >
        <section class="catalog-modal">
          <div class="catalog-sheet-handle" aria-hidden="true"><span></span></div>

          <header class="catalog-header">
            <div class="catalog-title-group">
              <div class="catalog-title-icon">
                <SquaresFour :size="20" weight="duotone" aria-hidden="true" />
              </div>
              <div class="catalog-title-copy">
                <h2>Tất cả sản phẩm</h2>
                <p>
                  <template v-if="totalCatalog > 0">
                    {{ totalCatalog.toLocaleString('vi-VN') }} sản phẩm · {{ departments.length }} danh mục
                  </template>
                  <template v-else>
                    Duyệt sản phẩm theo từng danh mục và thêm nhanh vào giỏ
                  </template>
                </p>
              </div>
            </div>

            <div class="catalog-header-actions">
              <button
                type="button"
                class="catalog-header-cart"
                :class="{ 'has-items': assistedTotalCount > 0 }"
                :aria-label="assistedTotalCount > 0 ? `Mở giỏ hàng (${assistedTotalCount} sản phẩm)` : 'Mở giỏ hàng'"
                @click="emit('open-cart')"
              >
                <ShoppingBagOpen :size="17" weight="duotone" aria-hidden="true" />
                <span>Giỏ hàng</span>
                <span v-if="assistedTotalCount > 0" class="catalog-header-cart-badge">{{ assistedTotalCount }}</span>
              </button>
              <button
                type="button"
                class="catalog-close-btn"
                aria-label="Đóng danh mục sản phẩm"
                @click="emit('close')"
              >
                <CloseIcon :size="18" weight="bold" aria-hidden="true" />
              </button>
            </div>
          </header>

          <div class="catalog-toolbar">
            <div class="catalog-search-box">
              <MagnifyingGlass :size="17" weight="duotone" class="catalog-search-icon" aria-hidden="true" />
              <input
                :value="searchQuery"
                type="search"
                class="catalog-search-input"
                placeholder="Tìm tên sản phẩm, thương hiệu (VD: Coca, sữa tươi, mì Hảo Hảo)..."
                aria-label="Tìm kiếm trong tất cả sản phẩm"
                @input="onSearchInput"
              />
              <button
                v-if="searchQuery"
                type="button"
                class="catalog-search-clear"
                aria-label="Xóa từ khóa tìm kiếm"
                @click="clearSearch"
              >
                <CloseIcon :size="14" weight="bold" aria-hidden="true" />
              </button>
            </div>

            <div class="catalog-sorts" role="group" aria-label="Sắp xếp sản phẩm">
              <button
                v-for="opt in sortOptions"
                :key="opt.value"
                type="button"
                class="catalog-sort-chip"
                :class="{ 'is-active': sortMode === opt.value }"
                @click="selectSort(opt.value)"
              >
                <Fire v-if="opt.value === 'popular'" :size="13" weight="duotone" aria-hidden="true" />
                <Tag v-else-if="opt.value === 'promo'" :size="13" weight="duotone" aria-hidden="true" />
                <span>{{ opt.label }}</span>
              </button>
            </div>
          </div>

          <div class="catalog-body">
            <!-- Categories: Desktop Left Sidebar / Mobile Horizontal Strip -->
            <aside class="catalog-sidebar" aria-label="Danh mục sản phẩm">
              <button
                type="button"
                class="catalog-dept-btn"
                :class="{ 'is-active': activeDepartment === null }"
                @click="selectDepartment(null)"
              >
                <span class="catalog-dept-name">Tất cả</span>
                <span v-if="totalCatalog > 0" class="catalog-dept-count">{{ totalCatalog }}</span>
              </button>
              <button
                v-for="dept in departments"
                :key="dept.name"
                type="button"
                class="catalog-dept-btn"
                :class="{ 'is-active': activeDepartment === dept.name }"
                @click="selectDepartment(dept.name)"
              >
                <span class="catalog-dept-name">{{ dept.name }}</span>
                <span class="catalog-dept-count">{{ dept.count }}</span>
              </button>
            </aside>

            <!-- Product Grid Column -->
            <div ref="gridScrollRef" class="catalog-main">
              <!-- Subcategories strip when a department is chosen -->
              <div v-if="visibleSubcategories.length > 0" class="catalog-subcats" role="group" aria-label="Danh mục con">
                <button
                  type="button"
                  class="catalog-subcat-chip"
                  :class="{ 'is-active': activeCategory === null }"
                  @click="selectSubcategory(null)"
                >
                  Tất cả {{ activeDepartment }}
                </button>
                <button
                  v-for="sub in visibleSubcategories"
                  :key="sub.name"
                  type="button"
                  class="catalog-subcat-chip"
                  :class="{ 'is-active': activeCategory === sub.name }"
                  @click="selectSubcategory(sub.name)"
                >
                  <span>{{ sub.name }}</span>
                  <small>{{ sub.count }}</small>
                </button>
              </div>

              <div class="catalog-results-meta">
                <span>
                  <template v-if="activeCategory">{{ activeCategory }}</template>
                  <template v-else-if="activeDepartment">{{ activeDepartment }}</template>
                  <template v-else>Tất cả danh mục</template>
                  <template v-if="!isLoading"> · <strong>{{ total.toLocaleString('vi-VN') }}</strong> sản phẩm</template>
                </span>
                <span v-if="searchQuery.trim()" class="catalog-search-tag">
                  Từ khóa: “{{ searchQuery.trim() }}”
                </span>
              </div>

              <div v-if="loadError" class="catalog-state-box is-error" role="alert">
                <WarningCircle :size="24" weight="fill" aria-hidden="true" />
                <p>{{ loadError }}</p>
                <button type="button" class="catalog-retry-btn" @click="ensureCatalogLoaded()">
                  Thử tải lại
                </button>
              </div>

              <div v-else-if="isLoading" class="catalog-grid" aria-busy="true" aria-label="Đang tải sản phẩm">
                <div v-for="n in 8" :key="n" class="catalog-card is-skeleton">
                  <div class="catalog-skel-media"></div>
                  <div class="catalog-skel-line is-short"></div>
                  <div class="catalog-skel-line"></div>
                  <div class="catalog-skel-line is-price"></div>
                </div>
              </div>

              <div v-else-if="visibleItems.length === 0" class="catalog-state-box">
                <SquaresFour :size="32" weight="duotone" aria-hidden="true" />
                <strong>Không tìm thấy sản phẩm phù hợp</strong>
                <p>Thử xóa từ khóa hoặc chuyển sang danh mục khác nhé.</p>
                <button
                  v-if="searchQuery || activeDepartment"
                  type="button"
                  class="catalog-retry-btn"
                  @click="activeDepartment = null; activeCategory = null; clearSearch()"
                >
                  Xem lại tất cả sản phẩm
                </button>
              </div>

              <template v-else>
                <div class="catalog-grid">
                  <article
                    v-for="(product, idx) in visibleItems"
                    :key="product.product_id"
                    class="catalog-card"
                  >
                    <button
                      type="button"
                      class="catalog-card-media"
                      :aria-label="`Xem chi tiết ${cleanName(product.name)}`"
                      @click="detailProduct = product"
                    >
                      <span v-if="discountPercent(product)" class="catalog-discount-badge">
                        -{{ discountPercent(product) }}%
                      </span>
                      <img
                        v-if="hasImage(product)"
                        :src="product.image_url!"
                        :alt="cleanName(product.name)"
                        width="140"
                        height="140"
                        :loading="idx < 4 ? 'eager' : 'lazy'"
                        decoding="async"
                        @error="onImgError(product.product_id)"
                      />
                      <div v-else class="catalog-card-placeholder">
                        <ImageIcon :size="26" weight="duotone" aria-hidden="true" />
                      </div>
                    </button>

                    <div class="catalog-card-body">
                      <div class="catalog-card-tags">
                        <span v-if="product.brand" class="catalog-card-brand">{{ product.brand }}</span>
                        <span class="catalog-card-cat">{{ product.category }}</span>
                      </div>
                      <button
                        type="button"
                        class="catalog-card-title"
                        @click="detailProduct = product"
                      >
                        {{ cleanName(product.name) }}
                      </button>
                      <small v-if="product.sales_data_source && product.demo_units_sold_30d" class="catalog-card-sales">
                        {{ product.sales_data_source === 'SIMULATED' ? 'Đã bán (demo)' : 'Đã bán' }}: {{ product.demo_units_sold_30d.toLocaleString('vi-VN') }}/30 ngày
                      </small>
                    </div>

                    <div class="catalog-card-footer">
                      <div class="catalog-card-pricing">
                        <strong class="catalog-card-price">{{ formatVnd(product.price_vnd) }}</strong>
                        <span
                          v-if="product.list_price_vnd && product.list_price_vnd > product.price_vnd"
                          class="catalog-card-list-price"
                        >
                          {{ formatVnd(product.list_price_vnd) }}
                        </span>
                      </div>

                      <div v-if="qtyOf(product.product_id) > 0" class="catalog-card-stepper">
                        <button
                          type="button"
                          class="card-stepper-btn"
                          :aria-label="`Giảm số lượng ${cleanName(product.name)}`"
                          @click="emit('update-qty', product.product_id, qtyOf(product.product_id) - 1)"
                        >
                          <Minus :size="13" weight="bold" aria-hidden="true" />
                        </button>
                        <span class="card-stepper-qty">{{ qtyOf(product.product_id) }}</span>
                        <button
                          type="button"
                          class="card-stepper-btn"
                          :disabled="qtyOf(product.product_id) >= 20"
                          :aria-label="`Tăng số lượng ${cleanName(product.name)}`"
                          @click="emit('update-qty', product.product_id, qtyOf(product.product_id) + 1)"
                        >
                          <Plus :size="13" weight="bold" aria-hidden="true" />
                        </button>
                      </div>
                      <button
                        v-else
                        type="button"
                        class="catalog-add-btn"
                        :aria-label="`Thêm ${cleanName(product.name)} vào giỏ hàng`"
                        @click="emit('add-item', product)"
                      >
                        <Plus :size="14" weight="bold" aria-hidden="true" />
                        <span>Giỏ</span>
                      </button>
                    </div>
                  </article>
                </div>

                <div v-if="hasMore" ref="loadMoreSentinel" class="catalog-load-more-wrap">
                  <span class="catalog-load-progress">
                    Đang hiển thị {{ visibleItems.length.toLocaleString('vi-VN') }} / {{ total.toLocaleString('vi-VN') }} sản phẩm
                  </span>
                  <button
                    type="button"
                    class="catalog-load-more-btn"
                    :disabled="isLoadingMore"
                    @click="handleLoadMore"
                  >
                    <ArrowDown :size="15" weight="bold" aria-hidden="true" />
                    <span>{{ isLoadingMore ? 'Đang tải thêm...' : 'Xem thêm sản phẩm' }}</span>
                  </button>
                </div>
              </template>
            </div>
          </div>

          <!-- Sticky Bottom Cart Summary Bar when cart has items -->
          <footer v-if="assistedTotalCount > 0" class="catalog-cart-bar">
            <button type="button" class="assisted-cart-bar-btn" @click="emit('open-cart')">
              <span class="assisted-cart-bar-left">
                <span class="assisted-cart-bar-icon">
                  <ShoppingBagOpen :size="18" weight="duotone" aria-hidden="true" />
                  <span class="assisted-cart-bar-count">{{ assistedTotalCount }}</span>
                </span>
                <span class="assisted-cart-bar-copy">
                  <strong>{{ assistedTotalCount }} sản phẩm trong giỏ</strong>
                  <small>Tổng {{ formatVnd(assistedTotalVnd) }}</small>
                </span>
              </span>
              <span class="assisted-cart-bar-cta">
                <span>Xem giỏ &amp; Thanh toán</span>
                <CaretRight :size="15" weight="bold" aria-hidden="true" />
              </span>
            </button>
          </footer>
        </section>

        <!-- Product Detail Lightbox inside Catalog -->
        <div
          v-if="detailProduct"
          class="product-modal"
          role="dialog"
          aria-modal="true"
          :aria-label="cleanName(detailProduct.name)"
          @click.self="detailProduct = null"
        >
          <div class="product-modal-card">
            <header class="product-modal-header">
              <div class="product-modal-badges">
                <span v-if="detailProduct.brand" class="product-modal-chip">{{ detailProduct.brand }}</span>
                <span v-if="detailProduct.category" class="product-modal-chip is-muted">{{ detailProduct.category }}</span>
              </div>
              <button
                type="button"
                class="product-modal-close"
                aria-label="Đóng chi tiết sản phẩm"
                @click="detailProduct = null"
              >
                <CloseIcon :size="18" weight="bold" aria-hidden="true" />
              </button>
            </header>

            <div class="product-modal-media">
              <img
                v-if="hasImage(detailProduct)"
                :src="detailProduct.image_url!"
                :alt="cleanName(detailProduct.name)"
                class="product-modal-image"
                @error="onImgError(detailProduct.product_id)"
              />
              <div v-else class="product-modal-placeholder">
                <ImageIcon :size="44" weight="duotone" aria-hidden="true" />
                <span>Sản phẩm chưa có ảnh trong catalog</span>
              </div>
            </div>

            <div class="product-modal-body">
              <h2 class="product-modal-title">{{ cleanName(detailProduct.name) }}</h2>
              <div class="product-modal-pricing">
                <strong class="product-modal-price">{{ formatVnd(detailProduct.price_vnd) }}</strong>
                <span
                  v-if="detailProduct.list_price_vnd && detailProduct.list_price_vnd > detailProduct.price_vnd"
                  class="product-modal-list-price"
                >
                  {{ formatVnd(detailProduct.list_price_vnd) }}
                </span>
                <span v-if="detailProduct.uom" class="product-modal-uom">/ {{ detailProduct.uom }}</span>
              </div>
              <p v-if="detailProduct.promotion" class="product-modal-promo">{{ detailProduct.promotion }}</p>
              <div class="product-modal-meta">
                <span v-if="detailProduct.shelf">Kệ gợi ý: {{ formatShelfLocation(detailProduct.shelf, { detailed: true }) }}</span>
                <span>Giá tham khảo từ catalog siêu thị</span>
              </div>
            </div>

            <footer class="product-modal-actions">
              <button
                type="button"
                class="product-modal-cart-btn"
                @click="emit('add-item', detailProduct)"
              >
                <ShoppingBagOpen :size="17" weight="duotone" aria-hidden="true" />
                <span>
                  {{
                    qtyOf(detailProduct.product_id) > 0
                      ? `Trong giỏ: ${qtyOf(detailProduct.product_id)} (+1)`
                      : 'Thêm vào giỏ'
                  }}
                </span>
              </button>
              <button
                type="button"
                class="product-modal-ask-btn"
                @click="askMaiAboutProduct(detailProduct)"
              >
                <Sparkle :size="17" weight="duotone" aria-hidden="true" />
                <span>Hỏi Mai</span>
              </button>
            </footer>
          </div>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

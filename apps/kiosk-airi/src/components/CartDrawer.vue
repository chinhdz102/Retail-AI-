<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from 'vue'
import {
  PhBellRinging as BellRinging,
  PhCheckCircle as CheckCircle,
  PhClock as Clock,
  PhImage as ImageIcon,
  PhMapPin as MapPin,
  PhMinus as Minus,
  PhNotePencil as NotePencil,
  PhPaperPlaneTilt as PaperPlaneTilt,
  PhPhone as Phone,
  PhPlus as Plus,
  PhReceipt as Receipt,
  PhShoppingBagOpen as ShoppingBagOpen,
  PhSparkle as Sparkle,
  PhTrash as Trash,
  PhUser as UserIcon,
  PhWarningCircle as WarningCircle,
  PhX as CloseIcon,
} from '@phosphor-icons/vue'

import type { AssistedCartItem, AssistedOrderResponse, LanguageCode } from '../types'

const props = defineProps<{
  open: boolean
  items: AssistedCartItem[]
  orders: AssistedOrderResponse[]
  activeOrder: AssistedOrderResponse | null
  initialTab?: 'cart' | 'orders'
  isSubmitting: boolean
  submitError: string | null
  language: LanguageCode
  managerNotifyChannel?: 'zalo' | 'messenger' | 'gmail' | null
}>()

const emit = defineEmits<{
  close: []
  'update-qty': [productId: string, quantity: number]
  'remove-item': [productId: string]
  'clear-cart': []
  'submit-order': [payload: {
    customerName: string
    customerPhone: string
    pickupLocation: string
    customerNote: string
  }]
  'dismiss-active-order': []
  'browse-suggestions': []
}>()

const activeTab = ref<'cart' | 'orders'>('cart')
const customerName = ref('')
const customerPhone = ref('')
const pickupLocation = ref('Đứng tại Kiosk')
const customerNote = ref('')
const brokenImages = ref<Record<string, boolean>>({})

const pickupPresets = [
  'Đứng tại Kiosk',
  'Nhận tại quầy thu ngân',
  'Nhờ nhân viên dẫn tới kệ',
]

const totalCount = computed(() => props.items.reduce((sum, item) => sum + item.quantity, 0))
const totalVnd = computed(() =>
  props.items.reduce((sum, item) => sum + item.product.price_vnd * item.quantity, 0),
)

const channelLabel = computed(() => {
  if (props.managerNotifyChannel === 'zalo') return 'Zalo'
  if (props.managerNotifyChannel === 'messenger') return 'Messenger'
  if (props.managerNotifyChannel === 'gmail') return 'Gmail'
  return ''
})

function formatVnd(amount: number) {
  return new Intl.NumberFormat(props.language === 'en' ? 'en-US' : 'vi-VN', {
    maximumFractionDigits: 0,
  }).format(amount) + (props.language === 'en' ? ' VND' : 'đ')
}

function formatTime(iso: string) {
  try {
    return new Intl.DateTimeFormat('vi-VN', {
      hour: '2-digit',
      minute: '2-digit',
      day: '2-digit',
      month: '2-digit',
      timeZone: 'Asia/Ho_Chi_Minh',
    }).format(new Date(iso))
  } catch {
    return ''
  }
}

function channelName(channel: 'zalo' | 'messenger' | 'gmail' | null | undefined) {
  if (channel === 'zalo') return 'Zalo'
  if (channel === 'messenger') return 'Messenger'
  if (channel === 'gmail') return 'Gmail'
  return ''
}

function cleanName(name: string) {
  return name.replace(/\s+/g, ' ').trim()
}

function hasImage(productId: string, url?: string | null) {
  return Boolean(url && !brokenImages.value[productId])
}

function onImgError(productId: string) {
  brokenImages.value = { ...brokenImages.value, [productId]: true }
}

function selectPickupPreset(preset: string) {
  pickupLocation.value = preset
}

function handleSubmit() {
  if (!props.items.length || props.isSubmitting) return
  emit('submit-order', {
    customerName: customerName.value.trim(),
    customerPhone: customerPhone.value.trim(),
    pickupLocation: pickupLocation.value.trim(),
    customerNote: customerNote.value.trim(),
  })
}

function onKeydown(event: KeyboardEvent) {
  if (event.key === 'Escape' && props.open) {
    event.preventDefault()
    emit('close')
  }
}

watch(() => props.open, (isOpen) => {
  if (isOpen) {
    if (props.initialTab) activeTab.value = props.initialTab
    else if (props.activeOrder) activeTab.value = 'cart'
    else if (!props.items.length && props.orders.length) activeTab.value = 'orders'
    else activeTab.value = 'cart'
    if (typeof window !== 'undefined') window.addEventListener('keydown', onKeydown)
  } else if (typeof window !== 'undefined') {
    window.removeEventListener('keydown', onKeydown)
  }
})

watch(() => props.activeOrder, (nextOrder) => {
  if (nextOrder) {
    customerNote.value = ''
    activeTab.value = 'cart'
  }
})

onUnmounted(() => {
  if (typeof window !== 'undefined') window.removeEventListener('keydown', onKeydown)
})
</script>

<template>
  <Teleport to="body">
    <Transition name="cart-drawer">
      <div
        v-if="open"
        class="cart-overlay"
        role="dialog"
        aria-modal="true"
        aria-label="Giỏ hàng &amp; hỗ trợ chọn hàng"
        @click.self="emit('close')"
      >
        <section class="cart-sheet">
          <div class="cart-sheet-handle" aria-hidden="true"><span></span></div>

          <header class="cart-sheet-header">
            <div class="cart-sheet-title-group">
              <div class="cart-sheet-icon">
                <ShoppingBagOpen :size="20" weight="duotone" aria-hidden="true" />
              </div>
              <div>
                <h2>Giỏ hàng của bạn</h2>
                <p v-if="channelLabel" class="cart-sheet-subtitle">
                  <BellRinging :size="13" weight="duotone" aria-hidden="true" />
                  <span>Gửi trực tiếp quản lý qua {{ channelLabel }} để hỗ trợ chọn &amp; trao hàng</span>
                </p>
                <p v-else class="cart-sheet-subtitle">
                  <span>Chọn sản phẩm để nhân viên hỗ trợ lấy hàng và trao trả tại quầy</span>
                </p>
              </div>
            </div>
            <button
              type="button"
              class="cart-sheet-close"
              aria-label="Đóng giỏ hàng"
              @click="emit('close')"
            >
              <CloseIcon :size="18" weight="bold" aria-hidden="true" />
            </button>
          </header>

          <nav v-if="orders.length > 0" class="cart-tabs" aria-label="Chuyển giữa giỏ hàng và đơn đã gửi">
            <button
              type="button"
              class="cart-tab"
              :class="{ 'is-active': activeTab === 'cart' }"
              @click="activeTab = 'cart'"
            >
              <ShoppingBagOpen :size="15" weight="duotone" aria-hidden="true" />
              <span>Giỏ hiện tại</span>
              <span v-if="totalCount > 0" class="cart-tab-badge">{{ totalCount }}</span>
            </button>
            <button
              type="button"
              class="cart-tab"
              :class="{ 'is-active': activeTab === 'orders' }"
              @click="activeTab = 'orders'"
            >
              <Receipt :size="15" weight="duotone" aria-hidden="true" />
              <span>Đơn đã gửi</span>
              <span class="cart-tab-badge is-orders">{{ orders.length }}</span>
            </button>
          </nav>

          <!-- TAB 1: CART OR CONFIRMATION RECEIPT -->
          <div v-if="activeTab === 'cart'" class="cart-sheet-body">
            <!-- Order Submitted Confirmation State -->
            <div v-if="activeOrder" class="cart-receipt-card" role="status" aria-live="polite">
              <div class="cart-receipt-banner" :class="{ 'is-delivered': activeOrder.notification_status === 'DELIVERED' }">
                <CheckCircle :size="28" weight="fill" aria-hidden="true" />
                <div>
                  <span class="cart-receipt-kicker">
                    {{
                      activeOrder.notification_status === 'DELIVERED' && activeOrder.notified_channel
                        ? `Đã báo quản lý qua ${channelName(activeOrder.notified_channel)}`
                        : 'Đã tạo phiếu hỗ trợ chọn hàng'
                    }}
                  </span>
                  <h3>Mã đơn #{{ activeOrder.order_code }}</h3>
                </div>
              </div>

              <p class="cart-receipt-message">{{ activeOrder.customer_message }}</p>

              <div v-if="activeOrder.pickup_location || activeOrder.customer_name || activeOrder.customer_note" class="cart-receipt-meta">
                <span v-if="activeOrder.pickup_location">
                  <MapPin :size="14" weight="duotone" aria-hidden="true" />
                  {{ activeOrder.pickup_location }}
                </span>
                <span v-if="activeOrder.customer_name">
                  <UserIcon :size="14" weight="duotone" aria-hidden="true" />
                  {{ activeOrder.customer_name }}<template v-if="activeOrder.customer_phone"> · {{ activeOrder.customer_phone }}</template>
                </span>
                <span v-if="activeOrder.customer_note">
                  <NotePencil :size="14" weight="duotone" aria-hidden="true" />
                  {{ activeOrder.customer_note }}
                </span>
              </div>

              <ul class="cart-receipt-items">
                <li v-for="item in activeOrder.items" :key="item.product_id" class="cart-receipt-item">
                  <div class="cart-receipt-item-copy">
                    <strong>{{ cleanName(item.name) }}</strong>
                    <small>
                      {{ formatVnd(item.unit_price_vnd) }} × {{ item.quantity }}
                      <template v-if="item.shelf_location"> · Kệ gợi ý: {{ item.shelf_location }}</template>
                    </small>
                  </div>
                  <span class="cart-receipt-item-total">{{ formatVnd(item.line_total_vnd) }}</span>
                </li>
              </ul>

              <div class="cart-receipt-total">
                <span>Tổng tạm tính ({{ activeOrder.item_count }} sản phẩm)</span>
                <strong>{{ formatVnd(activeOrder.total_vnd) }}</strong>
              </div>

              <div class="cart-receipt-actions">
                <button
                  type="button"
                  class="cart-primary-btn"
                  @click="emit('dismiss-active-order'); emit('close')"
                >
                  Tiếp tục mua sắm
                </button>
                <button
                  type="button"
                  class="cart-secondary-btn"
                  @click="emit('dismiss-active-order')"
                >
                  Tạo giỏ mới
                </button>
              </div>
            </div>

            <!-- Empty Cart State -->
            <div v-else-if="items.length === 0" class="cart-empty">
              <div class="cart-empty-icon">
                <ShoppingBagOpen :size="36" weight="duotone" aria-hidden="true" />
              </div>
              <h3>Giỏ hàng đang trống</h3>
              <p>Hỏi Mai sản phẩm bạn muốn mua, rồi bấm nút <strong>+ Giỏ</strong> trên thẻ sản phẩm để nhân viên chuẩn bị giúp nhé.</p>
              <button
                type="button"
                class="cart-suggest-btn"
                @click="emit('browse-suggestions')"
              >
                <Sparkle :size="16" weight="duotone" aria-hidden="true" />
                <span>Xem sản phẩm bán chạy nhất</span>
              </button>
            </div>

            <!-- Active Cart Items + Pickup Form -->
            <template v-else>
              <div class="cart-list-toolbar">
                <span>{{ items.length }} loại · {{ totalCount }} sản phẩm</span>
                <button type="button" class="cart-clear-btn" @click="emit('clear-cart')">
                  <Trash :size="14" weight="duotone" aria-hidden="true" />
                  <span>Xóa giỏ</span>
                </button>
              </div>

              <ul class="cart-items-list">
                <li v-for="entry in items" :key="entry.product.product_id" class="cart-item-row">
                  <div class="cart-item-thumb">
                    <img
                      v-if="hasImage(entry.product.product_id, entry.product.image_url)"
                      :src="entry.product.image_url!"
                      :alt="cleanName(entry.product.name)"
                      loading="lazy"
                      @error="onImgError(entry.product.product_id)"
                    />
                    <ImageIcon v-else :size="22" weight="duotone" aria-hidden="true" />
                  </div>

                  <div class="cart-item-info">
                    <strong class="cart-item-name">{{ cleanName(entry.product.name) }}</strong>
                    <div class="cart-item-sub">
                      <span class="cart-item-unit">{{ formatVnd(entry.product.price_vnd) }}<template v-if="entry.product.uom"> / {{ entry.product.uom }}</template></span>
                      <span v-if="entry.product.brand" class="cart-item-brand">{{ entry.product.brand }}</span>
                    </div>
                    <div class="cart-item-controls-row">
                      <div class="cart-stepper" role="group" :aria-label="`Số lượng ${cleanName(entry.product.name)}`">
                        <button
                          type="button"
                          class="cart-stepper-btn"
                          :aria-label="`Giảm số lượng ${cleanName(entry.product.name)}`"
                          @click="emit('update-qty', entry.product.product_id, entry.quantity - 1)"
                        >
                          <Minus :size="14" weight="bold" aria-hidden="true" />
                        </button>
                        <span class="cart-stepper-val">{{ entry.quantity }}</span>
                        <button
                          type="button"
                          class="cart-stepper-btn"
                          :disabled="entry.quantity >= 20"
                          :aria-label="`Tăng số lượng ${cleanName(entry.product.name)}`"
                          @click="emit('update-qty', entry.product.product_id, entry.quantity + 1)"
                        >
                          <Plus :size="14" weight="bold" aria-hidden="true" />
                        </button>
                      </div>
                      <strong class="cart-item-subtotal">{{ formatVnd(entry.product.price_vnd * entry.quantity) }}</strong>
                      <button
                        type="button"
                        class="cart-remove-btn"
                        :aria-label="`Xóa ${cleanName(entry.product.name)} khỏi giỏ`"
                        @click="emit('remove-item', entry.product.product_id)"
                      >
                        <Trash :size="15" weight="duotone" aria-hidden="true" />
                      </button>
                    </div>
                  </div>
                </li>
              </ul>

              <section class="cart-handoff-form" aria-label="Thông tin hỗ trợ nhận hàng">
                <div class="cart-handoff-heading">
                  <MapPin :size="15" weight="duotone" aria-hidden="true" />
                  <strong>Nhân viên hỗ trợ chọn &amp; trao hàng</strong>
                </div>

                <div class="cart-pickup-chips" role="group" aria-label="Chọn cách nhân viên hỗ trợ">
                  <button
                    v-for="preset in pickupPresets"
                    :key="preset"
                    type="button"
                    class="cart-pickup-chip"
                    :class="{ 'is-selected': pickupLocation === preset }"
                    @click="selectPickupPreset(preset)"
                  >
                    {{ preset }}
                  </button>
                </div>

                <div class="cart-form-grid">
                  <label class="cart-field">
                    <span class="cart-field-label">
                      <UserIcon :size="13" weight="duotone" aria-hidden="true" />
                      Tên / xưng hô (tùy chọn)
                    </span>
                    <input
                      v-model="customerName"
                      type="text"
                      maxlength="80"
                      placeholder="VD: Anh Minh, Chị Lan"
                      autocomplete="name"
                    />
                  </label>
                  <label class="cart-field">
                    <span class="cart-field-label">
                      <Phone :size="13" weight="duotone" aria-hidden="true" />
                      SĐT liên hệ (tùy chọn)
                    </span>
                    <input
                      v-model="customerPhone"
                      type="tel"
                      inputmode="tel"
                      maxlength="32"
                      placeholder="VD: 09xx..."
                      autocomplete="tel"
                    />
                  </label>
                </div>

                <label class="cart-field">
                  <span class="cart-field-label">
                    <NotePencil :size="13" weight="duotone" aria-hidden="true" />
                    Ghi chú cho nhân viên (tùy chọn)
                  </span>
                  <input
                    v-model="customerNote"
                    type="text"
                    maxlength="300"
                    placeholder="VD: Chọn hạn dùng xa, lấy giúp bịch lạnh..."
                  />
                </label>
              </section>
            </template>
          </div>

          <!-- TAB 2: ORDER HISTORY -->
          <div v-else class="cart-sheet-body">
            <ul class="cart-orders-history">
              <li v-for="ord in orders" :key="ord.order_id" class="cart-history-card">
                <header class="cart-history-header">
                  <div>
                    <strong>#{{ ord.order_code }}</strong>
                    <small>
                      <Clock :size="12" aria-hidden="true" />
                      {{ formatTime(ord.created_at) }}
                    </small>
                  </div>
                  <span
                    class="cart-history-status"
                    :class="{ 'is-delivered': ord.notification_status === 'DELIVERED' }"
                  >
                    {{
                      ord.notification_status === 'DELIVERED' && ord.notified_channel
                        ? `Đã báo qua ${channelName(ord.notified_channel)}`
                        : 'Phiếu tại quầy'
                    }}
                  </span>
                </header>
                <ul class="cart-history-items">
                  <li v-for="item in ord.items" :key="item.product_id">
                    <span>{{ cleanName(item.name) }} × {{ item.quantity }}</span>
                    <strong>{{ formatVnd(item.line_total_vnd) }}</strong>
                  </li>
                </ul>
                <footer class="cart-history-footer">
                  <span>{{ ord.item_count }} sản phẩm · {{ ord.pickup_location || 'Tại cửa hàng' }}</span>
                  <strong>{{ formatVnd(ord.total_vnd) }}</strong>
                </footer>
              </li>
            </ul>
          </div>

          <!-- STICKY FOOTER FOR ACTIVE CART -->
          <footer v-if="activeTab === 'cart' && !activeOrder && items.length > 0" class="cart-sheet-footer">
            <div v-if="submitError" class="cart-submit-error" role="alert">
              <WarningCircle :size="16" weight="fill" aria-hidden="true" />
              <span>{{ submitError }}</span>
            </div>

            <div class="cart-summary-row">
              <div>
                <span class="cart-summary-label">Tổng tạm tính ({{ totalCount }} sp)</span>
                <small class="cart-summary-disclaimer">Giá tham khảo · Thanh toán trực tiếp cùng nhân viên</small>
              </div>
              <strong class="cart-summary-total">{{ formatVnd(totalVnd) }}</strong>
            </div>

            <button
              type="button"
              class="cart-checkout-cta"
              :disabled="isSubmitting"
              @click="handleSubmit"
            >
              <PaperPlaneTilt :size="18" weight="fill" aria-hidden="true" />
              <span>
                {{
                  isSubmitting
                    ? 'Đang gửi tin nhắn tới quản lý...'
                    : channelLabel
                      ? `Đặt mua · Gửi quản lý (${channelLabel})`
                      : 'Đặt mua · Gửi nhân viên chọn hàng'
                }}
              </span>
            </button>
          </footer>
        </section>
      </div>
    </Transition>
  </Teleport>
</template>

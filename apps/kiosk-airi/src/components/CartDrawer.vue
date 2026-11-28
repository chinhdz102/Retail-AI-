<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from 'vue'
import QRCode from 'qrcode'
import {
  PhArrowClockwise as ArrowClockwise,
  PhArrowSquareOut as ArrowSquareOut,
  PhBellRinging as BellRinging,
  PhCheck as CheckIcon,
  PhCheckCircle as CheckCircle,
  PhClock as Clock,
  PhCopy as CopyIcon,
  PhImage as ImageIcon,
  PhMapPin as MapPin,
  PhMinus as Minus,
  PhNotePencil as NotePencil,
  PhPaperPlaneTilt as PaperPlaneTilt,
  PhPhone as Phone,
  PhPlus as Plus,
  PhQrCode as QrCodeIcon,
  PhReceipt as Receipt,
  PhShoppingBagOpen as ShoppingBagOpen,
  PhSparkle as Sparkle,
  PhStorefront as StorefrontIcon,
  PhTrash as Trash,
  PhUser as UserIcon,
  PhWarningCircle as WarningCircle,
  PhX as CloseIcon,
} from '@phosphor-icons/vue'

import type { AssistedCartItem, AssistedOrderResponse, LanguageCode } from '../types'
import { formatShelfLocation } from '../utils/shelfLocation'

const props = defineProps<{
  open: boolean
  items: AssistedCartItem[]
  orders: AssistedOrderResponse[]
  activeOrder: AssistedOrderResponse | null
  initialTab?: 'cart' | 'orders'
  isSubmitting: boolean
  isCheckingPayment?: boolean
  submitError: string | null
  language: LanguageCode
  managerNotifyChannel?: 'zalo' | 'messenger' | 'gmail' | null
  payosEnabled?: boolean
}>()

const emit = defineEmits<{
  close: []
  'update-qty': [productId: string, quantity: number]
  'remove-item': [productId: string]
  'clear-cart': []
  'submit-order': [payload: {
    paymentMethod: 'payos' | 'counter'
    customerName: string
    customerPhone: string
    pickupLocation: string
    customerNote: string
  }]
  'check-payment': [order: AssistedOrderResponse]
  'cancel-payment': [order: AssistedOrderResponse]
  'dismiss-active-order': []
  'browse-suggestions': []
}>()

const activeTab = ref<'cart' | 'orders'>('cart')
const paymentMethod = ref<'payos' | 'counter'>(props.payosEnabled ? 'payos' : 'counter')
const customerName = ref('')
const customerPhone = ref('')
const pickupLocation = ref('Đứng tại Kiosk')
const customerNote = ref('')
const brokenImages = ref<Record<string, boolean>>({})
const qrImageDataUrl = ref<string | null>(null)
const copiedField = ref<string | null>(null)
let copiedTimer: ReturnType<typeof setTimeout> | undefined

const pickupPresets = [
  'Đứng tại Kiosk',
  'Nhận tại quầy thu ngân',
  'Nhờ nhân viên dẫn tới kệ',
]

const totalCount = computed(() => props.items.reduce((sum, item) => sum + item.quantity, 0))
const totalVnd = computed(() =>
  props.items.reduce((sum, item) => sum + item.product.price_vnd * item.quantity, 0),
)

const isPendingPayOS = computed(() =>
  Boolean(
    props.activeOrder
      && props.activeOrder.payment_method === 'payos'
      && props.activeOrder.payment_status === 'PENDING_PAYOS',
  ),
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

async function copyText(field: string, value: string | null | undefined) {
  if (!value) return
  try {
    await navigator.clipboard?.writeText(value)
    copiedField.value = field
    if (copiedTimer) clearTimeout(copiedTimer)
    copiedTimer = setTimeout(() => {
      copiedField.value = null
    }, 1800)
  } catch {
    // Clipboard unavailable in restricted contexts.
  }
}

async function renderQrCode(qrPayload: string | null | undefined) {
  if (!qrPayload) {
    qrImageDataUrl.value = null
    return
  }
  try {
    qrImageDataUrl.value = await QRCode.toDataURL(qrPayload, {
      width: 240,
      margin: 1,
      errorCorrectionLevel: 'M',
      color: { dark: '#0f172a', light: '#ffffff' },
    })
  } catch {
    qrImageDataUrl.value = null
  }
}

function handleSubmit() {
  if (!props.items.length || props.isSubmitting) return
  const chosenMethod: 'payos' | 'counter' = props.payosEnabled ? paymentMethod.value : 'counter'
  emit('submit-order', {
    paymentMethod: chosenMethod,
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

watch(() => props.payosEnabled, (enabled) => {
  if (enabled) paymentMethod.value = 'payos'
  else paymentMethod.value = 'counter'
}, { immediate: true })

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
    if (nextOrder.payment_status === 'PAID' || nextOrder.payment_method === 'counter') {
      customerNote.value = ''
    }
    activeTab.value = 'cart'
    void renderQrCode(nextOrder.payos_qr_code)
  } else {
    qrImageDataUrl.value = null
  }
}, { immediate: true })

onUnmounted(() => {
  if (typeof window !== 'undefined') window.removeEventListener('keydown', onKeydown)
  if (copiedTimer) clearTimeout(copiedTimer)
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
        aria-label="Giỏ hàng &amp; thanh toán PayOS"
        @click.self="emit('close')"
      >
        <section class="cart-sheet">
          <div class="cart-sheet-handle" aria-hidden="true"><span></span></div>

          <header class="cart-sheet-header">
            <div class="cart-sheet-title-group">
              <div class="cart-sheet-icon">
                <QrCodeIcon v-if="isPendingPayOS" :size="20" weight="duotone" aria-hidden="true" />
                <ShoppingBagOpen v-else :size="20" weight="duotone" aria-hidden="true" />
              </div>
              <div>
                <h2>{{ isPendingPayOS ? 'Thanh toán QR PayOS' : 'Giỏ hàng của bạn' }}</h2>
                <p v-if="isPendingPayOS" class="cart-sheet-subtitle">
                  <BellRinging :size="13" weight="duotone" aria-hidden="true" />
                  <span>Thanh toán xong tự động báo quản lý{{ channelLabel ? ` qua ${channelLabel}` : '' }} soạn hàng ngay</span>
                </p>
                <p v-else-if="channelLabel" class="cart-sheet-subtitle">
                  <BellRinging :size="13" weight="duotone" aria-hidden="true" />
                  <span>Tự động báo quản lý qua {{ channelLabel }} để nhân viên hỗ trợ chọn &amp; trao hàng</span>
                </p>
                <p v-else class="cart-sheet-subtitle">
                  <span>Chọn sản phẩm để thanh toán PayOS hoặc nhờ nhân viên lấy hàng tại quầy</span>
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

          <nav v-if="orders.length > 0 && !isPendingPayOS" class="cart-tabs" aria-label="Chuyển giữa giỏ hàng và đơn đã gửi">
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
              <span>Đơn đã tạo</span>
              <span class="cart-tab-badge is-orders">{{ orders.length }}</span>
            </button>
          </nav>

          <!-- TAB 1: PAYOS QR / RECEIPT / ACTIVE CART -->
          <div v-if="activeTab === 'cart'" class="cart-sheet-body">
            <!-- STATE A: PENDING PAYOS VIETQR PAYMENT SCREEN -->
            <div v-if="isPendingPayOS && activeOrder" class="payos-qr-panel" role="region" aria-label="Mã QR thanh toán PayOS">
              <div class="payos-status-banner">
                <span class="payos-live-dot" aria-hidden="true"></span>
                <div>
                  <strong>Đang chờ chuyển khoản · #{{ activeOrder.order_code }}</strong>
                  <small>Hệ thống tự kiểm tra mỗi 3 giây — thanh toán xong sẽ tự động nhắn cho quản lý.</small>
                </div>
              </div>

              <div class="payos-qr-stage">
                <img
                  v-if="qrImageDataUrl"
                  :src="qrImageDataUrl"
                  alt="Mã VietQR thanh toán PayOS"
                  class="payos-qr-img"
                />
                <div v-else class="payos-qr-fallback">
                  <QrCodeIcon :size="48" weight="duotone" aria-hidden="true" />
                  <span>Mở trang thanh toán PayOS bên dưới để quét mã VietQR</span>
                </div>
                <strong class="payos-qr-amount">{{ formatVnd(activeOrder.total_vnd) }}</strong>
                <span class="payos-qr-hint">Quét bằng ứng dụng ngân hàng bất kỳ (VietQR / Napas 247)</span>
              </div>

              <div class="payos-bank-details">
                <div v-if="activeOrder.payos_account_number" class="payos-bank-row">
                  <span>Số tài khoản</span>
                  <div class="payos-bank-val">
                    <strong>{{ activeOrder.payos_account_number }}</strong>
                    <button
                      type="button"
                      class="payos-copy-btn"
                      aria-label="Sao chép số tài khoản"
                      @click="copyText('account', activeOrder.payos_account_number)"
                    >
                      <CheckIcon v-if="copiedField === 'account'" :size="13" weight="bold" aria-hidden="true" />
                      <CopyIcon v-else :size="13" weight="duotone" aria-hidden="true" />
                    </button>
                  </div>
                </div>
                <div v-if="activeOrder.payos_account_name" class="payos-bank-row">
                  <span>Chủ tài khoản</span>
                  <strong>{{ activeOrder.payos_account_name }}</strong>
                </div>
                <div v-if="activeOrder.payos_description" class="payos-bank-row">
                  <span>Nội dung CK</span>
                  <div class="payos-bank-val">
                    <strong>{{ activeOrder.payos_description }}</strong>
                    <button
                      type="button"
                      class="payos-copy-btn"
                      aria-label="Sao chép nội dung chuyển khoản"
                      @click="copyText('memo', activeOrder.payos_description)"
                    >
                      <CheckIcon v-if="copiedField === 'memo'" :size="13" weight="bold" aria-hidden="true" />
                      <CopyIcon v-else :size="13" weight="duotone" aria-hidden="true" />
                    </button>
                  </div>
                </div>
              </div>

              <div v-if="submitError" class="cart-submit-error" role="alert">
                <WarningCircle :size="16" weight="fill" aria-hidden="true" />
                <span>{{ submitError }}</span>
              </div>

              <div class="payos-actions">
                <button
                  type="button"
                  class="cart-primary-btn"
                  :disabled="isCheckingPayment"
                  @click="emit('check-payment', activeOrder)"
                >
                  <ArrowClockwise :size="17" weight="bold" aria-hidden="true" />
                  <span>{{ isCheckingPayment ? 'Đang kiểm tra giao dịch...' : 'Tôi đã chuyển khoản · Kiểm tra ngay' }}</span>
                </button>
                <a
                  v-if="activeOrder.payos_checkout_url"
                  :href="activeOrder.payos_checkout_url"
                  target="_blank"
                  rel="noopener noreferrer"
                  class="payos-external-btn"
                >
                  <ArrowSquareOut :size="16" weight="duotone" aria-hidden="true" />
                  <span>Mở trang thanh toán PayOS</span>
                </a>
                <button
                  type="button"
                  class="cart-secondary-btn"
                  @click="emit('cancel-payment', activeOrder)"
                >
                  Hủy mã QR · Quay lại giỏ hàng
                </button>
              </div>
            </div>

            <!-- STATE B: CONFIRMED / PAID RECEIPT -->
            <div v-else-if="activeOrder" class="cart-receipt-card" role="status" aria-live="polite">
              <div
                class="cart-receipt-banner"
                :class="{
                  'is-delivered': activeOrder.payment_status === 'PAID' || activeOrder.notification_status === 'DELIVERED',
                }"
              >
                <CheckCircle :size="28" weight="fill" aria-hidden="true" />
                <div>
                  <span class="cart-receipt-kicker">
                    <template v-if="activeOrder.payment_status === 'PAID'">
                      Đã thanh toán PayOS{{ activeOrder.notification_status === 'DELIVERED' && activeOrder.notified_channel ? ` · Đã báo ${channelName(activeOrder.notified_channel)}` : '' }}
                    </template>
                    <template v-else-if="activeOrder.notification_status === 'DELIVERED' && activeOrder.notified_channel">
                      Đã báo quản lý qua {{ channelName(activeOrder.notified_channel) }}
                    </template>
                    <template v-else>
                      Đã tạo phiếu hỗ trợ chọn hàng
                    </template>
                  </span>
                  <h3>Mã đơn #{{ activeOrder.order_code }}</h3>
                </div>
              </div>

              <p class="cart-receipt-message">{{ activeOrder.customer_message }}</p>

              <div v-if="activeOrder.payos_transaction_ref || activeOrder.pickup_location || activeOrder.customer_name || activeOrder.customer_note" class="cart-receipt-meta">
                <span v-if="activeOrder.payos_transaction_ref">
                  <QrCodeIcon :size="14" weight="duotone" aria-hidden="true" />
                  Mã GD PayOS: {{ activeOrder.payos_transaction_ref }}
                </span>
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
                      <template v-if="item.shelf_location"> · Kệ gợi ý: {{ formatShelfLocation(item.shelf_location) }}</template>
                    </small>
                  </div>
                  <span class="cart-receipt-item-total">{{ formatVnd(item.line_total_vnd) }}</span>
                </li>
              </ul>

              <div class="cart-receipt-total">
                <span>{{ activeOrder.payment_status === 'PAID' ? 'Đã thanh toán PayOS' : 'Tổng tạm tính' }} ({{ activeOrder.item_count }} sản phẩm)</span>
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

            <!-- STATE C: EMPTY CART -->
            <div v-else-if="items.length === 0" class="cart-empty">
              <div class="cart-empty-icon">
                <ShoppingBagOpen :size="36" weight="duotone" aria-hidden="true" />
              </div>
              <h3>Giỏ hàng đang trống</h3>
              <p>Hỏi Mai sản phẩm bạn muốn mua, rồi bấm nút <strong>+ Giỏ</strong> trên thẻ sản phẩm để thanh toán QR hoặc nhờ nhân viên chọn giúp nhé.</p>
              <button
                type="button"
                class="cart-suggest-btn"
                @click="emit('browse-suggestions')"
              >
                <Sparkle :size="16" weight="duotone" aria-hidden="true" />
                <span>Xem tất cả sản phẩm theo danh mục</span>
              </button>
            </div>

            <!-- STATE D: ACTIVE CART ITEMS + PAYMENT METHOD + PICKUP FORM -->
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

              <!-- Payment Method Chooser (PayOS QR vs Counter Handoff) -->
              <section v-if="payosEnabled" class="payos-method-section" aria-label="Chọn hình thức thanh toán">
                <div class="cart-handoff-heading">
                  <QrCodeIcon :size="15" weight="duotone" aria-hidden="true" />
                  <strong>Hình thức thanh toán</strong>
                </div>
                <div class="payos-method-grid">
                  <button
                    type="button"
                    class="payos-method-card"
                    :class="{ 'is-selected': paymentMethod === 'payos' }"
                    @click="paymentMethod = 'payos'"
                  >
                    <QrCodeIcon :size="20" weight="duotone" aria-hidden="true" />
                    <div>
                      <strong>Chuyển khoản QR PayOS</strong>
                      <small>Thanh toán xong tự động báo quản lý{{ channelLabel ? ` (${channelLabel})` : '' }} soạn &amp; trao hàng</small>
                    </div>
                  </button>
                  <button
                    type="button"
                    class="payos-method-card"
                    :class="{ 'is-selected': paymentMethod === 'counter' }"
                    @click="paymentMethod = 'counter'"
                  >
                    <StorefrontIcon :size="20" weight="duotone" aria-hidden="true" />
                    <div>
                      <strong>Nhờ nhân viên &amp; trả tại quầy</strong>
                      <small>Gửi danh sách ngay cho nhân viên hỗ trợ chọn hàng</small>
                    </div>
                  </button>
                </div>
              </section>

              <section class="cart-handoff-form" aria-label="Thông tin hỗ trợ nhận hàng">
                <div class="cart-handoff-heading">
                  <MapPin :size="15" weight="duotone" aria-hidden="true" />
                  <strong>Thông tin nhận hàng &amp; hỗ trợ</strong>
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
                    :class="{ 'is-delivered': ord.payment_status === 'PAID' || ord.notification_status === 'DELIVERED' }"
                  >
                    <template v-if="ord.payment_status === 'PAID'">Đã TT PayOS</template>
                    <template v-else-if="ord.payment_status === 'PENDING_PAYOS'">Chờ QR PayOS</template>
                    <template v-else-if="ord.notification_status === 'DELIVERED' && ord.notified_channel">
                      Đã báo {{ channelName(ord.notified_channel) }}
                    </template>
                    <template v-else>Phiếu tại quầy</template>
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
                <span class="cart-summary-label">Tổng thanh toán ({{ totalCount }} sp)</span>
                <small class="cart-summary-disclaimer">
                  {{
                    payosEnabled && paymentMethod === 'payos'
                      ? 'Quét mã VietQR PayOS · Thanh toán xong tự động báo quản lý'
                      : 'Giá tham khảo · Nhân viên hỗ trợ chọn hàng và thanh toán tại quầy'
                  }}
                </small>
              </div>
              <strong class="cart-summary-total">{{ formatVnd(totalVnd) }}</strong>
            </div>

            <button
              type="button"
              class="cart-checkout-cta"
              :disabled="isSubmitting"
              @click="handleSubmit"
            >
              <QrCodeIcon v-if="payosEnabled && paymentMethod === 'payos'" :size="19" weight="duotone" aria-hidden="true" />
              <PaperPlaneTilt v-else :size="18" weight="fill" aria-hidden="true" />
              <span>
                <template v-if="isSubmitting">
                  {{ payosEnabled && paymentMethod === 'payos' ? 'Đang tạo mã QR PayOS...' : 'Đang gửi tin nhắn tới quản lý...' }}
                </template>
                <template v-else-if="payosEnabled && paymentMethod === 'payos'">
                  Thanh toán QR PayOS · {{ formatVnd(totalVnd) }}
                </template>
                <template v-else>
                  {{ channelLabel ? `Đặt mua · Gửi quản lý (${channelLabel})` : 'Đặt mua · Gửi nhân viên chọn hàng' }}
                </template>
              </span>
            </button>
          </footer>
        </section>
      </div>
    </Transition>
  </Teleport>
</template>

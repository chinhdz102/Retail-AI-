<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import {
  PhArrowClockwise as ArrowClockwise,
  PhArrowUp as ArrowUp,
  PhBarcode as Barcode,
  PhImage as ImageIcon,
  PhCaretRight as CaretRight,
  PhMicrophone as Microphone,
  PhPause as Pause,
  PhPlus as Plus,
  PhMinus as Minus,
  PhShoppingCart as ShoppingCartIcon,
  PhSlidersHorizontal as SlidersHorizontal,
  PhSparkle as Sparkle,
  PhStop as Stop,
  PhTrash as Trash,
  PhWaveform as Waveform,
} from '@phosphor-icons/vue'
import type {
  AssistantStatus,
  ChatMessage,
  CheckoutQuote,
  SandboxOrder,
  ShoppingCart,
  VisionIdentifyResponse,
} from '../types'
const props = defineProps<{
  messages: ChatMessage[]
  status: AssistantStatus
  draft: string
  isRecording: boolean
  isVoiceActive: boolean
  isHandsFree: boolean
  voiceHint: string
  errorMessage: string | null
  cart?: ShoppingCart | null
  checkoutQuote?: CheckoutQuote | null
  order?: SandboxOrder | null
  microphoneLevel?: number
  microphoneDevices?: Array<{
    id: string
    label: string
  }>
  selectedMicrophone?: string
  activeMicrophone?: string
  visionResult?: VisionIdentifyResponse | null
}>()
const conversation = ref<HTMLElement | null>(null)
watch(
  () => [props.messages.length, props.status],
  async () => {
    await nextTick()
    if (conversation.value)
      conversation.value.scrollTop = conversation.value.scrollHeight
  },
)
const emit = defineEmits<{
  'update:draft': [value: string]
  send: []
  'quick-prompt': [value: string]
  'toggle-recording': []
  'stop-speaking': []
  'change-microphone': [deviceId: string]
  retry: []
  'add-product': [productId: string]
  'set-cart-quantity': [productId: string, quantity: number]
  'prepare-checkout': []
  'confirm-checkout': []
  'cancel-checkout': []
  'simulate-payment': [outcome: 'PAID' | 'FAILED']
  'clear-order': []
  'end-session': []
  'request-support': []
  'identify-image': [file: File]
}>()

const copy = computed(() => {
  return {
    eyebrow: 'Cùng Sky mua sắm',
    title: 'Mình có thể giúp gì bạn?',
    emptyTitle: 'Bạn muốn tìm gì hôm nay?',
    emptyBody:
      'Tìm món ngon, chọn đồ phù hợp hay so sánh sản phẩm — cứ hỏi mình nhé.',
    user: 'Bạn',
    thinking: 'Mình đang tìm câu trả lời',
    transcribing: 'Mình đang nghe lại câu nói của bạn',
    errorTitle: 'Sky chưa trả lời được câu này',
    placeholder: 'Hỏi Sky về sản phẩm bạn cần…',
    hint: 'Enter để gửi',
    hintSecond: 'Shift + Enter để xuống dòng',
    retry: 'Thử lại',
    micStart: 'Bắt đầu nói',
    micStop: 'Dừng ghi âm',
    voiceStop: 'Dừng nghe',
    stopSpeaking: 'Dừng Sky nói',
    languageLabel: 'Ngôn ngữ hội thoại',
    sectionLabel: 'Trò chuyện với Sky',
    microphone: 'Micro đầu vào',
    defaultMicrophone: 'Micro mặc định',
    microphoneAriaLabel: 'Chọn micro đầu vào',
    microphoneLevel: 'Mức tín hiệu micro',
    inputLabel: 'Nhập câu hỏi cho Sky',
    quickPromptsLabel: 'Câu hỏi gợi ý',
    sendLabel: 'Gửi câu hỏi',
    imageLabel: 'Nhận diện ảnh sản phẩm',
    imageHint: 'Chụp hoặc chọn ảnh mặt trước sản phẩm',
  }
})
const shoppingText = computed(() => {
  return {
    cart: 'Giỏ hàng demo',
    sandbox: 'SANDBOX',
    total: 'Tổng',
    checkout: 'Xem lại checkout',
    confirm: 'Xác nhận đơn demo',
    cancel: 'Quay lại giỏ',
    order: 'Đơn sandbox',
    simulatePaid: 'Mô phỏng thanh toán thành công',
    simulateFailed: 'Mô phỏng thất bại',
    add: 'Thêm',
    stock: 'còn bán trong demo',
    end: 'Kết thúc phiên',
    support: 'Gọi nhân viên · demo',
    ticketLabel: 'Yêu cầu hỗ trợ demo',
    ticketDemo: 'Chỉ là bản demo · chưa liên hệ nhân viên thật',
  }
})
const visibleCart = computed(
  () => props.checkoutQuote?.cart ?? props.cart ?? null,
)
const visibleOrder = computed(() => props.order ?? null)
const stateLabel = computed(() => ({
  idle: 'Sẵn sàng', listening: 'Đang nghe', transcribing: 'Đang nhận diện',
  thinking: 'Đang suy nghĩ', speaking: 'Đang trả lời', error: 'Cần thử lại',
}[props.status]))
function formatVnd(amount: number) {
  return (
    new Intl.NumberFormat('vi-VN', {
      maximumFractionDigits: 0,
    }).format(amount) + 'đ'
  )
}
const quickPrompts = computed(() => {
  return [
    { label: 'Giá Coca Zero', icon: Barcode },
    { label: 'Gợi ý snack không cay', icon: Sparkle },
    { label: 'Sữa ở kệ nào?', icon: CaretRight },
  ]
})
function onKeydown(event: KeyboardEvent) {
  if (event.key === 'Enter' && !event.shiftKey) {
    event.preventDefault()
    emit('send')
  }
}
function onImageChange(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = ''
  if (file) emit('identify-image', file)
}
</script>

<template>
  <section class="chat-panel" :aria-label="copy.sectionLabel">
    <header class="chat-header">
      <div class="chat-title-copy">
        <p class="panel-eyebrow">{{ copy.eyebrow }}</p>
        <h1>{{ copy.title }}</h1>
        <p class="chat-voice-hint" role="status" aria-live="polite">
          {{ voiceHint }}
        </p>
        <meter
          v-if="isVoiceActive"
          class="microphone-level"
          min="0"
          max="1"
          :value="microphoneLevel ?? 0"
          :aria-label="copy.microphoneLevel"
        ></meter>
      </div>
      <div class="chat-header-tools">
        <div class="voice-mode-chip" :class="{ active: isHandsFree }" :data-state="status">
          <Waveform :size="17" weight="duotone" aria-hidden="true" />
          <span>{{ stateLabel }}</span>
        </div>
        <details class="chat-settings">
          <summary aria-label="Cài đặt hội thoại" title="Cài đặt hội thoại">
            <SlidersHorizontal :size="18" aria-hidden="true" />
          </summary>
          <div class="chat-settings-popover">
            <span class="language-option selected">Tiếng Việt</span>
        <button
          type="button"
          class="end-session-button"
          @click="emit('end-session')"
        >
          {{ shoppingText.end }}
        </button>
        <button
          type="button"
          class="support-request-button"
          @click="emit('request-support')"
        >
          {{ shoppingText.support }}
        </button>
      <label v-if="microphoneDevices?.length" class="microphone-picker">
        <span>{{ copy.microphone }}</span>
        <select
          :value="selectedMicrophone || ''"
          :disabled="['thinking', 'transcribing', 'speaking'].includes(status)"
          :aria-label="copy.microphoneAriaLabel"
          @change="
            emit(
              'change-microphone',
              ($event.target as HTMLSelectElement).value,
            )
          "
        >
          <option value="">
            {{ activeMicrophone || copy.defaultMicrophone }}
          </option>
          <option
            v-for="device in microphoneDevices"
            :key="device.id"
            :value="device.id"
          >
            {{ device.label }}
          </option>
        </select>
      </label>
          </div>
        </details>
      </div>
    </header>

    <div
      ref="conversation"
      class="conversation"
      aria-live="polite"
      aria-label="Nội dung hội thoại"
    >
      <div v-if="messages.length === 0" class="conversation-empty">
        <div class="empty-icon">
          <Sparkle :size="22" weight="duotone" aria-hidden="true" />
        </div>
        <h2>{{ copy.emptyTitle }}</h2>
        <p>{{ copy.emptyBody }}</p>
      </div>

      <article
        v-for="message in messages"
        :key="message.id"
        class="message-row"
        :class="`message-${message.role}`"
      >
        <div class="message-meta">
          <span class="message-author">{{
            message.role === 'assistant' ? 'Sky' : copy.user
          }}</span>
        </div>
        <p class="message-bubble">{{ message.text }}</p>
        <div
          v-if="message.supportTicket"
          class="support-ticket-card"
          role="status"
        >
          <strong>{{ shoppingText.ticketLabel }}</strong>
          <span
            >{{ message.supportTicket.ticket_id }} ·
            {{ message.supportTicket.status }}</span
          >
          <small>{{
            message.supportTicket.demo ? shoppingText.ticketDemo : ''
          }}</small>
        </div>
        <div v-if="message.products?.length" class="message-product-list">
          <article
            v-for="product in message.products"
            :key="product.product_id"
            class="message-product-card"
          >
            <div class="message-product-copy">
              <strong>{{ product.name }}</strong>
              <span class="product-card-price">{{ formatVnd(product.price_vnd) }}</span>
            </div>
            <button
              v-if="visibleCart"
              type="button"
              class="small-action-button"
              @click="emit('add-product', product.product_id)"
            >
              <ShoppingCartIcon
                :size="16"
                weight="duotone"
                aria-hidden="true"
              />
              {{ shoppingText.add }}
            </button>
          </article>
        </div>
      </article>

      <div
        v-if="status === 'thinking' || status === 'transcribing'"
        class="message-row message-assistant"
      >
        <div class="message-meta"><span class="message-author">Sky</span></div>
        <div class="message-bubble typing-bubble" role="status">
          <span>{{
            status === 'transcribing' ? copy.transcribing : copy.thinking
          }}</span>
          <span class="typing-dots" aria-hidden="true"
            ><i></i><i></i><i></i
          ></span>
        </div>
      </div>
    </div>

    <section v-if="visionResult" class="vision-result" aria-live="polite">
      <div class="vision-result-header">
        <div>
          <strong>Nhận diện từ ảnh</strong>
          <span>{{ visionResult.top_match_confident ? 'Độ khớp tốt' : 'Cần đối chiếu thêm' }}</span>
        </div>
        <small>{{ visionResult.gallery_size.toLocaleString('vi-VN') }} sản phẩm trong catalog</small>
      </div>
      <article v-for="match in visionResult.matches" :key="match.product_id" class="vision-match-card">
        <div>
          <strong>{{ match.name }}</strong>
          <span>{{ Math.round(match.score * 100) }}% khớp · {{ match.shelf_friendly || 'Chưa có vị trí kệ' }}</span>
        </div>
      </article>
      <p class="vision-result-note">Ảnh toàn kệ cần tách từng sản phẩm trước khi nhận diện.</p>
    </section>

    <section
      v-if="
        (visibleCart?.items.length ?? 0) > 0 || visibleOrder || checkoutQuote
      "
      class="shopping-panel"
      aria-live="polite"
    >
      <header class="shopping-panel-header">
        <div class="shopping-panel-title">
          <ShoppingCartIcon :size="17" weight="duotone" aria-hidden="true" />
          <strong>{{
            visibleOrder ? shoppingText.order : shoppingText.cart
          }}</strong>
        </div>
        <span class="sandbox-chip">{{ shoppingText.sandbox }}</span>
      </header>

      <div v-if="visibleCart && !visibleOrder" class="shopping-cart-lines">
        <div
          v-for="item in visibleCart.items"
          :key="item.product_id"
          class="shopping-cart-line"
        >
          <div class="shopping-cart-line-copy">
            <strong>{{ item.name }}</strong>
            <span
              >{{ formatVnd(item.unit_price_vnd) }} × {{ item.quantity }} =
              {{ formatVnd(item.line_total_vnd) }}</span
            >
          </div>
          <div v-if="!checkoutQuote" class="quantity-actions">
            <button
              type="button"
              :aria-label="`Reduce ${item.name}`"
              @click="
                emit(
                  'set-cart-quantity',
                  item.product_id,
                  Math.max(0, item.quantity - 1),
                )
              "
            >
              <Minus :size="15" aria-hidden="true" />
            </button>
            <button
              type="button"
              :aria-label="`Add one ${item.name}`"
              @click="
                emit('set-cart-quantity', item.product_id, item.quantity + 1)
              "
            >
              <Plus :size="15" aria-hidden="true" />
            </button>
            <button
              type="button"
              :aria-label="`Remove ${item.name}`"
              @click="emit('set-cart-quantity', item.product_id, 0)"
            >
              <Trash :size="15" aria-hidden="true" />
            </button>
          </div>
        </div>
        <div class="shopping-total">
          <span>{{ shoppingText.total }}</span>
          <strong>{{ formatVnd(visibleCart.total_vnd) }}</strong>
        </div>
      </div>

      <div v-if="visibleOrder" class="sandbox-order-status">
        <span>{{ visibleOrder.order_id }}</span>
        <strong>{{ visibleOrder.payment_status }}</strong>
        <span>{{ formatVnd(visibleOrder.amount_vnd) }}</span>
      </div>

      <div class="shopping-actions">
        <template v-if="checkoutQuote">
          <button
            type="button"
            class="small-action-button primary"
            @click="emit('confirm-checkout')"
          >
            {{ shoppingText.confirm }}
          </button>
          <button
            type="button"
            class="small-action-button"
            @click="emit('cancel-checkout')"
          >
            {{ shoppingText.cancel }}
          </button>
        </template>
        <button
          v-else-if="visibleCart && !visibleOrder && visibleCart.items.length"
          type="button"
          class="small-action-button primary"
          @click="emit('prepare-checkout')"
        >
          {{ shoppingText.checkout }}
        </button>
        <template v-if="visibleOrder?.payment_status === 'PENDING'">
          <button
            type="button"
            class="small-action-button primary"
            @click="emit('simulate-payment', 'PAID')"
          >
            {{ shoppingText.simulatePaid }}
          </button>
          <button
            type="button"
            class="small-action-button"
            @click="emit('simulate-payment', 'FAILED')"
          >
            {{ shoppingText.simulateFailed }}
          </button>
        </template>
        <button
          v-else-if="visibleOrder && visibleOrder.payment_status !== 'PAID'"
          type="button"
          class="small-action-button"
          @click="emit('clear-order')"
        >
          {{ shoppingText.cancel }}
        </button>
      </div>
      <p class="sandbox-note">
        {{ 'Chỉ dùng để demo, không phát sinh thanh toán thật.' }}
      </p>
    </section>

    <div v-if="errorMessage" class="chat-error" role="alert">
      <div>
        <strong>{{ copy.errorTitle }}</strong>
        <span>{{ errorMessage }}</span>
      </div>
      <button
        type="button"
        class="icon-button error-retry"
        :aria-label="copy.retry"
        @click="emit('retry')"
      >
        <ArrowClockwise :size="18" weight="bold" aria-hidden="true" />
      </button>
    </div>

    <div
      v-if="messages.length === 0"
      class="quick-prompts"
      :aria-label="copy.quickPromptsLabel"
    >
      <button
        v-for="prompt in quickPrompts"
        :key="prompt.label"
        type="button"
        class="quick-prompt"
        :data-quick-prompt="prompt.label"
        @click="emit('quick-prompt', prompt.label)"
      >
        <component
          :is="prompt.icon"
          :size="16"
          weight="duotone"
          aria-hidden="true"
        />
        <span>{{ prompt.label }}</span>
      </button>
    </div>

    <form class="composer" @submit.prevent="emit('send')">
      <label class="sr-only" for="assistant-input">{{ copy.inputLabel }}</label>
      <textarea
        id="assistant-input"
        :value="draft"
        rows="1"
        maxlength="2000"
        :placeholder="copy.placeholder"
        @input="
          emit('update:draft', ($event.target as HTMLTextAreaElement).value)
        "
        @keydown="onKeydown"
      ></textarea>
      <div class="composer-actions">
        <label class="image-upload-button" :aria-label="copy.imageLabel" :title="copy.imageHint">
          <ImageIcon :size="19" weight="duotone" aria-hidden="true" />
          <input type="file" accept="image/*" @change="onImageChange" />
        </label>
        <button
          type="button"
          class="mic-button"
          :class="{ recording: isRecording }"
          :aria-label="
            isVoiceActive || isHandsFree ? copy.voiceStop : copy.micStart
          "
          :disabled="
            !isHandsFree && (status === 'transcribing' || status === 'thinking')
          "
          @click="emit('toggle-recording')"
        >
          <Stop
            v-if="isRecording || isVoiceActive"
            :size="21"
            weight="fill"
            aria-hidden="true"
          />
          <Microphone v-else :size="21" weight="duotone" aria-hidden="true" />
        </button>
        <button
          v-if="status === 'speaking'"
          type="button"
          class="stop-speaking"
          :aria-label="copy.stopSpeaking"
          @click="emit('stop-speaking')"
        >
          <Pause :size="18" weight="fill" aria-hidden="true" />
        </button>
        <button
          type="submit"
          class="send-button"
          :aria-label="copy.sendLabel"
          :disabled="
            !draft.trim() || status === 'thinking' || status === 'transcribing'
          "
        >
          <ArrowUp :size="20" weight="bold" aria-hidden="true" />
        </button>
      </div>
    </form>
    <p class="composer-hint">
      {{ copy.hint }} <span>•</span> {{ copy.hintSecond }}
    </p>
  </section>
</template>

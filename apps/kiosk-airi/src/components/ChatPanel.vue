<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import {
  PhArrowClockwise as ArrowClockwise,
  PhArrowUp as ArrowUp,
  PhBarcode as Barcode,
  PhCaretRight as CaretRight,
  PhMicrophone as Microphone,
  PhPause as Pause,
  PhPlus as Plus,
  PhMinus as Minus,
  PhShoppingCart as ShoppingCartIcon,
  PhSparkle as Sparkle,
  PhStop as Stop,
  PhTrash as Trash,
  PhTranslate as Translate,
  PhWaveform as Waveform,
} from '@phosphor-icons/vue'

import type {
  AssistantStatus,
  ChatMessage,
  CheckoutQuote,
  LanguageCode,
  ProductCard,
  SandboxOrder,
  ShoppingCart,
} from '../types'

const props = defineProps<{
  messages: ChatMessage[]
  status: AssistantStatus
  draft: string
  isRecording: boolean
  isVoiceActive: boolean
  isHandsFree: boolean
  voiceHint: string
  language: LanguageCode
  errorMessage: string | null
  cart?: ShoppingCart | null
  checkoutQuote?: CheckoutQuote | null
  order?: SandboxOrder | null
  microphoneLevel?: number
  microphoneDevices?: Array<{ id: string; label: string }>
  selectedMicrophone?: string
  activeMicrophone?: string
}>()

const conversation = ref<HTMLElement | null>(null)
watch(() => [props.messages.length, props.status], async () => {
  await nextTick()
  if (conversation.value) conversation.value.scrollTop = conversation.value.scrollHeight
})

const emit = defineEmits<{
  'update:draft': [value: string]
  send: []
  'quick-prompt': [value: string]
  'toggle-recording': []
  'stop-speaking': []
  'change-language': [value: LanguageCode]
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
}>()

const languageOptions: Array<{ code: LanguageCode; short: string; label: string }> = [
  { code: 'vi', short: 'VN', label: 'Tiếng Việt' },
  { code: 'en', short: 'EN', label: 'English' },
  { code: 'zh', short: '中', label: '中文' },
]

const copy = computed(() => {
  if (props.language === 'en') {
    return {
      eyebrow: 'Hands-free conversation',
      title: "Let's talk",
      emptyTitle: 'What can I help you find?',
      emptyBody: 'Speak naturally or type a question. I will use verified store information.',
      user: 'You',
      thinking: 'Finding the right answer',
      transcribing: 'Listening back to your voice',
      errorTitle: 'Assistant connection issue',
      placeholder: 'Try: How much is Coca Zero?',
      hint: 'Enter to send',
      hintSecond: 'Shift + Enter for a new line',
      retry: 'Try again',
      micStart: 'Start speaking',
      micStop: 'Stop recording',
      voiceStop: 'Stop listening',
      stopSpeaking: 'Stop Sky speaking',
      languageLabel: 'Conversation language',
      sectionLabel: 'Conversation with Sky',
      microphone: 'Input microphone',
      defaultMicrophone: 'Default microphone',
      activeMicrophone: 'Currently capturing from',
      microphoneAriaLabel: 'Choose an input microphone',
      microphoneLevel: 'Microphone signal level',
      inputLabel: 'Type a question for Sky',
      quickPromptsLabel: 'Suggested questions',
      sendLabel: 'Send question',
    }
  }
  if (props.language === 'zh') {
    return {
      eyebrow: '免提对话',
      title: '和我聊聊吧',
      emptyTitle: '想找什么商品？',
      emptyBody: '可以自然说话或输入问题。我只使用已验证的门店信息。',
      user: '你',
      thinking: '正在查找答案',
      transcribing: '正在识别你的声音',
      errorTitle: '助手连接出现问题',
      placeholder: '例如：Coca Zero 多少钱？',
      hint: '按 Enter 发送',
      hintSecond: 'Shift + Enter 换行',
      retry: '重试',
      micStart: '开始说话',
      micStop: '停止录音',
      voiceStop: '停止聆听',
      stopSpeaking: '停止 Sky 朗读',
      languageLabel: '对话语言',
      sectionLabel: '与 Sky 对话',
      microphone: '输入麦克风',
      defaultMicrophone: '默认麦克风',
      activeMicrophone: '当前正在使用',
      microphoneAriaLabel: '选择输入麦克风',
      microphoneLevel: '麦克风信号强度',
      inputLabel: '输入问题给 Sky',
      quickPromptsLabel: '推荐问题',
      sendLabel: '发送问题',
    }
  }
  return {
    eyebrow: 'Hội thoại rảnh tay',
    title: 'Mình cùng nói chuyện nhé',
    emptyTitle: 'Bạn muốn tìm gì hôm nay?',
    emptyBody: 'Nói tự nhiên hoặc nhập câu hỏi. Mình chỉ dùng thông tin cửa hàng đã xác minh.',
    user: 'Bạn',
    thinking: 'Mình đang tìm câu trả lời',
    transcribing: 'Mình đang nghe lại câu nói của bạn',
    errorTitle: 'Chưa kết nối được trợ lý',
    placeholder: 'Ví dụ: Coca Zero giá bao nhiêu?',
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
    activeMicrophone: 'Đang thu từ',
    microphoneAriaLabel: 'Chọn micro đầu vào',
    microphoneLevel: 'Mức tín hiệu micro',
    inputLabel: 'Nhập câu hỏi cho Sky',
    quickPromptsLabel: 'Câu hỏi gợi ý',
    sendLabel: 'Gửi câu hỏi',
  }
})

const shoppingText = computed(() => {
  if (props.language === 'en') {
    return {
      cart: 'Demo cart',
      sandbox: 'SANDBOX',
      total: 'Total',
      checkout: 'Review checkout',
      confirm: 'Confirm demo order',
      cancel: 'Back to cart',
      order: 'Sandbox order',
      simulatePaid: 'Simulate payment success',
      simulateFailed: 'Simulate failure',
      add: 'Add',
      stock: 'available in demo',
      snapshotStock: 'recorded in snapshot · current stock unverified',
      referencePrice: 'Reference price',
      catalogDemo: 'Demo catalog',
      catalogWinmart: 'WinMart catalog',
      catalogSnapshot: 'WinMart dated snapshot',
      catalogUnknown: 'Catalog',
      end: 'End session',
      support: 'Call staff · demo',
      ticketLabel: 'Demo support request',
      ticketDemo: 'Demo only · no real staff was contacted',
    }
  }
  if (props.language === 'zh') {
    return {
      cart: '演示购物车', sandbox: '沙盒', total: '合计', checkout: '查看结账摘要',
      confirm: '确认演示订单', cancel: '返回购物车', order: '沙盒订单',
      simulatePaid: '模拟支付成功', simulateFailed: '模拟支付失败', add: '添加',
      stock: '演示库存', end: '结束会话',
      snapshotStock: '目录记录库存 · 当前库存未核实',
      referencePrice: '参考价',
      support: '联系店员 · 演示', ticketLabel: '演示服务请求',
      ticketDemo: '仅供演示 · 尚未联系真实店员',
      catalogDemo: '演示商品目录', catalogWinmart: 'WinMart 商品目录',
      catalogSnapshot: 'WinMart 历史快照',
      catalogUnknown: '商品目录',
    }
  }
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
    snapshotStock: 'ghi nhận trong snapshot · tồn hiện tại chưa xác minh',
    referencePrice: 'Giá tham khảo',
    catalogDemo: 'Catalog demo',
    catalogWinmart: 'Catalog WinMart',
    catalogSnapshot: 'Snapshot WinMart có ngày dữ liệu',
    catalogUnknown: 'Catalog',
    end: 'Kết thúc phiên',
    support: 'Gọi nhân viên · demo',
    ticketLabel: 'Yêu cầu hỗ trợ demo',
    ticketDemo: 'Chỉ là bản demo · chưa liên hệ nhân viên thật',
  }
})

const visibleCart = computed(() => props.checkoutQuote?.cart ?? props.cart ?? null)
const visibleOrder = computed(() => props.order ?? null)

function formatVnd(amount: number) {
  return new Intl.NumberFormat(props.language === 'en' ? 'en-US' : 'vi-VN', {
    maximumFractionDigits: 0,
  }).format(amount) + (props.language === 'en' ? ' VND' : 'đ')
}

function productProvenance(product: ProductCard) {
  const source = product.data_source === 'demo_product_catalog'
    ? shoppingText.value.catalogDemo
    : product.data_source === 'winmart_postgres_catalog'
      ? shoppingText.value.catalogWinmart
      : product.data_source === 'winmart_json_snapshot'
        ? shoppingText.value.catalogSnapshot
      : shoppingText.value.catalogUnknown
  const date = new Intl.DateTimeFormat(props.language === 'en' ? 'en-GB' : 'vi-VN', {
    day: '2-digit', month: '2-digit', year: 'numeric', timeZone: 'Asia/Ho_Chi_Minh',
  }).format(new Date(product.data_timestamp))
  return `${source} · ${date}`
}

function productStockLabel(product: ProductCard) {
  if (product.stock_is_current === false) return shoppingText.value.snapshotStock
  return `${product.stock} ${shoppingText.value.stock}`
}

function productPriceLabel(product: ProductCard) {
  const price = formatVnd(product.price_vnd)
  return product.stock_is_current === false
    ? `${shoppingText.value.referencePrice}: ${price}`
    : price
}

const quickPrompts = computed(() => {
  if (props.language === 'en') {
    return [
      { label: 'Coca Zero price', icon: Barcode },
      { label: 'Recommend a non-spicy snack', icon: Sparkle },
      { label: 'Where is milk?', icon: CaretRight },
    ]
  }
  if (props.language === 'zh') {
    return [
      { label: 'Coca Zero 价格', icon: Barcode },
      { label: '推荐不辣的零食', icon: Sparkle },
      { label: '牛奶在哪？', icon: CaretRight },
    ]
  }
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
</script>

<template>
  <section class="chat-panel" :aria-label="copy.sectionLabel">
    <header class="chat-header">
      <div class="chat-title-copy">
        <p class="panel-eyebrow">{{ copy.eyebrow }}</p>
        <h1>{{ copy.title }}</h1>
        <p class="chat-voice-hint" role="status" aria-live="polite">{{ voiceHint }}</p>
        <meter v-if="isVoiceActive" class="microphone-level" min="0" max="1" :value="microphoneLevel ?? 0" :aria-label="copy.microphoneLevel"></meter>
      </div>
      <div class="chat-header-tools">
        <div class="voice-mode-chip" :class="{ active: isHandsFree }">
          <Waveform :size="17" weight="duotone" aria-hidden="true" />
          <span>{{ isHandsFree ? 'AUTO' : 'MIC' }}</span>
        </div>
        <div class="language-picker" role="group" :aria-label="copy.languageLabel">
          <Translate :size="16" weight="duotone" aria-hidden="true" />
          <button
            v-for="option in languageOptions"
            :key="option.code"
            type="button"
            class="language-option"
            :class="{ selected: language === option.code }"
            :aria-pressed="language === option.code"
            :aria-label="option.label"
            @click="emit('change-language', option.code)"
          >
            {{ option.short }}
          </button>
        </div>
        <button type="button" class="end-session-button" @click="emit('end-session')">
          {{ shoppingText.end }}
        </button>
        <button type="button" class="support-request-button" @click="emit('request-support')">
          {{ shoppingText.support }}
        </button>
      </div>
      <label v-if="microphoneDevices?.length" class="microphone-picker">
        <span>{{ copy.microphone }}</span>
        <select :value="selectedMicrophone || ''" :disabled="['thinking', 'transcribing', 'speaking'].includes(status)" :aria-label="copy.microphoneAriaLabel" @change="emit('change-microphone', ($event.target as HTMLSelectElement).value)">
          <option value="">{{ activeMicrophone || copy.defaultMicrophone }}</option>
          <option v-for="device in microphoneDevices" :key="device.id" :value="device.id">{{ device.label }}</option>
        </select>
        <small v-if="activeMicrophone" class="active-microphone-label">{{ copy.activeMicrophone }}: {{ activeMicrophone }}</small>
      </label>
    </header>

    <div ref="conversation" class="conversation" aria-live="polite" aria-label="Nội dung hội thoại">
      <div v-if="messages.length === 0" class="conversation-empty">
        <div class="empty-icon"><Sparkle :size="22" weight="duotone" aria-hidden="true" /></div>
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
          <span class="message-author">{{ message.role === 'assistant' ? 'Sky' : copy.user }}</span>
          <span v-if="message.model" class="message-model">{{ message.model }}</span>
        </div>
        <p v-if="message.text" class="message-bubble">{{ message.text }}</p>
        <div v-else-if="message.role === 'assistant' && status === 'thinking'" class="message-bubble typing-bubble" role="status">
          <span>{{ copy.thinking }}</span>
          <span class="typing-dots" aria-hidden="true"><i></i><i></i><i></i></span>
        </div>
        <div v-if="message.supportTicket" class="support-ticket-card" role="status">
          <strong>{{ shoppingText.ticketLabel }}</strong>
          <span>{{ message.supportTicket.ticket_id }} · {{ message.supportTicket.status }}</span>
          <small>{{ message.supportTicket.demo ? shoppingText.ticketDemo : '' }}</small>
        </div>
        <section v-if="message.shoppingPlan" class="shopping-plan-card" :aria-label="language === 'en' ? 'Suggested shopping plan' : language === 'zh' ? '购物方案建议' : 'Phương án mua sắm gợi ý'">
          <article v-for="option in message.shoppingPlan.options" :key="option.option_id" class="shopping-plan-option">
            <header class="shopping-plan-option-header">
              <strong>{{ option.label }}</strong>
              <strong>{{ formatVnd(option.total_vnd) }}</strong>
            </header>
            <div v-for="item in option.items" :key="`${option.option_id}-${item.need_id}-${item.product_id}`" class="shopping-plan-item">
              <div class="shopping-plan-item-heading">
                <strong>{{ item.need_label }}</strong>
                <span>{{ item.source === 'EXPLICIT' ? (language === 'en' ? 'Requested' : language === 'zh' ? '已说明' : 'Bạn đã nêu') : (language === 'en' ? 'Optional idea' : language === 'zh' ? '可选建议' : 'Gợi ý thêm') }}</span>
              </div>
              <span>{{ item.name }} × {{ item.quantity }} · {{ formatVnd(item.line_total_vnd) }}</span>
              <small v-if="item.rationale">{{ item.rationale }}</small>
              <small>{{ item.stock_is_current ? (language === 'en' ? 'Stock checked by the active source' : language === 'zh' ? '已由当前数据源核实库存' : 'Tồn kho đã được nguồn dữ liệu hiện tại kiểm tra') : (language === 'en' ? 'Current stock is not verified' : language === 'zh' ? '当前库存尚未核实' : 'Tồn kho hiện tại chưa được xác minh') }}</small>
            </div>
            <p v-if="option.remaining_budget_vnd !== null" class="shopping-plan-remaining">
              {{ language === 'en' ? 'Remaining budget' : language === 'zh' ? '剩余预算' : 'Còn lại' }}: {{ formatVnd(option.remaining_budget_vnd) }}
            </p>
          </article>
          <p v-if="message.shoppingPlan.missing_required_needs.length" class="shopping-plan-note">
            {{ language === 'en' ? 'Could not verify' : language === 'zh' ? '未能核实' : 'Chưa xác minh được' }}: {{ message.shoppingPlan.missing_required_needs.join(', ') }}
          </p>
          <p v-if="message.shoppingPlan.optional_needs_not_selected.length" class="shopping-plan-note">
            {{ language === 'en' ? 'Optional needs not included' : language === 'zh' ? '未纳入的可选需求' : 'Chưa đưa vào phương án' }}: {{ message.shoppingPlan.optional_needs_not_selected.join(', ') }}
          </p>
          <p v-if="message.shoppingPlan.budget_shortfall_vnd" class="shopping-plan-note">
            {{ language === 'en' ? 'Minimum budget shortfall' : language === 'zh' ? '最低预算缺口' : 'Ngân sách tối thiểu còn thiếu' }}: {{ formatVnd(message.shoppingPlan.budget_shortfall_vnd) }}
          </p>
          <small v-if="!message.shoppingPlan.inventory_verified" class="shopping-plan-provenance">
            {{ language === 'en' ? 'Prices use the listed catalog snapshot; current store stock is not verified.' : language === 'zh' ? '价格来自目录快照；门店当前库存尚未核实。' : 'Giá lấy từ ảnh chụp catalog; tồn kho thực tế tại cửa hàng chưa được xác minh.' }}
          </small>
          <small v-if="!message.shoppingPlan.cart_mutated" class="shopping-plan-provenance">
            {{ language === 'en' ? 'This is a proposal and has not been added to your cart.' : language === 'zh' ? '这是购物建议，尚未加入购物车。' : 'Đây là phương án tư vấn, chưa được thêm vào giỏ hàng.' }}
          </small>
        </section>
        <div v-if="message.products?.length && !message.shoppingPlan" class="message-product-list">
          <article v-for="product in message.products" :key="product.product_id" class="message-product-card">
            <div class="message-product-copy">
              <strong>{{ product.name }}</strong>
              <span>{{ productPriceLabel(product) }} · {{ productStockLabel(product) }}</span>
              <small class="product-provenance">{{ productProvenance(product) }}</small>
            </div>
            <button v-if="product.data_source === 'demo_product_catalog'" type="button" class="small-action-button" @click="emit('add-product', product.product_id)">
              <ShoppingCartIcon :size="16" weight="duotone" aria-hidden="true" />
              {{ shoppingText.add }}
            </button>
          </article>
        </div>
      </article>

      <div v-if="status === 'transcribing' || (status === 'thinking' && messages[messages.length - 1]?.role !== 'assistant')" class="message-row message-assistant">
        <div class="message-meta"><span class="message-author">Sky</span></div>
        <div class="message-bubble typing-bubble" role="status">
          <span>{{ status === 'transcribing' ? copy.transcribing : copy.thinking }}</span>
          <span class="typing-dots" aria-hidden="true"><i></i><i></i><i></i></span>
        </div>
      </div>
    </div>

    <section
      v-if="(visibleCart?.items.length ?? 0) > 0 || visibleOrder || checkoutQuote"
      class="shopping-panel"
      aria-live="polite"
    >
      <header class="shopping-panel-header">
        <div class="shopping-panel-title">
          <ShoppingCartIcon :size="17" weight="duotone" aria-hidden="true" />
          <strong>{{ visibleOrder ? shoppingText.order : shoppingText.cart }}</strong>
        </div>
        <span class="sandbox-chip">{{ shoppingText.sandbox }}</span>
      </header>

      <div v-if="visibleCart && !visibleOrder" class="shopping-cart-lines">
        <div v-for="item in visibleCart.items" :key="item.product_id" class="shopping-cart-line">
          <div class="shopping-cart-line-copy">
            <strong>{{ item.name }}</strong>
            <span>{{ formatVnd(item.unit_price_vnd) }} × {{ item.quantity }} = {{ formatVnd(item.line_total_vnd) }}</span>
          </div>
          <div v-if="!checkoutQuote" class="quantity-actions">
            <button type="button" :aria-label="`Reduce ${item.name}`" @click="emit('set-cart-quantity', item.product_id, Math.max(0, item.quantity - 1))">
              <Minus :size="15" aria-hidden="true" />
            </button>
            <button type="button" :aria-label="`Add one ${item.name}`" @click="emit('set-cart-quantity', item.product_id, item.quantity + 1)">
              <Plus :size="15" aria-hidden="true" />
            </button>
            <button type="button" :aria-label="`Remove ${item.name}`" @click="emit('set-cart-quantity', item.product_id, 0)">
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
          <button type="button" class="small-action-button primary" @click="emit('confirm-checkout')">
            {{ shoppingText.confirm }}
          </button>
          <button type="button" class="small-action-button" @click="emit('cancel-checkout')">
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
          <button type="button" class="small-action-button primary" @click="emit('simulate-payment', 'PAID')">
            {{ shoppingText.simulatePaid }}
          </button>
          <button type="button" class="small-action-button" @click="emit('simulate-payment', 'FAILED')">
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
      <p class="sandbox-note">{{ language === 'en' ? 'Demo only. No real payment is made.' : language === 'zh' ? '仅供演示，不会进行真实付款。' : 'Chỉ dùng để demo, không phát sinh thanh toán thật.' }}</p>
    </section>

    <div v-if="errorMessage" class="chat-error" role="alert">
      <div>
        <strong>{{ copy.errorTitle }}</strong>
        <span>{{ errorMessage }}</span>
      </div>
      <button type="button" class="icon-button error-retry" :aria-label="copy.retry" @click="emit('retry')">
        <ArrowClockwise :size="18" weight="bold" aria-hidden="true" />
      </button>
    </div>

    <div v-if="messages.length === 0" class="quick-prompts" :aria-label="copy.quickPromptsLabel">
      <button
        v-for="prompt in quickPrompts"
        :key="prompt.label"
        type="button"
        class="quick-prompt"
        :data-quick-prompt="prompt.label"
        @click="emit('quick-prompt', prompt.label)"
      >
        <component :is="prompt.icon" :size="16" weight="duotone" aria-hidden="true" />
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
        @input="emit('update:draft', ($event.target as HTMLTextAreaElement).value)"
        @keydown="onKeydown"
      ></textarea>
      <div class="composer-actions">
        <button
          type="button"
          class="mic-button"
          :class="{ recording: isRecording }"
          :aria-label="isVoiceActive || isHandsFree ? copy.voiceStop : copy.micStart"
          :disabled="!isHandsFree && (status === 'transcribing' || status === 'thinking')"
          @click="emit('toggle-recording')"
        >
          <Stop v-if="isRecording || isVoiceActive" :size="21" weight="fill" aria-hidden="true" />
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
          :disabled="!draft.trim() || status === 'thinking' || status === 'transcribing'"
        >
          <ArrowUp :size="20" weight="bold" aria-hidden="true" />
        </button>
      </div>
    </form>
    <p class="composer-hint">{{ copy.hint }} <span>•</span> {{ copy.hintSecond }}</p>
  </section>
</template>

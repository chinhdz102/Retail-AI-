<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import {
  PhArrowClockwise as ArrowClockwise,
  PhArrowUp as ArrowUp,
  PhBarcode as Barcode,
  PhCaretRight as CaretRight,
  PhMicrophone as Microphone,
  PhPause as Pause,
  PhSparkle as Sparkle,
  PhStop as Stop,
  PhTranslate as Translate,
  PhWaveform as Waveform,
} from '@phosphor-icons/vue'

import type { AssistantStatus, ChatMessage, LanguageCode } from '../types'

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
    microphoneAriaLabel: 'Chọn micro đầu vào',
    microphoneLevel: 'Mức tín hiệu micro',
    inputLabel: 'Nhập câu hỏi cho Sky',
    quickPromptsLabel: 'Câu hỏi gợi ý',
    sendLabel: 'Gửi câu hỏi',
  }
})

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
      </div>
      <label v-if="microphoneDevices?.length" class="microphone-picker">
        <span>{{ copy.microphone }}</span>
        <select :value="selectedMicrophone || ''" :disabled="['thinking', 'transcribing', 'speaking'].includes(status)" :aria-label="copy.microphoneAriaLabel" @change="emit('change-microphone', ($event.target as HTMLSelectElement).value)">
          <option value="">{{ activeMicrophone || copy.defaultMicrophone }}</option>
          <option v-for="device in microphoneDevices" :key="device.id" :value="device.id">{{ device.label }}</option>
        </select>
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
        <p class="message-bubble">{{ message.text }}</p>
      </article>

      <div v-if="status === 'thinking' || status === 'transcribing'" class="message-row message-assistant">
        <div class="message-meta"><span class="message-author">Sky</span></div>
        <div class="message-bubble typing-bubble" role="status">
          <span>{{ status === 'transcribing' ? copy.transcribing : copy.thinking }}</span>
          <span class="typing-dots" aria-hidden="true"><i></i><i></i><i></i></span>
        </div>
      </div>
    </div>

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
          :disabled="status === 'transcribing' || status === 'thinking'"
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

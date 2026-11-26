import { mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import MaiVideoAvatar from './MaiVideoAvatar.vue'

describe('MaiVideoAvatar', () => {
  let playSpy: ReturnType<typeof vi.fn>
  let pauseSpy: ReturnType<typeof vi.fn>
  let loadSpy: ReturnType<typeof vi.fn>

  beforeEach(() => {
    playSpy = vi.fn().mockResolvedValue(undefined)
    pauseSpy = vi.fn()
    loadSpy = vi.fn()
    Object.defineProperty(HTMLMediaElement.prototype, 'play', { configurable: true, value: playSpy })
    Object.defineProperty(HTMLVideoElement.prototype, 'pause', { configurable: true, value: pauseSpy })
    Object.defineProperty(HTMLMediaElement.prototype, 'load', { configurable: true, value: loadSpy })
  })

  it('mounts 4 preloaded muted layers and crossfades without calling load()', async () => {
    const wrapper = mount(MaiVideoAvatar, { props: { state: 'idle' } })
    const videos = wrapper.findAll('video')
    expect(videos).toHaveLength(4)
    expect(wrapper.get('section[aria-label="Avatar Mai"]').attributes('data-active-clip')).toBe('idle')
    expect(wrapper.get('video[data-clip="idle"]').classes()).toContain('is-active')
    expect(loadSpy).not.toHaveBeenCalled()

    await wrapper.setProps({ state: 'listening' })
    expect(wrapper.get('video[data-clip="listening"]').classes()).toContain('is-active')
    expect(wrapper.get('video[data-clip="idle"]').classes()).not.toContain('is-active')

    await wrapper.setProps({ state: 'thinking' })
    expect(wrapper.get('video[data-clip="thinking"]').classes()).toContain('is-active')

    await wrapper.setProps({ state: 'speaking', audioPlaying: true })
    expect(wrapper.get('video[data-clip="speaking"]').classes()).toContain('is-active')
    expect(loadSpy).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('keeps Mai mouth closed while waiting for TTS audio before playback begins', async () => {
    const wrapper = mount(MaiVideoAvatar, { props: { state: 'speaking', audioPlaying: false } })
    expect(wrapper.get('section[aria-label="Avatar Mai"]').attributes('data-active-clip')).toBe('listening')
    expect(wrapper.get('video[data-clip="speaking"]').classes()).not.toContain('is-active')
    expect(wrapper.get('video[data-clip="listening"]').classes()).toContain('is-active')

    await wrapper.setProps({ state: 'speaking', audioPlaying: true })
    expect(wrapper.get('section[aria-label="Avatar Mai"]').attributes('data-active-clip')).toBe('speaking')
    expect(wrapper.get('video[data-clip="speaking"]').classes()).toContain('is-active')

    // Instant barge-in back to listening closes mouth smoothly.
    await wrapper.setProps({ state: 'listening', audioPlaying: false })
    expect(wrapper.get('section[aria-label="Avatar Mai"]').attributes('data-active-clip')).toBe('listening')
    expect(wrapper.get('video[data-clip="speaking"]').classes()).not.toContain('is-active')
    wrapper.unmount()
  })

  it('defaults to the natural full-face speaking clip with all 4 layers preloaded (no 2D mouth sticker overlay)', () => {
    const wrapper = mount(MaiVideoAvatar, { props: { state: 'speaking', audioPlaying: true } })
    const videos = wrapper.findAll('video')
    expect(videos).toHaveLength(4)
    for (const v of videos) {
      expect(v.attributes('preload')).toBe('auto')
    }
    expect(wrapper.get('video[data-clip="speaking"]').attributes('src')).toBe('/avatars/mai-clip-v4-04-speaking.mp4')
    expect(wrapper.find('canvas.sky-lipsync-canvas').classes()).not.toContain('is-active')
    wrapper.unmount()
  })

  it('renders lip-sync canvas layer when explicitly opted in and falls back to standalone speaking clip if track fails', async () => {
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockRejectedValueOnce(new Error('offline'))
    const wrapper = mount(MaiVideoAvatar, { props: { state: 'speaking', audioPlaying: true, lipSyncEnabled: true } })
    expect(wrapper.find('canvas.sky-lipsync-canvas').exists()).toBe(true)
    await new Promise((resolve) => setTimeout(resolve, 0))
    await wrapper.vm.$nextTick()
    expect(wrapper.get('video[data-clip="speaking"]').attributes('src')).toBe('/avatars/mai-clip-v4-04-speaking.mp4')
    expect(wrapper.find('canvas.sky-lipsync-canvas').classes()).not.toContain('is-active')
    fetchSpy.mockRestore()
    wrapper.unmount()
  })
})

import { mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'

import SkyAvatar from './SkyAvatar.vue'

describe('SkyAvatar animation control', () => {
  it('plays the GLB talk animation while Sky is speaking', async () => {
    const wrapper = mount(SkyAvatar, {
      props: { src: '/avatars/sky.glb', state: 'speaking' },
    })
    const viewer = wrapper.get('model-viewer').element as HTMLElement & {
      animationName?: string
      play?: () => void
    }
    viewer.play = vi.fn()
    viewer.dispatchEvent(new Event('load'))
    await wrapper.vm.$nextTick()

    expect(viewer.animationName).toBe('talk')
    expect(viewer.play).toHaveBeenCalled()
  })

  it('pauses the model animation when Sky returns to idle', async () => {
    const wrapper = mount(SkyAvatar, {
      props: { src: '/avatars/sky.glb', state: 'speaking' },
    })
    const viewer = wrapper.get('model-viewer').element as HTMLElement & {
      pause?: () => void
    }
    viewer.pause = vi.fn()

    await wrapper.setProps({ state: 'idle' })

    expect(viewer.pause).toHaveBeenCalled()
  })

  it('does not add a synthetic mouth overlay over the model', async () => {
    const wrapper = mount(SkyAvatar, {
      props: { src: '/avatars/sky.glb', state: 'speaking' },
    })

    expect(wrapper.find('.avatar-mouth').exists()).toBe(false)
  })
})

// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { nextTick } from 'vue'
import MediaPreview from '../src/components/MediaPreview.vue'

describe('media preview lifecycle', () => {
  const wrappers: ReturnType<typeof mount>[] = []
  function preview(type = 'video_clip') {
    const wrapper = mount(MediaPreview, { props: {
      artifact: { id: 'asset-1', type, metadata: { storage_key: 'media/file' } },
    } })
    wrappers.push(wrapper)
    return wrapper
  }
  beforeEach(() => vi.useFakeTimers())
  afterEach(() => {
    wrappers.forEach(wrapper => wrapper.unmount())
    wrappers.length = 0
    vi.useRealTimers()
  })

  it.each(['video_clip', 'audio_narration', 'reference_image'])('keeps slow %s mounted and accepts late completion', async type => {
    const wrapper = preview(type)
    await nextTick()
    const tag = type === 'video_clip' ? 'video' : type === 'audio_narration' ? 'audio' : 'img'
    const element = wrapper.get(tag).element
    await vi.advanceTimersByTimeAsync(12000)
    expect(wrapper.text()).toContain('文件读取较慢')
    expect(wrapper.get(tag).element).toBe(element)
    await wrapper.get(tag).trigger(tag === 'img' ? 'load' : 'loadeddata')
    if (tag === 'audio') await wrapper.get(tag).trigger('canplay')
    expect(wrapper.text()).not.toContain('文件读取较慢')
    expect(wrapper.attributes('aria-busy')).toBe('false')
  })

  it('rearms the slow-load timer after manual image retry', async () => {
    const wrapper = preview('reference_image')
    await nextTick()
    await wrapper.get('img').trigger('error')
    expect(wrapper.text()).toContain('文件加载失败')
    await wrapper.get('button').trigger('click')
    expect(wrapper.get('img').attributes('src')).toContain('preview_attempt=1')
    await vi.advanceTimersByTimeAsync(12000)
    expect(wrapper.text()).toContain('文件读取较慢')
  })

  it('resets the deadline when switching artifacts', async () => {
    const wrapper = preview('reference_image')
    await nextTick()
    await vi.advanceTimersByTimeAsync(11000)
    await wrapper.setProps({ artifact: { id: 'asset-2', type: 'reference_image', metadata: { storage_key: 'media/second' } } })
    expect(wrapper.get('img').attributes('src')).toContain('asset-2')
    await vi.advanceTimersByTimeAsync(1000)
    expect(wrapper.text()).not.toContain('文件读取较慢')
    await vi.advanceTimersByTimeAsync(11000)
    expect(wrapper.text()).toContain('文件读取较慢')
    await wrapper.get('img').trigger('load')
    expect(vi.getTimerCount()).toBe(0)
  })

  it('does not start a timer for a document', async () => {
    const wrapper = preview('subtitle_srt')
    await nextTick()
    expect(wrapper.text()).toContain('结构化文件')
    expect(vi.getTimerCount()).toBe(0)
  })

  it('does not alter signed fallback query parameters', async () => {
    const wrapper = preview()
    const signed = 'https://storage.example.test/file?signature=example'
    await wrapper.setProps({ artifact: { id: 'asset-1', type: 'video_clip', metadata: { storage_key: 'media/file' }, download_url: signed } })
    await wrapper.get('video').trigger('error')
    expect(wrapper.get('video').attributes('src')).toBe(signed)
    await wrapper.get('video').trigger('error')
    expect(wrapper.text()).toContain('文件加载失败')
  })

  it('cleans up the pending timer on unmount', async () => {
    const wrapper = preview()
    await nextTick()
    wrapper.unmount()
    wrappers.pop()
    expect(vi.getTimerCount()).toBe(0)
  })
})

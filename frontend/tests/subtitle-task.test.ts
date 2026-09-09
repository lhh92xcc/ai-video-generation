// @vitest-environment jsdom
import { describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import SubtitleTaskView from '../src/components/SubtitleTaskView.vue'
import { createSubtitleAlignmentTask } from '../src/api/subtitles'
import { getEpisodes, getNovelProjects } from '../src/api/novels'

vi.mock('../src/api/novels', () => ({
  getNovelProjects: vi.fn(async () => [{ id: 'project', title: 'Test' }]),
  getEpisodes: vi.fn(async () => [{ id: 'episode', episode_number: 1, outline: { title: 'Test' } }, { id: 'episode-2', episode_number: 2, outline: { title: 'Second' } }]),
}))
vi.mock('../src/api/tasks', () => ({ getArtifacts: vi.fn(async () => ({ items: [
  { id: 'audio', provider: 'local', metadata: { episode_id: 'episode', duration_seconds: 80 } },
  { id: 'other-audio', provider: 'local', metadata: { episode_id: 'episode-2', duration_seconds: 90 } },
  { id: 'unbound-audio', provider: 'legacy', metadata: { duration_seconds: 10 } },
] })) }))
vi.mock('../src/api/providerProfiles', () => ({ getProviderProfiles: vi.fn(async () => ({
  default_profile_id: 'mock', items: [{ profile_id: 'mock', configured: true, provider: 'mock', label: 'Mock' }],
})) }))
vi.mock('../src/api/subtitles', () => ({
  createSubtitleAlignmentTask: vi.fn(async () => ({ id: 'new-task' })),
  createSubtitleASRTask: vi.fn(),
}))

describe('subtitle input boundaries', () => {
  it.each(['success', 'failure'])('ignores a stale project response: %s', async result => {
    let resolveOld!: (value: Awaited<ReturnType<typeof getEpisodes>>) => void
    let rejectOld!: (error: Error) => void
    const oldRequest = new Promise<Awaited<ReturnType<typeof getEpisodes>>>((resolve, reject) => {
      resolveOld = resolve
      rejectOld = reject
    })
    const initialProjects = await getNovelProjects()
    vi.mocked(getNovelProjects).mockResolvedValueOnce([
      ...initialProjects, { ...initialProjects[0]!, id: 'new-project', title: 'New' },
    ])
    vi.mocked(getEpisodes).mockReturnValueOnce(oldRequest)
    const wrapper = mount(SubtitleTaskView)
    try {
      await flushPromises()
      await wrapper.findAll('select')[0]!.setValue('new-project')
      await flushPromises()
      expect(wrapper.findAll('select')[1]!.findAll('option')).toHaveLength(3)
      if (result === 'success') resolveOld([])
      else rejectOld(new Error('old project failed'))
      await flushPromises()
      expect(wrapper.findAll('select')[1]!.findAll('option')).toHaveLength(3)
      expect(wrapper.find('.inline-error').exists()).toBe(false)
      expect((wrapper.findAll('select')[0]!.element as HTMLSelectElement).value).toBe('new-project')
    } finally { wrapper.unmount() }
  })
  it('only selects audio bound to the target episode', async () => {
    const wrapper = mount(SubtitleTaskView)
    try {
      await flushPromises()
      const selects = wrapper.findAll('select')
      expect(selects[2]!.findAll('option').map(item => item.attributes('value'))).toEqual(['', 'audio'])
      await selects[1]!.setValue('episode-2')
      expect(selects[2]!.findAll('option').map(item => item.attributes('value'))).toEqual(['', 'other-audio'])
      expect((selects[2]!.element as HTMLSelectElement).value).toBe('other-audio')
    } finally { wrapper.unmount() }
  })
  it('warns about Mock and submits explicit duration instead of metadata', async () => {
    const wrapper = mount(SubtitleTaskView)
    try {
      await flushPromises()
      expect(wrapper.text()).toContain('不识别真实音频')
      await wrapper.findAll('button').find(button => button.text() === '句子级对齐')!.trigger('click')
      await wrapper.get('textarea').setValue('测试字幕。')
      const duration = wrapper.get('input[inputmode="decimal"]')
      const submit = wrapper.findAll('button').find(button => button.text() === '创建字幕任务')!
      await duration.setValue('Infinity')
      expect(submit.attributes('disabled')).toBeDefined()
      await duration.setValue('-1')
      expect(submit.attributes('disabled')).toBeDefined()
      await duration.setValue('45.9')
      await submit.trigger('click')
      await flushPromises()
      expect(createSubtitleAlignmentTask).toHaveBeenCalledWith('episode', expect.objectContaining({ audio_duration_seconds: 45.9 }), expect.any(String))
    } finally { wrapper.unmount() }
  })
})

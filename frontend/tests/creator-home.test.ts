// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import CreatorHome from '../src/components/CreatorHome.vue'

vi.mock('../src/api/novels', () => ({
  getNovelProjects: vi.fn(),
  createNovelProject: vi.fn(),
}))
vi.mock('../src/api/tasks', () => ({
  getTasks: vi.fn(),
  getArtifacts: vi.fn(),
}))

import { createNovelProject, getNovelProjects } from '../src/api/novels'
import { getArtifacts, getTasks } from '../src/api/tasks'

const project = (id: string, title: string) => ({
  id,
  title,
  target_episode_count: 1,
  target_episode_duration_seconds: 60,
  status: 'ready',
})

describe('creator home', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.mocked(getNovelProjects).mockResolvedValue([project('project-1', '雨夜来信')])
    vi.mocked(getTasks).mockResolvedValue({ items: [] })
    vi.mocked(getArtifacts).mockResolvedValue({ items: [] })
    vi.mocked(createNovelProject).mockResolvedValue(project('project-new', '新项目'))
  })

  afterEach(() => {
    vi.restoreAllMocks()
    vi.useRealTimers()
  })

  it('renders real dashboard data and opens the novel project form', async () => {
    const wrapper = mount(CreatorHome, { global: { stubs: ['CreatorProjectWorkspace', 'MediaPreview'] } })
    try {
      await flushPromises()
      expect(wrapper.text()).toContain('雨夜来信')
      expect(wrapper.text()).toContain('我的项目')
      expect(wrapper.text()).toContain('当前空闲')

      const createButton = wrapper.findAll('button').find((button) => button.text().includes('开始创建'))
      expect(createButton).toBeDefined()
      await createButton!.trigger('click')
      expect(wrapper.get('[role="dialog"]').text()).toContain('创建一个内容项目')
      expect(wrapper.get('input[placeholder="例如：雨夜来信"]')).toBeTruthy()
    } finally {
      wrapper.unmount()
    }
  })

  it('submits the selected duration and does not rely on a fixed GPU model', async () => {
    const wrapper = mount(CreatorHome, { global: { stubs: ['CreatorProjectWorkspace', 'MediaPreview'] } })
    try {
      await flushPromises()
      const createButton = wrapper.findAll('button').find((button) => button.text().includes('开始创建'))
      await createButton!.trigger('click')
      await wrapper.get('input[placeholder="例如：雨夜来信"]').setValue('镜中来客')
      await wrapper.get('input[type="checkbox"]').setValue(true)
      await wrapper.findAll('select')[0]!.setValue('1')
      await wrapper.findAll('select')[1]!.setValue('45')
      await wrapper.findAll('button').find((button) => button.text().includes('创建项目并继续'))!.trigger('click')
      await flushPromises()

      expect(createNovelProject).toHaveBeenCalledWith({
        title: '镜中来客',
        target_episode_count: 1,
        target_episode_duration_seconds: 45,
        rights_status: 'confirmed',
      })
      expect(wrapper.find('[data-test="creator-project-workspace"]').exists()).toBe(false)
      expect(wrapper.findComponent({ name: 'CreatorProjectWorkspace' }).exists()).toBe(true)
    } finally {
      wrapper.unmount()
    }
  })

  it('ignores an older dashboard response after the next refresh wins', async () => {
    let resolveOld!: (value: ReturnType<typeof project>[]) => void
    const oldResponse = new Promise<ReturnType<typeof project>[]>((resolve) => { resolveOld = resolve })
    vi.mocked(getNovelProjects).mockReturnValueOnce(oldResponse).mockResolvedValueOnce([project('project-2', '最新项目')])
    const wrapper = mount(CreatorHome, { global: { stubs: ['CreatorProjectWorkspace', 'MediaPreview'] } })
    try {
      await vi.advanceTimersByTimeAsync(8000)
      await flushPromises()
      expect(wrapper.text()).toContain('最新项目')
      resolveOld([project('project-old', '旧响应')])
      await flushPromises()
      expect(wrapper.text()).toContain('最新项目')
      expect(wrapper.text()).not.toContain('旧响应')
    } finally {
      wrapper.unmount()
    }
  })
})

// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import CreatorHome from '../src/components/CreatorHome.vue'

vi.mock('../src/api/novels', () => ({
  getNovelProjects: vi.fn(),
  createNovelProject: vi.fn(),
}))
vi.mock('../src/api/projects', () => ({
  getTopicProjects: vi.fn(),
  createTopicProject: vi.fn(),
  startTopicProduction: vi.fn(),
  getLatestTopicProductionRun: vi.fn(),
  getTopicProductionRun: vi.fn(),
}))
vi.mock('../src/api/tasks', () => ({
  getTasks: vi.fn(),
  getArtifacts: vi.fn(),
}))

import { createNovelProject, getNovelProjects } from '../src/api/novels'
import {
  getLatestTopicProductionRun,
  getTopicProductionRun,
  createTopicProject,
  getTopicProjects,
  startTopicProduction,
} from '../src/api/projects'
import { getArtifacts, getTasks } from '../src/api/tasks'

const project = (id: string, title: string) => ({
  id,
  title,
  target_episode_count: 1,
  target_episode_duration_seconds: 60,
  status: 'ready',
})

const topicProject = (id: string, title: string) => ({
  id,
  title,
  topic: '新手周末露营装备推荐',
  language: 'zh-CN',
  target_duration_seconds: 60,
  aspect_ratio: '9:16',
  tone: '清晰、实用',
  status: 'draft',
  created_at: '2026-09-09T00:00:00.000Z',
  updated_at: '2026-09-09T00:00:00.000Z',
})

const infoTask = (id: string, projectId: string, status: 'queued' | 'running' | 'succeeded' = 'queued') => ({
  id,
  project_id: projectId,
  kind: 'info_script',
  status,
  input_data: {},
  current_stage: status === 'succeeded' ? null : 'script',
  progress: status === 'succeeded' ? 100 : 10,
  error: null,
  stages: [],
  artifacts: [],
  created_at: '2026-09-09T00:00:00.000Z',
  updated_at: '2026-09-09T00:00:00.000Z',
})

const topicRun = (runId = 'topic-run-1', status: 'active' | 'completed' = 'active') => ({
  project_id: 'topic-project-1',
  run_id: runId,
  status,
  stage: status === 'completed' ? 'video_assembly' : 'info_script',
  task_ids: ['topic-task-1'],
  auto_advance: status === 'active',
  message: status === 'completed' ? '主题短视频已完成当前配置启用的全部媒体阶段。' : '主题短视频 Run 正在由 Worker 按依赖推进。',
})

describe('creator home', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.mocked(getNovelProjects).mockResolvedValue([project('project-1', '雨夜来信')])
    vi.mocked(getTopicProjects).mockResolvedValue({ items: [], total: 0 })
    vi.mocked(getTasks).mockResolvedValue({ items: [] })
    vi.mocked(getArtifacts).mockResolvedValue({ items: [] })
    vi.mocked(createNovelProject).mockResolvedValue(project('project-new', '新项目'))
    vi.mocked(createTopicProject).mockResolvedValue(topicProject('topic-project-1', '露营装备清单'))
    vi.mocked(startTopicProduction).mockResolvedValue(topicRun())
    vi.mocked(getTopicProductionRun).mockResolvedValue(topicRun())
    vi.mocked(getLatestTopicProductionRun).mockResolvedValue(topicRun())
    vi.mocked(getTasks).mockResolvedValue({ items: [] })
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
      expect(wrapper.get('[data-test="portfolio-guide"]').text()).toContain('先做一条可验证的本地样片')

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

  it('opens the portfolio shortcut with the 45-second target selected', async () => {
    const wrapper = mount(CreatorHome, { global: { stubs: ['CreatorProjectWorkspace', 'MediaPreview'] } })
    try {
      await flushPromises()
      await wrapper.get('[data-test="portfolio-guide"] button').trigger('click')
      const formRowSelects = wrapper.findAll('[role="dialog"] .creator-form-row select')
      expect((formRowSelects[1]!.element as HTMLSelectElement).value).toBe('45')
      expect(wrapper.get('[role="dialog"]').text()).toContain('45 秒 · 作品集')
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

  it('creates a topic project, starts the full production Run, and exposes the task center action', async () => {
    const wrapper = mount(CreatorHome, { global: { stubs: ['CreatorProjectWorkspace', 'MediaPreview'] } })
    try {
      await flushPromises()
      await wrapper.findAll('button').find((button) => button.text().includes('主题短视频'))!.trigger('click')
      await wrapper.get('input[placeholder="例如：新手露营装备怎么选"]').setValue('露营装备清单')
      await wrapper.get('textarea[placeholder*="第一次周末露营"]').setValue('面向第一次周末露营的人，讲清帐篷、睡袋和照明的选择顺序。')
      await wrapper.findAll('select')[0]!.setValue('45')
      await wrapper.findAll('select')[1]!.setValue('9:16')
      await wrapper.findAll('select')[2]!.setValue('轻松口语')
      await wrapper.findAll('button').find((button) => button.text().includes('创建并开始生产'))!.trigger('click')
      await flushPromises()

      expect(createTopicProject).toHaveBeenCalledWith({
        title: '露营装备清单',
        topic: '面向第一次周末露营的人，讲清帐篷、睡袋和照明的选择顺序。',
        language: 'zh-CN',
        target_duration_seconds: 45,
        aspect_ratio: '9:16',
        tone: '轻松口语',
      })
      expect(startTopicProduction).toHaveBeenCalledWith(
        'topic-project-1',
        {},
        'creator-topic-production-topic-project-1',
      )
      expect(wrapper.get('[data-test="topic-launch-card"]').text()).toContain('主题短视频正在后台生成')
      expect(wrapper.get('[data-test="topic-launch-card"]').text()).toContain('查看生产任务')
    } finally {
      wrapper.unmount()
    }
  })

  it('does not overlap topic task polls and ignores a response after dismissal', async () => {
    let resolveSlowPoll!: (run: ReturnType<typeof topicRun>) => void
    vi.mocked(getTopicProductionRun)
      .mockResolvedValueOnce(topicRun())
      .mockReturnValueOnce(new Promise((resolve) => { resolveSlowPoll = resolve }))

    const wrapper = mount(CreatorHome, { global: { stubs: ['CreatorProjectWorkspace', 'MediaPreview'] } })
    try {
      await flushPromises()
      await wrapper.findAll('button').find((button) => button.text().includes('主题短视频'))!.trigger('click')
      await wrapper.get('input[placeholder="例如：新手露营装备怎么选"]').setValue('露营装备清单')
      await wrapper.get('textarea[placeholder*="第一次周末露营"]').setValue('面向第一次周末露营的人，讲清帐篷、睡袋和照明的选择顺序。')
      await wrapper.findAll('button').find((button) => button.text().includes('创建并开始生产'))!.trigger('click')
      await flushPromises()

      await vi.advanceTimersByTimeAsync(4000)
      expect(getTopicProductionRun).toHaveBeenCalledTimes(2)

      await wrapper.get('button[aria-label="关闭主题任务提示"]').trigger('click')
      resolveSlowPoll(topicRun('topic-run-1', 'completed'))
      await flushPromises()
      expect(wrapper.find('[data-test="topic-launch-card"]').exists()).toBe(false)
    } finally {
      wrapper.unmount()
    }
  })
})

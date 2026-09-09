// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import ProductionQueue from '../src/components/ProductionQueue.vue'

const queueMocks = vi.hoisted(() => ({
  getOperationalHealth: vi.fn(async () => ({ status: 'ok', components: [], worker: {}, disk_free_gb: 100 })),
  getProductionQueue: vi.fn(async () => ({ counts: {}, tasks: [], gpu_lock_enabled: false, auto_runs: [
    { id: 'failed-run', status: 'failed', active_count: 0, failed_count: 1 },
    { id: 'canceled-run', status: 'canceled', active_count: 0 },
  ] })),
  cleanupTemporaryFiles: vi.fn(),
}))
const runControlMocks = vi.hoisted(() => ({
  pauseProductionRun: vi.fn(async () => ({})),
  resumeProductionRun: vi.fn(async () => ({})),
  cancelProductionRun: vi.fn(async () => ({})),
}))

vi.mock('../src/api/productionQueue', () => ({
  getOperationalHealth: queueMocks.getOperationalHealth,
  getProductionQueue: queueMocks.getProductionQueue,
  cleanupTemporaryFiles: queueMocks.cleanupTemporaryFiles,
}))
vi.mock('../src/api/tasks', () => ({ retryTask: vi.fn() }))
vi.mock('../src/api/novels', () => runControlMocks)

describe('production queue status wording', () => {
  afterEach(() => vi.useRealTimers())
  it('preserves terminal states and does not infer GPU usage or retry limits', async () => {
    vi.useFakeTimers()
    const wrapper = mount(ProductionQueue)
    try {
      await flushPromises()
      expect(wrapper.text()).toContain('失败 · 需处理')
      expect(wrapper.text()).toContain('已取消')
      expect(wrapper.text()).not.toContain('等待推进')
      expect(wrapper.text()).toContain('GPU 占用状态未监测')
      expect(wrapper.text()).not.toContain('GPU 当前空闲')
      expect(wrapper.text()).not.toContain('最多 2 次')
    } finally {
      wrapper.unmount()
    }
  })

  it('pauses, resumes and cancels a Run from the remote queue', async () => {
    vi.useFakeTimers()
    const activeRun = { id: 'run-1', project_id: 'project-1', status: 'active', task_count: 2, active_count: 2, succeeded_count: 0, failed_count: 0, progress: 10, episode_ids: [], updated_at: '2026-09-09T00:00:00Z' }
    const pausedRun = { ...activeRun, status: 'paused', active_count: 1 }
    const canceledRun = { ...activeRun, status: 'canceled', active_count: 0 }
    queueMocks.getProductionQueue
      .mockResolvedValueOnce({ counts: {}, tasks: [], gpu_lock_enabled: false, auto_runs: [activeRun] })
      .mockResolvedValueOnce({ counts: {}, tasks: [], gpu_lock_enabled: false, auto_runs: [pausedRun] })
      .mockResolvedValueOnce({ counts: {}, tasks: [], gpu_lock_enabled: false, auto_runs: [activeRun] })
      .mockResolvedValueOnce({ counts: {}, tasks: [], gpu_lock_enabled: false, auto_runs: [canceledRun] })
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    const wrapper = mount(ProductionQueue)
    try {
      await flushPromises()
      await wrapper.get('button[aria-label="暂停 Run run-1"]').trigger('click')
      await flushPromises()
      expect(runControlMocks.pauseProductionRun).toHaveBeenCalledWith('project-1', 'run-1')
      expect(wrapper.text()).toContain('已暂停')

      await wrapper.get('button[aria-label="恢复 Run run-1"]').trigger('click')
      await flushPromises()
      expect(runControlMocks.resumeProductionRun).toHaveBeenCalledWith('project-1', 'run-1')

      await wrapper.get('button[aria-label="取消 Run run-1"]').trigger('click')
      await flushPromises()
      expect(runControlMocks.cancelProductionRun).toHaveBeenCalledWith('project-1', 'run-1')
      expect(wrapper.text()).toContain('已取消')
    } finally {
      wrapper.unmount()
      vi.restoreAllMocks()
    }
  })
})

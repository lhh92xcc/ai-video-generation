// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import ProductionQueue from '../src/components/ProductionQueue.vue'

vi.mock('../src/api/productionQueue', () => ({
  getOperationalHealth: vi.fn(async () => ({ status: 'ok', components: [], worker: {}, disk_free_gb: 100 })),
  getProductionQueue: vi.fn(async () => ({ counts: {}, tasks: [], gpu_lock_enabled: false, auto_runs: [
    { id: 'failed-run', status: 'failed', active_count: 0, failed_count: 1 },
    { id: 'canceled-run', status: 'canceled', active_count: 0 },
  ] })),
  cleanupTemporaryFiles: vi.fn(),
}))
vi.mock('../src/api/tasks', () => ({ retryTask: vi.fn() }))

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
})

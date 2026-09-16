// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import Workbench from '../src/components/ScriptAssetWorkbench.vue'
function api(permission = true, fail = false) {
  let synced = false
  const mutations: string[] = []
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    const path = String(url)
    if (init?.method === 'POST') {
      mutations.push(path)
      if (fail) return new Response(JSON.stringify({ error: { code: 'STORY_BIBLE_REQUIRED', message: '请先生成故事设定' } }), { status: 409 })
      synced = true
    }
    let data: unknown = []
    if (path === '/api/v1/novel-projects') data = [{ id: 'p1', title: '同步验收', status: 'draft' }, { id: 'p2', title: '指定项目', status: 'draft' }]
    else if (path.endsWith('/access')) data = { permissions: permission ? ['asset:edit', 'asset:review'] : [], role: 'editor', actor: {} }
    else if (path.endsWith('/members') || path.includes('/audit-logs')) data = { items: [], total: 0 }
    else if ((path.endsWith('/assets') || path.endsWith('/assets/sync')) && synced) data = [{ id: 'a1', asset_key: 'key1', name: '铜色怀表', asset_type: 'prop', version: 1, status: 'draft', content: { description: '铜色怀表' }, aliases: [], source_chapter_numbers: [] }]
    return new Response(JSON.stringify(data), { status: 200 })
  }))
  return mutations
}
const syncButton = (w: ReturnType<typeof mount>) => w.findAll('button').find(b => b.text().includes('同步故事设定资产'))
afterEach(() => vi.unstubAllGlobals())
describe('workbench asset synchronization', () => {
  it('selects the requested project instead of the first project', async () => {
    const mutations = api(); const w = mount(Workbench, { props: { initialProjectId: 'p2' } })
    try {
      await flushPromises()
      expect((w.get('.workbench-toolbar select').element as HTMLSelectElement).value).toBe('p2')
      await syncButton(w)!.trigger('click'); await flushPromises()
      expect(mutations).toEqual(['/api/v1/novel-projects/p2/assets/sync'])
    } finally { w.unmount() }
  })

  it('populates assets without approving them', async () => {
    const mutations = api(); const w = mount(Workbench)
    try {
      await flushPromises(); expect(syncButton(w)).toBeDefined()
      await syncButton(w)!.trigger('click'); await flushPromises()
      expect(w.find('.asset-list').text()).toContain('铜色怀表')
      expect(w.find('.asset-status-pill').text()).toBe('草稿')
      expect(mutations).toEqual(['/api/v1/novel-projects/p1/assets/sync'])
    } finally { w.unmount() }
  })
  it('disables review controls while synchronization is pending', async () => {
    api(); const w = mount(Workbench)
    let finish!: (response: Response) => void
    try {
      await flushPromises(); await syncButton(w)!.trigger('click'); await flushPromises()
      vi.mocked(fetch).mockImplementationOnce(() => new Promise<Response>(resolve => { finish = resolve }))
      await syncButton(w)!.trigger('click'); await flushPromises()
      expect(w.get('.review-submit').attributes('disabled')).toBeDefined()
      expect(w.get('.review-form select').attributes('disabled')).toBeDefined()
    } finally {
      finish?.(new Response('[]')); await flushPromises(); w.unmount()
    }
  })
  it('hides sync without asset editing permission', async () => {
    api(false); const w = mount(Workbench)
    try { await flushPromises(); expect(syncButton(w)).toBeUndefined() } finally { w.unmount() }
  })
  it('shows prerequisite errors and allows retry', async () => {
    api(true, true); const w = mount(Workbench)
    try {
      await flushPromises(); expect(syncButton(w)).toBeDefined()
      await syncButton(w)!.trigger('click'); await flushPromises()
      expect(w.text()).toContain('请先生成故事设定')
      expect(syncButton(w)!.attributes('disabled')).toBeUndefined()
    } finally { w.unmount() }
  })
})

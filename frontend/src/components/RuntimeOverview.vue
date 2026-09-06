<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ApiClientError, getHealth, type HealthResponse } from '../api/client'
import { getNovelProjects } from '../api/novels'
import { getOperationalHealth } from '../api/productionQueue'
import { getArtifacts, getTasks } from '../api/tasks'
import type {
  ArtifactRecord,
  GenerationTaskRecord,
  NovelProjectRecord,
  OperationalHealthResponse,
  TaskStatus,
} from '../types/task'
import { formatTaskKind, formatStatus } from '../utils/taskStatus'

type OperatorView = 'overview' | 'provider' | 'subtitle' | 'tasks' | 'queue' | 'artifacts' | 'workbench'

const emit = defineEmits<{
  openView: [view: OperatorView]
  openCreator: []
}>()

const projects = ref<NovelProjectRecord[]>([])
const tasks = ref<GenerationTaskRecord[]>([])
const artifacts = ref<ArtifactRecord[]>([])
const health = ref<HealthResponse | null>(null)
const operationalHealth = ref<OperationalHealthResponse | null>(null)
const loading = ref(true)
const refreshing = ref(false)
const errorMessage = ref<string | null>(null)

const activeTaskCount = computed(() => tasks.value.filter((task) => ['created', 'queued', 'running'].includes(task.status)).length)
const failedTaskCount = computed(() => tasks.value.filter((task) => task.status === 'failed').length)
const mediaArtifactCount = computed(() => artifacts.value.filter((artifact) => [
  'rendered_video',
  'video_clip',
  'lip_synced_video',
  'reference_image',
].includes(artifact.type)).length)
const recentProjects = computed(() => projects.value.slice(0, 5))
const recentTasks = computed(() => [...tasks.value].sort((left, right) => right.updated_at.localeCompare(left.updated_at)).slice(0, 6))
const workerLabel = computed(() => {
  const status = operationalHealth.value?.worker?.status
  if (status === 'online') return 'Worker 在线'
  if (status === 'in_process') return '进程内执行'
  return '待检查'
})
const overallStatus = computed(() => operationalHealth.value?.status === 'ok' ? '运行正常' : operationalHealth.value ? '存在告警' : '未检查')

function formatTime(value: string) {
  const date = new Date(value)
  return Number.isNaN(date.getTime())
    ? value
    : date.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
}

function projectStatusLabel(status: string) {
  if (status === 'ready') return '可继续制作'
  if (status === 'failed') return '需要处理'
  return '草稿'
}

function taskStatusClass(status: TaskStatus) {
  return status
}

function displayError(error: unknown) {
  if (error instanceof ApiClientError) return `${error.message} · ${error.code}`
  return '运行概览暂时无法读取，请稍后重试。'
}

async function load(showLoading = false) {
  if (showLoading) loading.value = true
  else refreshing.value = true
  errorMessage.value = null

  const [healthResult, operationalResult, projectResult, taskResult, artifactResult] = await Promise.allSettled([
    getHealth(),
    getOperationalHealth(),
    getNovelProjects(),
    getTasks({ limit: 100 }),
    getArtifacts({ limit: 200, expiresInSeconds: 3600 }),
  ])

  if (healthResult.status === 'fulfilled') health.value = healthResult.value
  if (operationalResult.status === 'fulfilled') operationalHealth.value = operationalResult.value
  if (projectResult.status === 'fulfilled') projects.value = projectResult.value
  if (taskResult.status === 'fulfilled') tasks.value = taskResult.value.items
  if (artifactResult.status === 'fulfilled') artifacts.value = artifactResult.value.items

  const results = [healthResult, operationalResult, projectResult, taskResult, artifactResult]
  const failedRequests = results.filter((result): result is PromiseRejectedResult => result.status === 'rejected')
  if (failedRequests.length === results.length) errorMessage.value = displayError(failedRequests[0].reason)

  loading.value = false
  refreshing.value = false
}

onMounted(() => load(true))
</script>

<template>
  <section class="page-header runtime-overview-header">
    <div>
      <p class="page-kicker">WORKSPACE / OVERVIEW</p>
      <h1>运行概览</h1>
      <p>从这里查看项目、任务和媒体产物；Provider 只在系统配置中管理，不再作为默认首页。</p>
    </div>
    <div class="runtime-overview-actions">
      <button class="secondary-button" type="button" @click="emit('openCreator')">打开创作者前台 <span>↗</span></button>
      <button class="primary-button" type="button" :disabled="refreshing" @click="load()">{{ refreshing ? '刷新中…' : '刷新概览' }}</button>
    </div>
  </section>

  <div v-if="errorMessage" class="alert-card error-card">
    <div><strong>运行概览读取失败</strong><p>{{ errorMessage }}</p></div>
    <button class="secondary-button" type="button" @click="load(true)">重试</button>
  </div>

  <section class="summary-grid runtime-summary-grid" aria-label="运行概览摘要">
    <article class="summary-card">
      <div class="summary-icon purple"><span>▦</span></div>
      <div><span>小说项目</span><strong>{{ loading ? '—' : projects.length }}</strong><small>可从创作者前台继续制作</small></div>
      <span class="summary-state">内容</span>
    </article>
    <article class="summary-card">
      <div class="summary-icon blue"><span>↻</span></div>
      <div><span>进行中的任务</span><strong>{{ loading ? '—' : activeTaskCount }}</strong><small>{{ failedTaskCount }} 个失败待处理</small></div>
      <span class="summary-state" :class="{ good: activeTaskCount === 0 }">{{ activeTaskCount ? '运行中' : '空闲' }}</span>
    </article>
    <article class="summary-card">
      <div class="summary-icon green"><span>◉</span></div>
      <div><span>可预览媒体</span><strong>{{ loading ? '—' : mediaArtifactCount }}</strong><small>图片、视频和唇形片段</small></div>
      <span class="summary-state good">已登记</span>
    </article>
    <article class="summary-card">
      <div class="summary-icon amber"><span>!</span></div>
      <div><span>系统状态</span><strong>{{ overallStatus }}</strong><small>{{ workerLabel }}</small></div>
      <span class="summary-state" :class="{ good: overallStatus === '运行正常' }">{{ health ? '在线' : '—' }}</span>
    </article>
  </section>

  <section class="card runtime-quickstart-card">
    <div class="card-header">
      <div><h2>从这里开始</h2><p>按照内容生产顺序进入对应页面，避免在 Provider、任务和产物之间来回寻找。</p></div>
      <span class="guide-badge">操作入口</span>
    </div>
    <div class="runtime-quick-actions">
      <button type="button" class="runtime-quick-action featured" @click="emit('openCreator')"><span class="runtime-quick-icon">＋</span><span><strong>创建或继续视频</strong><small>小说 → 剧本 → 分镜 → 成片</small></span><b>→</b></button>
      <button type="button" class="runtime-quick-action" @click="emit('openView', 'tasks')"><span class="runtime-quick-icon blue">↻</span><span><strong>查看生产任务</strong><small>进度、失败原因和重试</small></span><b>→</b></button>
      <button type="button" class="runtime-quick-action" @click="emit('openView', 'artifacts')"><span class="runtime-quick-icon green">◉</span><span><strong>打开媒体资产</strong><small>预览图片、视频、音频和字幕</small></span><b>→</b></button>
      <button type="button" class="runtime-quick-action" @click="emit('openView', 'provider')"><span class="runtime-quick-icon amber">⚙</span><span><strong>管理 Provider</strong><small>配置只影响服务端任务</small></span><b>→</b></button>
    </div>
  </section>

  <section class="runtime-dashboard-grid">
    <article class="card runtime-projects-card">
      <div class="card-header">
        <div><h2>最近项目</h2><p>项目内容和制作进度在创作者工作区继续。</p></div>
        <button class="runtime-text-action" type="button" @click="emit('openCreator')">打开前台 <span>↗</span></button>
      </div>
      <div v-if="loading" class="runtime-list-empty"><span class="spinner" />正在读取项目…</div>
      <div v-else-if="!recentProjects.length" class="runtime-list-empty"><strong>还没有项目</strong><span>打开创作者前台创建第一个小说视频。</span><button class="creator-small-button" type="button" @click="emit('openCreator')">创建项目</button></div>
      <div v-else class="runtime-project-list">
        <div v-for="project in recentProjects" :key="project.id" class="runtime-project-row">
          <span class="runtime-project-mark">{{ project.title.slice(0, 1) }}</span>
          <div><strong>{{ project.title }}</strong><small>{{ project.target_episode_count }} 集 · 每集约 {{ project.target_episode_duration_seconds }} 秒 · 更新于 {{ formatTime(project.updated_at) }}</small></div>
          <span class="runtime-project-status" :class="project.status">{{ projectStatusLabel(project.status) }}</span>
        </div>
      </div>
    </article>

    <article class="card runtime-activity-card">
      <div class="card-header">
        <div><h2>最近活动</h2><p>任务完成后，产物会进入媒体资产库。</p></div>
        <button class="runtime-text-action" type="button" @click="emit('openView', 'tasks')">查看任务 <span>→</span></button>
      </div>
      <div v-if="loading" class="runtime-list-empty"><span class="spinner" />正在读取任务…</div>
      <div v-else-if="!recentTasks.length" class="runtime-list-empty"><strong>暂无任务记录</strong><span>从创作者前台开始创建视频后，这里会显示实时活动。</span></div>
      <div v-else class="runtime-activity-list">
        <div v-for="task in recentTasks" :key="task.id" class="runtime-activity-row">
          <span class="runtime-activity-mark" :class="taskStatusClass(task.status)">{{ task.status === 'succeeded' ? '✓' : task.status === 'failed' ? '!' : '↻' }}</span>
          <div><strong>{{ formatTaskKind(task.kind) }}</strong><small>{{ formatStatus(task.status) }} · {{ formatTime(task.updated_at) }}</small></div>
          <span class="runtime-task-status" :class="task.status">{{ formatStatus(task.status) }}</span>
        </div>
      </div>
    </article>
  </section>

  <section class="runtime-bottom-grid">
    <article class="card runtime-system-card">
      <div class="card-header"><div><h2>运行环境</h2><p>只展示当前服务状态，不展示密钥。</p></div><span class="status-pill" :class="{ neutral: overallStatus !== '运行正常' }">{{ overallStatus }}</span></div>
      <dl class="runtime-system-grid">
        <div><dt>API</dt><dd>{{ health ? `在线 · v${health.version}` : '待检查' }}</dd></div>
        <div><dt>Worker</dt><dd>{{ workerLabel }}</dd></div>
        <div><dt>GPU 锁</dt><dd>{{ operationalHealth?.gpu_lock_enabled ? (operationalHealth.gpu_lock_busy ? '占用中' : '已启用 · 空闲') : '未启用' }}</dd></div>
        <div><dt>可用磁盘</dt><dd>{{ operationalHealth ? `${operationalHealth.disk_free_gb} GB` : '待检查' }}</dd></div>
      </dl>
      <button class="runtime-outline-action" type="button" @click="emit('openView', 'queue')">打开远程生产队列 <span>→</span></button>
    </article>
    <article class="card runtime-boundary-card">
      <div class="runtime-boundary-icon">i</div>
      <div><h2>当前工作边界</h2><p>创作者前台负责内容生产和人工审核；制作后台负责任务、Provider、队列和 Artifact 运维。图片/视频预览统一通过授权内容接口，文件不可读时会显示真实原因，不再笼统提示“浏览器预览暂不可用”。</p></div>
    </article>
  </section>
</template>

<style scoped>
.runtime-overview-header { align-items: flex-end; }
.runtime-overview-actions { display: flex; align-items: center; gap: 9px; flex: 0 0 auto; }
.runtime-overview-actions button span { margin-left: 3px; }
.runtime-summary-grid { grid-template-columns: repeat(4, minmax(0, 1fr)); }
.summary-icon.amber { color: var(--amber); background: var(--amber-soft); }
.runtime-quickstart-card { margin-bottom: 18px; padding: 22px; }
.runtime-quick-actions { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 10px; }
.runtime-quick-action { display: flex; align-items: center; gap: 9px; min-width: 0; border: 1px solid var(--border-soft); border-radius: 9px; padding: 11px; color: var(--text); background: #fcfdff; text-align: left; transition: 150ms ease; }
.runtime-quick-action:hover, .runtime-quick-action.featured { border-color: #cfd1f2; background: #fafaff; }
.runtime-quick-action:hover { transform: translateY(-1px); box-shadow: 0 7px 15px rgba(34, 48, 73, .06); }
.runtime-quick-action > span:nth-child(2) { min-width: 0; flex: 1; }
.runtime-quick-action strong, .runtime-quick-action small { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.runtime-quick-action strong { color: var(--text-strong); font-size: 10px; }
.runtime-quick-action small { margin-top: 4px; color: var(--text-muted); font-size: 8px; }
.runtime-quick-action > b { color: #a1a8b8; font-size: 13px; font-weight: 500; }
.runtime-quick-icon { display: grid; place-items: center; flex: 0 0 28px; width: 28px; height: 28px; border-radius: 8px; color: #fff; background: var(--primary); font-size: 15px; font-weight: 700; }
.runtime-quick-icon.blue { color: var(--blue); background: #eaf3ff; }
.runtime-quick-icon.green { color: var(--green); background: var(--green-soft); }
.runtime-quick-icon.amber { color: var(--amber); background: var(--amber-soft); font-size: 13px; }
.runtime-dashboard-grid, .runtime-bottom-grid { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 18px; margin-bottom: 18px; }
.runtime-projects-card, .runtime-activity-card, .runtime-system-card { overflow: hidden; padding: 22px; }
.runtime-text-action, .runtime-outline-action { border: 1px solid #dfe1f3; border-radius: 7px; padding: 7px 9px; color: var(--primary); background: #fff; font-size: 9px; font-weight: 700; white-space: nowrap; }
.runtime-text-action:hover, .runtime-outline-action:hover { border-color: #afb2e8; background: #fafaff; }
.runtime-list-empty { display: flex; align-items: center; justify-content: center; flex-direction: column; gap: 6px; min-height: 192px; border-top: 1px solid var(--border-soft); color: var(--text-muted); font-size: 10px; text-align: center; }
.runtime-list-empty strong { color: var(--text); font-size: 12px; }
.runtime-list-empty .creator-small-button { margin-top: 5px; }
.runtime-project-list, .runtime-activity-list { border-top: 1px solid var(--border-soft); }
.runtime-project-row, .runtime-activity-row { display: flex; align-items: center; gap: 10px; min-width: 0; border-bottom: 1px solid var(--border-soft); padding: 12px 0; }
.runtime-project-row:last-child, .runtime-activity-row:last-child { border-bottom: 0; }
.runtime-project-mark { display: grid; place-items: center; flex: 0 0 29px; width: 29px; height: 29px; border-radius: 8px; color: #6063cd; background: #efefff; font-size: 11px; font-weight: 800; }
.runtime-project-row > div, .runtime-activity-row > div { min-width: 0; flex: 1; }
.runtime-project-row strong, .runtime-project-row small, .runtime-activity-row strong, .runtime-activity-row small { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.runtime-project-row strong, .runtime-activity-row strong { color: var(--text); font-size: 10px; }
.runtime-project-row small, .runtime-activity-row small { margin-top: 4px; color: var(--text-muted); font-size: 8px; }
.runtime-project-status, .runtime-task-status { flex: 0 0 auto; border-radius: 999px; padding: 5px 7px; color: var(--text-muted); background: #f0f2f6; font-size: 8px; font-weight: 700; white-space: nowrap; }
.runtime-project-status.ready, .runtime-task-status.succeeded { color: var(--green); background: var(--green-soft); }
.runtime-project-status.failed, .runtime-task-status.failed { color: var(--red); background: var(--red-soft); }
.runtime-activity-mark { display: grid; place-items: center; flex: 0 0 25px; width: 25px; height: 25px; border-radius: 7px; color: var(--primary); background: #eeeeff; font-size: 10px; font-weight: 800; }
.runtime-activity-mark.succeeded { color: var(--green); background: var(--green-soft); }
.runtime-activity-mark.failed { color: var(--red); background: var(--red-soft); }
.runtime-system-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 14px 18px; margin: 3px 0 19px; }
.runtime-system-grid div { min-width: 0; }
.runtime-system-grid dt { color: var(--text-muted); font-size: 9px; }
.runtime-system-grid dd { overflow: hidden; margin: 5px 0 0; color: var(--text); font-family: "SFMono-Regular", Consolas, monospace; font-size: 10px; text-overflow: ellipsis; white-space: nowrap; }
.runtime-outline-action { width: 100%; }
.runtime-boundary-card { display: flex; align-items: flex-start; gap: 11px; padding: 22px; border-color: #e5e5fa; background: linear-gradient(145deg, #fafaff, #fff 65%); }
.runtime-boundary-icon { display: grid; place-items: center; flex: 0 0 28px; width: 28px; height: 28px; border: 1px solid #cfd1f3; border-radius: 50%; color: var(--primary); background: #f0f1ff; font-size: 11px; font-weight: 800; }
.runtime-boundary-card h2 { margin: 2px 0 0; color: var(--text-strong); font-size: 14px; }
.runtime-boundary-card p { margin: 8px 0 0; color: var(--text-muted); font-size: 10px; line-height: 1.7; }
@media (max-width: 960px) {
  .runtime-summary-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .runtime-quick-actions { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
@media (max-width: 720px) {
  .runtime-overview-header { align-items: flex-start; flex-direction: column; }
  .runtime-overview-actions { width: 100%; }
  .runtime-overview-actions button { flex: 1; }
  .runtime-summary-grid, .runtime-quick-actions, .runtime-dashboard-grid, .runtime-bottom-grid { grid-template-columns: 1fr; }
  .runtime-project-status, .runtime-task-status { display: none; }
}
</style>

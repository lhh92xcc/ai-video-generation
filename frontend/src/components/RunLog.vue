<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { ApiClientError } from '../api/client'
import { getTasks } from '../api/tasks'
import type { GenerationTaskRecord, TaskStatus } from '../types/task'
import { friendlyErrorMessage, formatStatus, formatTaskKind, isActiveTask, taskErrorDetail } from '../utils/taskStatus'

type StatusFilter = 'all' | TaskStatus

const tasks = ref<GenerationTaskRecord[]>([])
const loading = ref(false)
const errorMessage = ref<string | null>(null)
const searchQuery = ref('')
const statusFilter = ref<StatusFilter>('all')
const kindFilter = ref('all')
const expandedTaskId = ref<string | null>(null)
const lastRefreshedAt = ref<string | null>(null)
let pollingTimer: number | undefined

const orderedTasks = computed(() => [...tasks.value].sort((left, right) => right.updated_at.localeCompare(left.updated_at)))
const kindOptions = computed(() => [...new Set(tasks.value.map((task) => task.kind))].sort((left, right) => formatTaskKind(left).localeCompare(formatTaskKind(right), 'zh-CN')))
const filteredTasks = computed(() => {
  const query = searchQuery.value.trim().toLowerCase()
  return orderedTasks.value.filter((task) => {
    if (statusFilter.value !== 'all' && task.status !== statusFilter.value) return false
    if (kindFilter.value !== 'all' && task.kind !== kindFilter.value) return false
    if (!query) return true
    return [
      formatTaskKind(task.kind),
      task.kind,
      task.id,
      task.project_id,
      task.current_stage ?? '',
      task.error?.code ?? '',
    ].join(' ').toLowerCase().includes(query)
  })
})
const activeCount = computed(() => tasks.value.filter(isActiveTask).length)
const succeededCount = computed(() => tasks.value.filter((task) => task.status === 'succeeded').length)
const failedCount = computed(() => tasks.value.filter((task) => task.status === 'failed').length)
const artifactCount = computed(() => tasks.value.reduce((total, task) => total + task.artifacts.length, 0))

function formatTime(value: string | null | undefined) {
  if (!value) return '—'
  const date = new Date(value)
  return Number.isNaN(date.getTime())
    ? value
    : date.toLocaleString('zh-CN', {
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit',
      })
}

function formatLastRefresh() {
  return lastRefreshedAt.value ? `更新于 ${formatTime(lastRefreshedAt.value)}` : '尚未更新'
}

function displayError(error: unknown) {
  if (error instanceof ApiClientError) return `${error.message} · ${error.code}`
  return '运行日志暂时无法读取，请检查 API 服务。'
}

function toggleTask(taskId: string) {
  expandedTaskId.value = expandedTaskId.value === taskId ? null : taskId
}

function statusClass(status: string) {
  return status === 'succeeded' ? 'succeeded' : status === 'failed' ? 'failed' : isActiveStatus(status) ? 'active' : 'neutral'
}

function isActiveStatus(status: string) {
  return ['created', 'queued', 'running'].includes(status)
}

function typeMark(kind: string) {
  if (kind.includes('subtitle')) return '字'
  if (kind.includes('audio') || kind === 'lip_sync') return '声'
  if (kind.includes('image') || kind.includes('asset')) return '图'
  if (kind.includes('video')) return 'V'
  return '·'
}

function progressValue(task: GenerationTaskRecord) {
  return Math.min(100, Math.max(0, Number(task.progress) || 0))
}

function stageStatus(stage: Record<string, unknown>) {
  return String(stage.status ?? 'unknown')
}

function stageAttempt(stage: Record<string, unknown>) {
  const attempt = Number(stage.attempt)
  return Number.isFinite(attempt) && attempt > 0 ? `第 ${attempt} 次` : '—'
}

function stageError(stage: Record<string, unknown>) {
  return typeof stage.error_code === 'string' && stage.error_code ? stage.error_code : ''
}

function safeContext(task: GenerationTaskRecord) {
  const input = task.input_data
  const entries: Array<{ label: string; value: string }> = []
  const values: Array<[string, string, string]> = [
    ['集数', 'episode_id', ''],
    ['镜头', 'shot_index', ''],
    ['视觉档案', 'visual_quality_profile_id', ''],
    ['图片档案', 'image_provider_profile_id', ''],
    ['视频档案', 'video_provider_profile_id', ''],
    ['字幕模式', 'subtitle_mode', ''],
    ['自动 Run', 'auto_run_id', ''],
  ]
  for (const [label, key] of values) {
    const raw = input[key]
    if (raw !== undefined && raw !== null && String(raw).trim()) entries.push({ label, value: String(raw) })
  }
  return entries
}

function artifactModel(artifact: GenerationTaskRecord['artifacts'][number]) {
  const model = artifact.metadata.model
  return model === undefined || model === null || model === '' ? '' : String(model)
}

async function refresh(showLoading = false) {
  if (showLoading) loading.value = true
  errorMessage.value = null
  try {
    const response = await getTasks({ limit: 200 })
    tasks.value = response.items
    lastRefreshedAt.value = new Date().toISOString()
  } catch (error) {
    errorMessage.value = displayError(error)
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  refresh(true)
  pollingTimer = window.setInterval(() => {
    if (activeCount.value > 0) refresh()
  }, 5000)
})

onUnmounted(() => {
  if (pollingTimer) window.clearInterval(pollingTimer)
})
</script>

<template>
  <section class="page-header">
    <div>
      <p class="page-kicker">OPERATIONS / RUN LOG</p>
      <h1>运行日志</h1>
      <p>按时间回看每个生成任务的状态、阶段尝试和产物来源；日志只展示安全摘要，不暴露 Prompt、密钥或存储路径。</p>
    </div>
    <div class="run-log-header-actions">
      <span class="run-log-refreshed">{{ formatLastRefresh() }}</span>
      <button class="primary-button" type="button" :disabled="loading" @click="refresh(true)">{{ loading ? '刷新中…' : '刷新日志' }}</button>
    </div>
  </section>

  <div v-if="errorMessage" class="alert-card error-card">
    <div><strong>运行日志读取失败</strong><p>{{ errorMessage }}</p></div>
    <button class="secondary-button" type="button" @click="refresh(true)">重试</button>
  </div>

  <section class="summary-grid run-log-summary" aria-label="运行日志摘要">
    <article class="summary-card"><div class="summary-icon blue"><span>◎</span></div><div><span>任务总数</span><strong>{{ tasks.length }}</strong><small>当前保留范围</small></div><span class="summary-state">记录</span></article>
    <article class="summary-card"><div class="summary-icon purple"><span>↻</span></div><div><span>执行中</span><strong>{{ activeCount }}</strong><small>状态会自动刷新</small></div><span class="summary-state" :class="{ good: activeCount === 0 }">{{ activeCount ? '进行中' : '空闲' }}</span></article>
    <article class="summary-card"><div class="summary-icon green"><span>✓</span></div><div><span>成功 / 失败</span><strong>{{ succeededCount }} <em>/ {{ failedCount }}</em></strong><small>{{ artifactCount }} 个产物已登记</small></div><span class="summary-state" :class="{ good: failedCount === 0 }">{{ failedCount ? '需关注' : '稳定' }}</span></article>
  </section>

  <section class="card run-log-card">
    <div class="run-log-toolbar">
      <div><h2>任务时间线</h2><p>点击任意任务查看阶段状态、可恢复上下文和 Artifact 摘要。</p></div>
      <div class="run-log-filters">
        <input v-model="searchQuery" type="search" placeholder="搜索任务、项目或错误码" aria-label="搜索运行日志" />
        <select v-model="statusFilter" aria-label="按状态筛选">
          <option value="all">全部状态</option>
          <option value="created">已创建</option>
          <option value="queued">排队中</option>
          <option value="running">处理中</option>
          <option value="succeeded">已完成</option>
          <option value="failed">失败</option>
          <option value="canceled">已取消</option>
        </select>
        <select v-model="kindFilter" aria-label="按任务类型筛选">
          <option value="all">全部类型</option>
          <option v-for="kind in kindOptions" :key="kind" :value="kind">{{ formatTaskKind(kind) }}</option>
        </select>
      </div>
    </div>

    <div v-if="loading && tasks.length === 0" class="run-log-empty"><span class="spinner" />正在读取运行日志…</div>
    <div v-else-if="filteredTasks.length === 0" class="run-log-empty"><strong>{{ tasks.length ? '没有匹配的运行记录' : '暂无运行记录' }}</strong><span>{{ tasks.length ? '尝试清空搜索或切换筛选条件。' : '创建小说、配音、字幕或视频任务后，运行过程会出现在这里。' }}</span></div>
    <div v-else class="run-log-timeline">
      <article v-for="task in filteredTasks" :key="task.id" class="run-log-entry" :class="statusClass(task.status)">
        <span class="run-log-marker" aria-hidden="true"><i /></span>
        <div class="run-log-entry-content">
          <button class="run-log-entry-toggle" type="button" :aria-expanded="expandedTaskId === task.id" @click="toggleTask(task.id)">
            <span class="run-log-type-mark" :class="statusClass(task.status)">{{ typeMark(task.kind) }}</span>
            <span class="run-log-entry-heading"><strong>{{ formatTaskKind(task.kind) }}</strong><small>{{ task.id }} · {{ formatTime(task.updated_at) }}</small></span>
            <span class="run-log-entry-progress"><i><b :style="{ width: `${progressValue(task)}%` }" /></i><small>{{ progressValue(task) }}%</small></span>
            <span class="task-status" :class="task.status"><i />{{ formatStatus(task.status) }}</span>
            <span class="run-log-chevron">{{ expandedTaskId === task.id ? '⌃' : '⌄' }}</span>
          </button>

          <div v-if="expandedTaskId === task.id" class="run-log-detail">
            <div class="run-log-detail-top">
              <div><span>当前阶段</span><strong>{{ task.current_stage || '—' }}</strong></div>
              <div><span>创建时间</span><strong>{{ formatTime(task.created_at) }}</strong></div>
              <div><span>产物</span><strong>{{ task.artifacts.length }} 个 Artifact</strong></div>
            </div>

            <div v-if="task.error" class="run-log-error">
              <div><strong>{{ friendlyErrorMessage(task.error) }}</strong><small>{{ taskErrorDetail(task.error) }}</small></div>
              <span>失败任务可在“生产任务”中按原任务重试</span>
            </div>

            <div v-if="task.stages.length" class="run-log-section">
              <div class="run-log-section-heading"><strong>阶段尝试</strong><span>{{ task.stages.length }} 个状态节点</span></div>
              <div class="run-log-stage-list">
                <div v-for="(stage, index) in task.stages" :key="`${task.id}-${index}`" class="run-log-stage-row">
                  <span class="run-log-stage-dot" :class="statusClass(stageStatus(stage))"><i /></span>
                  <span class="run-log-stage-name">{{ String(stage.stage ?? 'unknown') }}</span>
                  <span class="run-log-stage-status" :class="statusClass(stageStatus(stage))">{{ formatStatus(stageStatus(stage)) }}</span>
                  <small>{{ stageAttempt(stage) }}<template v-if="stageError(stage)"> · {{ stageError(stage) }}</template></small>
                </div>
              </div>
            </div>

            <div v-if="safeContext(task).length" class="run-log-section">
              <div class="run-log-section-heading"><strong>安全运行上下文</strong><span>来自任务快照</span></div>
              <dl class="run-log-context-grid"><div v-for="entry in safeContext(task)" :key="entry.label"><dt>{{ entry.label }}</dt><dd>{{ entry.value }}</dd></div></dl>
            </div>

            <div v-if="task.artifacts.length" class="run-log-section">
              <div class="run-log-section-heading"><strong>Artifact 摘要</strong><span>可在媒体资产中预览</span></div>
              <div class="run-log-artifact-list">
                <div v-for="artifact in task.artifacts" :key="artifact.id" class="run-log-artifact-row">
                  <span class="run-log-artifact-icon">◇</span>
                  <div><strong>{{ artifact.type }}</strong><small>{{ artifact.provider }}<template v-if="artifactModel(artifact)"> · {{ artifactModel(artifact) }}</template></small></div>
                  <span class="run-log-artifact-ready">已登记</span>
                </div>
              </div>
            </div>
          </div>
        </div>
      </article>
    </div>
  </section>
</template>

<style scoped>
.run-log-header-actions { display: flex; align-items: center; gap: 13px; }
.run-log-refreshed { color: var(--text-muted); font-size: 10px; white-space: nowrap; }
.run-log-summary { margin-bottom: 18px; }
.run-log-card { overflow: hidden; }
.run-log-toolbar { display: flex; align-items: flex-start; justify-content: space-between; gap: 18px; padding: 21px 22px 17px; }
.run-log-toolbar h2 { margin: 0; color: var(--text-strong); font-size: 16px; font-weight: 650; }
.run-log-toolbar p { margin: 6px 0 0; color: var(--text-muted); font-size: 11px; line-height: 1.5; }
.run-log-filters { display: flex; flex-wrap: wrap; justify-content: flex-end; gap: 7px; }
.run-log-filters input, .run-log-filters select { height: 32px; border: 1px solid #d9deea; border-radius: 7px; padding: 0 9px; color: var(--text); background: #fff; font-size: 10px; outline: 0; }
.run-log-filters input { width: 190px; }
.run-log-filters input:focus, .run-log-filters select:focus { border-color: var(--primary); box-shadow: 0 0 0 3px rgba(91, 92, 226, .1); }
.run-log-empty { display: flex; align-items: center; justify-content: center; flex-direction: column; gap: 7px; min-height: 220px; border-top: 1px solid var(--border-soft); color: var(--text-muted); font-size: 11px; text-align: center; }
.run-log-empty strong { color: var(--text); font-size: 12px; }
.run-log-timeline { position: relative; border-top: 1px solid var(--border-soft); padding: 20px 22px 22px 40px; }
.run-log-timeline::before { content: ''; position: absolute; top: 30px; bottom: 31px; left: 26px; width: 1px; background: #e4e7f0; }
.run-log-entry { position: relative; display: flex; gap: 15px; padding: 0 0 15px; }
.run-log-entry:last-child { padding-bottom: 0; }
.run-log-marker { position: absolute; z-index: 1; top: 15px; left: -20px; display: grid; place-items: center; width: 12px; height: 12px; border: 3px solid #fff; border-radius: 50%; background: #aeb6c8; box-shadow: 0 0 0 1px #d8dce6; }
.run-log-marker i { width: 4px; height: 4px; border-radius: 50%; background: currentColor; }
.run-log-entry.active .run-log-marker { color: var(--primary); background: #dcdcff; box-shadow: 0 0 0 1px #bfc1ef; }
.run-log-entry.succeeded .run-log-marker { color: var(--green); background: #dff5e9; box-shadow: 0 0 0 1px #b8e5cc; }
.run-log-entry.failed .run-log-marker { color: var(--red); background: #ffe3e7; box-shadow: 0 0 0 1px #f0bfc7; }
.run-log-entry-content { min-width: 0; flex: 1; border: 1px solid var(--border-soft); border-radius: 10px; background: #fff; transition: border-color 150ms ease, box-shadow 150ms ease; }
.run-log-entry:hover .run-log-entry-content, .run-log-entry-content:has(.run-log-detail) { border-color: #d6d8f4; box-shadow: 0 5px 16px rgba(56, 62, 115, .05); }
.run-log-entry-toggle { display: grid; grid-template-columns: 30px minmax(140px, 1fr) minmax(90px, .5fr) 78px 16px; align-items: center; gap: 12px; width: 100%; border: 0; border-radius: 9px; padding: 12px 13px; color: var(--text); background: #fff; text-align: left; }
.run-log-entry-toggle:hover { background: #fcfcff; }
.run-log-type-mark { display: grid; place-items: center; width: 28px; height: 28px; border-radius: 8px; color: var(--primary); background: #f0f1ff; font-size: 11px; font-weight: 750; }
.run-log-type-mark.active { color: var(--primary); background: #f0f1ff; }
.run-log-type-mark.succeeded { color: var(--green); background: var(--green-soft); }
.run-log-type-mark.failed { color: var(--red); background: var(--red-soft); }
.run-log-entry-heading, .run-log-entry-heading strong, .run-log-entry-heading small { display: block; min-width: 0; }
.run-log-entry-heading strong { overflow: hidden; color: var(--text); font-size: 11px; font-weight: 650; text-overflow: ellipsis; white-space: nowrap; }
.run-log-entry-heading small { overflow: hidden; margin-top: 4px; color: var(--text-muted); font-family: "SFMono-Regular", Consolas, monospace; font-size: 8px; text-overflow: ellipsis; white-space: nowrap; }
.run-log-entry-progress { display: flex; align-items: center; gap: 6px; min-width: 0; }
.run-log-entry-progress i { display: block; overflow: hidden; flex: 1; height: 5px; border-radius: 99px; background: #edf0f6; }
.run-log-entry-progress b { display: block; height: 100%; border-radius: inherit; background: var(--primary); }
.run-log-entry-progress small { color: var(--text-muted); font-size: 8px; }
.run-log-chevron { color: #aeb6c5; font-size: 13px; }
.run-log-detail { border-top: 1px solid var(--border-soft); padding: 15px 16px 17px; background: #fcfdff; }
.run-log-detail-top { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; }
.run-log-detail-top div, .run-log-context-grid div { min-width: 0; }
.run-log-detail-top span, .run-log-context-grid dt { display: block; color: var(--text-muted); font-size: 9px; }
.run-log-detail-top strong { display: block; overflow: hidden; margin-top: 5px; color: var(--text); font-family: "SFMono-Regular", Consolas, monospace; font-size: 9px; text-overflow: ellipsis; white-space: nowrap; }
.run-log-error { display: flex; align-items: center; justify-content: space-between; gap: 13px; margin-top: 14px; border: 1px solid #f0d1d6; border-radius: 8px; padding: 10px 11px; color: var(--red); background: #fff5f6; }
.run-log-error strong, .run-log-error small { display: block; }
.run-log-error strong { font-size: 10px; }
.run-log-error small { margin-top: 4px; color: #a96d75; font-size: 8px; }
.run-log-error > span { flex: 0 0 auto; color: #a96d75; font-size: 8px; }
.run-log-section { margin-top: 16px; }
.run-log-section-heading { display: flex; align-items: center; justify-content: space-between; gap: 10px; margin-bottom: 8px; }
.run-log-section-heading strong { color: var(--text); font-size: 10px; }
.run-log-section-heading span { color: var(--text-muted); font-size: 8px; }
.run-log-stage-list, .run-log-artifact-list { display: grid; gap: 5px; }
.run-log-stage-row { display: grid; grid-template-columns: 16px minmax(100px, 1fr) 60px minmax(60px, auto); align-items: center; gap: 8px; border: 1px solid #edf0f5; border-radius: 7px; padding: 7px 8px; background: #fff; }
.run-log-stage-dot { display: grid; place-items: center; width: 13px; height: 13px; border-radius: 50%; color: #aab2c0; background: #eef1f5; }
.run-log-stage-dot i { width: 4px; height: 4px; border-radius: 50%; background: currentColor; }
.run-log-stage-dot.active { color: var(--primary); background: #e7e7ff; }
.run-log-stage-dot.succeeded { color: var(--green); background: #e2f5ea; }
.run-log-stage-dot.failed { color: var(--red); background: #ffe7ea; }
.run-log-stage-name { overflow: hidden; color: var(--text); font-family: "SFMono-Regular", Consolas, monospace; font-size: 9px; text-overflow: ellipsis; white-space: nowrap; }
.run-log-stage-status { font-size: 8px; }
.run-log-stage-status.active { color: var(--primary); }
.run-log-stage-status.succeeded { color: var(--green); }
.run-log-stage-status.failed { color: var(--red); }
.run-log-stage-row small { color: var(--text-muted); font-size: 8px; text-align: right; }
.run-log-context-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 9px 14px; margin: 0; border: 1px solid #edf0f5; border-radius: 8px; padding: 10px; background: #fff; }
.run-log-context-grid dd { overflow: hidden; margin: 4px 0 0; color: var(--text); font-family: "SFMono-Regular", Consolas, monospace; font-size: 8px; text-overflow: ellipsis; white-space: nowrap; }
.run-log-artifact-row { display: flex; align-items: center; gap: 8px; border: 1px solid #e3f0e7; border-radius: 7px; padding: 7px 8px; background: #f9fdf9; }
.run-log-artifact-icon { display: grid; place-items: center; flex: 0 0 21px; width: 21px; height: 21px; border-radius: 6px; color: var(--green); background: #e4f6eb; font-size: 10px; }
.run-log-artifact-row div { min-width: 0; flex: 1; }
.run-log-artifact-row strong, .run-log-artifact-row small { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.run-log-artifact-row strong { color: #3b8060; font-size: 9px; }
.run-log-artifact-row small { margin-top: 3px; color: #8da998; font-size: 8px; }
.run-log-artifact-ready { flex: 0 0 auto; color: var(--green); font-size: 8px; font-weight: 700; }
@media (max-width: 900px) {
  .run-log-toolbar { flex-direction: column; }
  .run-log-filters { justify-content: flex-start; width: 100%; }
  .run-log-filters input { flex: 1; width: auto; }
}
@media (max-width: 720px) {
  .run-log-header-actions { align-items: flex-start; flex-direction: column; }
  .run-log-entry-toggle { grid-template-columns: 30px minmax(0, 1fr) 16px; gap: 9px; }
  .run-log-entry-progress, .run-log-entry-toggle > .task-status { display: none; }
  .run-log-detail-top, .run-log-context-grid { grid-template-columns: 1fr 1fr; }
  .run-log-error { align-items: flex-start; flex-direction: column; }
  .run-log-stage-row { grid-template-columns: 16px minmax(0, 1fr) 54px; }
  .run-log-stage-row small { display: none; }
}
</style>

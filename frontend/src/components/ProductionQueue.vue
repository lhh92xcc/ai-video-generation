<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { ApiClientError } from '../api/client'
import { cleanupTemporaryFiles, getOperationalHealth, getProductionQueue } from '../api/productionQueue'
import { retryTask } from '../api/tasks'
import type {
  GenerationTaskRecord,
  OperationalComponentHealth,
  OperationalComponentStatus,
  OperationalHealthResponse,
  ProductionQueueRun,
  ProductionQueueSnapshot,
  TaskStatus,
} from '../types/task'

const health = ref<OperationalHealthResponse | null>(null)
const snapshot = ref<ProductionQueueSnapshot | null>(null)
const loading = ref(true)
const refreshing = ref(false)
const cleaning = ref(false)
const retryingId = ref<string | null>(null)
const errorMessage = ref<string | null>(null)
const noticeMessage = ref<string | null>(null)
let timer: number | undefined

const counts = computed(() => snapshot.value?.counts ?? {})
const recentTasks = computed(() => snapshot.value?.tasks.slice(0, 30) ?? [])
const activeCount = computed(() => (counts.value.queued ?? 0) + (counts.value.created ?? 0) + (counts.value.running ?? 0))
const failedCount = computed(() => counts.value.failed ?? 0)

const componentLabels: Record<string, string> = {
  redis: 'Redis 队列',
  ollama: 'Ollama',
  comfyui: 'ComfyUI',
  musetalk: 'MuseTalk',
}
const componentStatusLabels: Record<OperationalComponentStatus, string> = {
  ok: '正常', degraded: '降级', unavailable: '不可用', not_configured: '未启用',
}
const statusLabels: Record<TaskStatus, string> = {
  created: '已创建', queued: '排队中', running: '处理中', succeeded: '已完成', failed: '失败', canceled: '已取消',
}
const kindLabels: Record<string, string> = {
  novel_story_bible: '故事设定', novel_episode_plan: '分集大纲', novel_episode_script: '分场剧本', novel_shot_list: '结构化分镜',
  asset_reference_image: '参考图', audio_narration: '配音', audio_bgm: 'BGM', subtitle_align: '字幕对齐', subtitle_asr: 'ASR 字幕',
  video_clip: '视频片段', video_assembly: '最终成片', lip_sync: '唇形同步', info_script: '信息脚本',
}

function formatStatus(status: string) { return statusLabels[status as TaskStatus] ?? status }
function formatKind(kind: string) { return kindLabels[kind] ?? kind }
function formatComponent(component: OperationalComponentHealth) { return componentLabels[component.name] ?? component.name }
function formatComponentStatus(status: OperationalComponentStatus) { return componentStatusLabels[status] ?? status }
function formatRunStatus(run: ProductionQueueRun) {
  if (run.status === 'completed') return '已完成'
  if (run.status === 'blocked') return '需处理'
  return run.active_count ? '自动运行中' : '等待推进'
}
function formatTime(value: string | undefined) {
  if (!value) return '—'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
}
function formatBytes(value: number) {
  if (value < 1024 * 1024) return `${Math.round(value / 1024)} KB`
  return `${(value / 1024 / 1024).toFixed(1)} MB`
}
function displayError(error: unknown) {
  if (error instanceof ApiClientError) return `${error.message} · ${error.code}`
  return '远程队列暂时无法读取，请检查 API 和 Worker。'
}

async function refresh(showLoading = false) {
  if (showLoading) loading.value = true
  else refreshing.value = true
  errorMessage.value = null
  try {
    const [healthResponse, queueResponse] = await Promise.all([getOperationalHealth(), getProductionQueue({ limit: 100 })])
    health.value = healthResponse
    snapshot.value = queueResponse
  } catch (error) {
    errorMessage.value = displayError(error)
  } finally {
    loading.value = false
    refreshing.value = false
  }
}

async function cleanup() {
  cleaning.value = true
  errorMessage.value = null
  noticeMessage.value = null
  try {
    const result = await cleanupTemporaryFiles()
    noticeMessage.value = result.low_disk
      ? `已清理 ${result.deleted_files} 个临时文件，释放 ${formatBytes(result.deleted_bytes)}；磁盘仍低于阈值。`
      : `已清理 ${result.deleted_files} 个临时文件，释放 ${formatBytes(result.deleted_bytes)}。`
    await refresh()
  } catch (error) {
    errorMessage.value = displayError(error)
  } finally {
    cleaning.value = false
  }
}

async function retry(task: GenerationTaskRecord) {
  retryingId.value = task.id
  try {
    await retryTask(task.id)
    noticeMessage.value = '失败任务已重新入队。'
    await refresh()
  } catch (error) {
    errorMessage.value = displayError(error)
  } finally {
    retryingId.value = null
  }
}

onMounted(() => {
  refresh(true)
  timer = window.setInterval(() => refresh(), 5000)
})
onUnmounted(() => { if (timer) window.clearInterval(timer) })
</script>

<template>
  <section class="page-header">
    <div>
      <p class="page-kicker">OPERATIONS / REMOTE PRODUCTION QUEUE</p>
      <h1>远程生产队列</h1>
      <p>Windows 4060 Ti 生产机的状态、GPU 锁、自动 DAG 和失败恢复都在这里集中查看。</p>
    </div>
    <div class="queue-header-actions">
      <button class="secondary-button" type="button" :disabled="cleaning" @click="cleanup">{{ cleaning ? '清理中…' : '清理临时文件' }}</button>
      <button class="primary-button" type="button" :disabled="refreshing" @click="refresh()">{{ refreshing ? '刷新中…' : '健康检查' }}</button>
    </div>
  </section>

  <div v-if="errorMessage" class="alert-card error-card"><div><strong>队列读取失败</strong><p>{{ errorMessage }}</p></div><button class="secondary-button" type="button" @click="refresh(true)">重试</button></div>
  <div v-if="noticeMessage" class="alert-card success-card"><div><strong>操作完成</strong><p>{{ noticeMessage }}</p></div><button class="alert-dismiss" type="button" @click="noticeMessage = null">关闭</button></div>

  <section v-if="loading && !snapshot" class="queue-loading"><span class="spinner" />正在执行健康检查并读取队列…</section>
  <template v-else>
    <section class="summary-grid queue-summary-grid" aria-label="生产队列摘要">
      <article class="summary-card"><div class="summary-icon blue"><span>◎</span></div><div><span>待处理任务</span><strong>{{ activeCount }}</strong><small>{{ counts.queued ?? 0 }} 排队 · {{ counts.running ?? 0 }} 运行</small></div><span class="summary-state" :class="{ good: activeCount === 0 }">{{ activeCount ? '进行中' : '空闲' }}</span></article>
      <article class="summary-card"><div class="summary-icon purple"><span>↻</span></div><div><span>自动生产 Run</span><strong>{{ snapshot?.auto_runs.length ?? 0 }}</strong><small>{{ snapshot?.profile || 'default' }}</small></div><span class="summary-state">DAG</span></article>
      <article class="summary-card"><div class="summary-icon green"><span>✓</span></div><div><span>可用磁盘</span><strong>{{ health?.disk_free_gb ?? '—' }} <em>GB</em></strong><small>临时目录可安全清理</small></div><span class="summary-state" :class="{ good: (health?.disk_free_gb ?? 0) >= 10 }">{{ (health?.disk_free_gb ?? 0) >= 10 ? '充足' : '偏低' }}</span></article>
    </section>

    <section class="queue-top-grid">
      <article class="card queue-health-card">
        <div class="card-header"><div><h2>运行时健康</h2><p>每 5 秒自动刷新；状态来自 API/Worker 实时探针。</p></div><span class="status-pill" :class="{ neutral: health?.status !== 'ok' }">{{ health?.status === 'ok' ? '全部正常' : '存在告警' }}</span></div>
        <div class="component-health-list">
          <div v-for="component in health?.components ?? []" :key="component.name" class="component-health-row">
            <span class="health-mark" :class="component.status"><i /></span><div><strong>{{ formatComponent(component) }}</strong><small>{{ component.message }}</small></div><span class="component-status" :class="component.status">{{ formatComponentStatus(component.status) }}</span>
          </div>
        </div>
        <div class="queue-runtime-meta"><span>档案 <strong>{{ snapshot?.profile || health?.profile || '—' }}</strong></span><span>检查于 {{ formatTime(health?.checked_at) }}</span></div>
      </article>

      <article class="card queue-worker-card">
        <div class="card-header"><div><h2>Worker 与 GPU 锁</h2><p>单 GPU 任务串行执行，避免 8GB 显存同时加载多个模型。</p></div><span class="status-pill" :class="{ neutral: health?.worker?.status !== 'online' }">{{ health?.worker?.status === 'online' ? 'Worker 在线' : (health?.worker?.status === 'in_process' ? '进程内' : '待检查') }}</span></div>
        <div class="worker-lock-panel"><div class="worker-lock-icon" :class="{ busy: snapshot?.gpu_lock_busy }">{{ snapshot?.gpu_lock_busy ? '锁' : '闲' }}</div><div><strong>{{ snapshot?.gpu_lock_busy ? 'GPU 正在执行任务' : 'GPU 当前空闲' }}</strong><small>{{ snapshot?.gpu_lock_enabled ? 'Redis Lease Lock 已启用' : '当前未启用 GPU 锁' }}</small></div></div>
        <dl class="queue-meta-grid"><div><dt>当前任务</dt><dd>{{ String(health?.worker?.current_task_id || '—') }}</dd></div><div><dt>队列长度</dt><dd>{{ String(health?.worker?.pending_count ?? counts.queued ?? 0) }}</dd></div><div><dt>处理中</dt><dd>{{ String(health?.worker?.processing_count ?? counts.running ?? 0) }}</dd></div><div><dt>自动重试</dt><dd>最多 2 次</dd></div></dl>
      </article>
    </section>

    <section class="card queue-runs-card">
      <div class="task-toolbar"><div><h2>自动生产 DAG</h2><p>失败任务会先按退避策略自动恢复；需要人工处理的 Run 会显示为“需处理”。</p></div><span class="table-count">{{ snapshot?.auto_runs.length ?? 0 }} 个 Run</span></div>
      <div v-if="!snapshot?.auto_runs.length" class="queue-empty">还没有自动生产 Run。可在创作者前台的分集生产计划中开启自动推进。</div>
      <div v-else class="queue-run-list"><article v-for="run in snapshot.auto_runs" :key="run.id" class="queue-run-row"><div class="queue-run-copy"><strong>{{ run.id }}</strong><small>{{ run.succeeded_count }}/{{ run.task_count }} 完成 · {{ run.failed_count }} 失败 · 更新于 {{ formatTime(run.updated_at) }}</small></div><div class="queue-run-progress"><i><b :style="{ width: `${run.progress}%` }" /></i><small>{{ run.progress }}%</small></div><span class="task-status" :class="run.status"><i />{{ formatRunStatus(run) }}</span></article></div>
    </section>

    <section class="card queue-task-card">
      <div class="task-toolbar"><div><h2>最近任务</h2><p>失败任务可以在这里单独重新入队；成功 Artifact 不会被清理。</p></div><span class="table-count">{{ failedCount }} 个失败</span></div>
      <div v-if="!recentTasks.length" class="queue-empty">暂无任务记录。</div>
      <div v-else class="queue-task-list"><article v-for="task in recentTasks" :key="task.id" class="queue-task-row"><span class="task-type-mark" :class="task.status">{{ task.kind.includes('audio') ? '声' : task.kind.includes('video') ? 'V' : '·' }}</span><div class="queue-task-copy"><strong>{{ formatKind(task.kind) }}</strong><small>{{ task.id }} · {{ formatTime(task.updated_at) }}</small></div><div class="queue-task-progress"><i><b :style="{ width: `${task.progress}%` }" /></i><small>{{ task.progress }}%</small></div><span class="task-status" :class="task.status"><i />{{ formatStatus(task.status) }}</span><button v-if="task.status === 'failed'" class="table-action" type="button" :disabled="retryingId === task.id" @click="retry(task)">{{ retryingId === task.id ? '重试中…' : '重新入队' }}</button></article></div>
    </section>
  </template>
</template>

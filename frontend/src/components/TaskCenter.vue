<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { ApiClientError } from '../api/client'
import { createTaskBatch, getTaskBatches, getTasks, resumeTaskBatch, retryTask } from '../api/tasks'
import type { GenerationTaskRecord, TaskBatchRecord, TaskStatus } from '../types/task'

const tasks = ref<GenerationTaskRecord[]>([])
const loading = ref(false)
const errorMessage = ref<string | null>(null)
const statusFilter = ref<'all' | TaskStatus>('all')
const expandedTaskId = ref<string | null>(null)
const retryingTaskId = ref<string | null>(null)
const selectedTaskIds = ref<string[]>([])
const batchLabel = ref('生产批次')
const batches = ref<TaskBatchRecord[]>([])
const batchLoading = ref(false)
const batchActionId = ref<string | null>(null)
let pollingTimer: number | undefined

const filteredTasks = computed(() => tasks.value)
const activeCount = computed(() => tasks.value.filter((task) => ['queued', 'running', 'created'].includes(task.status)).length)
const selectedTasks = computed(() => tasks.value.filter((task) => selectedTaskIds.value.includes(task.id)))
const selectedProjectId = computed(() => {
  const projectIds = [...new Set(selectedTasks.value.map((task) => task.project_id))]
  return projectIds.length === 1 ? projectIds[0] : null
})
const batchCanCreate = computed(() => Boolean(selectedProjectId.value && selectedTasks.value.length > 0 && batchLabel.value.trim()))

const statusLabels: Record<TaskStatus, string> = {
  created: '已创建', queued: '排队中', running: '处理中', succeeded: '已完成', failed: '失败', canceled: '已取消',
}
const kindLabels: Record<string, string> = {
  subtitle_asr: 'ASR 字幕', subtitle_align: '字幕对齐', subtitle_srt: '人工字幕', audio_narration: '旁白音频', audio_bgm: 'BGM 音频',
  video_clip: '视频片段', video_assembly: '视频合成', info_script: '信息短视频脚本',
}

function formatStatus(status: TaskStatus) { return statusLabels[status] ?? status }
function formatKind(kind: string) { return kindLabels[kind] ?? kind }
function formatTime(value: string) {
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
}
function displayError(error: unknown) {
  if (error instanceof ApiClientError) return `${error.message} · ${error.code}`
  return '任务中心暂时无法读取，请稍后重试。'
}

const batchStatusLabels: Record<TaskBatchRecord['status'], string> = {
  created: '待运行', running: '运行中', succeeded: '已完成', partial: '部分完成', failed: '待恢复',
}
function formatBatchStatus(status: TaskBatchRecord['status']) { return batchStatusLabels[status] ?? status }

async function refresh() {
  loading.value = true
  errorMessage.value = null
  try {
    const response = await getTasks({ status: statusFilter.value === 'all' ? undefined : statusFilter.value })
    tasks.value = response.items
    selectedTaskIds.value = selectedTaskIds.value.filter((id) => tasks.value.some((task) => task.id === id))
    if (selectedProjectId.value) await refreshBatches(selectedProjectId.value)
  } catch (error) {
    errorMessage.value = displayError(error)
  } finally {
    loading.value = false
  }
}

async function refreshBatches(projectId: string) {
  batchLoading.value = true
  try {
    batches.value = (await getTaskBatches(projectId)).items
  } catch (error) {
    errorMessage.value = displayError(error)
  } finally {
    batchLoading.value = false
  }
}

function toggleSelected(taskId: string) {
  selectedTaskIds.value = selectedTaskIds.value.includes(taskId)
    ? selectedTaskIds.value.filter((id) => id !== taskId)
    : [...selectedTaskIds.value, taskId]
}

async function createBatch() {
  if (!batchCanCreate.value || !selectedProjectId.value) return
  batchActionId.value = 'create'
  try {
    const key = `task-batch-${selectedProjectId.value}-${selectedTaskIds.value.length}-${batchLabel.value.trim()}`.slice(0, 190)
    await createTaskBatch({ project_id: selectedProjectId.value, task_ids: selectedTaskIds.value, label: batchLabel.value.trim() }, key)
    selectedTaskIds.value = []
    await refreshBatches(selectedProjectId.value)
  } catch (error) {
    errorMessage.value = displayError(error)
  } finally {
    batchActionId.value = null
  }
}

async function resumeBatch(batch: TaskBatchRecord) {
  batchActionId.value = batch.id
  try {
    await resumeTaskBatch(batch.id)
    await refresh()
  } catch (error) {
    errorMessage.value = displayError(error)
  } finally {
    batchActionId.value = null
  }
}

async function retry(task: GenerationTaskRecord) {
  retryingTaskId.value = task.id
  try {
    const updated = await retryTask(task.id)
    tasks.value = tasks.value.map((item) => item.id === updated.id ? updated : item)
  } catch (error) {
    errorMessage.value = displayError(error)
  } finally {
    retryingTaskId.value = null
  }
}

function toggle(taskId: string) {
  expandedTaskId.value = expandedTaskId.value === taskId ? null : taskId
}

onMounted(() => {
  refresh()
  pollingTimer = window.setInterval(() => {
    if (activeCount.value > 0) refresh()
  }, 4000)
})
onUnmounted(() => { if (pollingTimer) window.clearInterval(pollingTimer) })
</script>

<template>
  <section class="page-header">
    <div>
      <p class="page-kicker">WORKSPACE / TASK CENTER</p>
      <h1>生产任务</h1>
      <p>集中查看脚本、音频、字幕和视频任务。进行中的任务每 4 秒自动刷新。</p>
    </div>
    <button class="primary-button" type="button" @click="refresh">刷新任务</button>
  </section>

  <section class="summary-grid task-summary-grid" aria-label="任务摘要">
    <article class="summary-card"><div class="summary-icon blue"><span>◎</span></div><div><span>全部任务</span><strong>{{ tasks.length }}</strong><small>当前查询范围</small></div><span class="summary-state">任务</span></article>
    <article class="summary-card"><div class="summary-icon purple"><span>↻</span></div><div><span>进行中</span><strong>{{ activeCount }}</strong><small>自动轮询状态</small></div><span class="summary-state" :class="{ good: activeCount === 0 }">{{ activeCount === 0 ? '空闲' : '运行中' }}</span></article>
    <article class="summary-card"><div class="summary-icon green"><span>✓</span></div><div><span>已完成</span><strong>{{ tasks.filter((task) => task.status === 'succeeded').length }}</strong><small>可查看 Artifact</small></div><span class="summary-state good">就绪</span></article>
  </section>

  <section class="card batch-card">
    <div class="task-toolbar">
      <div><h2>批次与断点续跑</h2><p>选择同一项目的任务建立批次；失败任务可重新入队，已完成产物会保留。</p></div>
      <span class="table-count">{{ selectedTasks.length }} 个已选择</span>
    </div>
    <div class="batch-create-row">
      <input v-model="batchLabel" class="batch-label-input" maxlength="120" placeholder="批次名称" />
      <button class="primary-button" type="button" :disabled="!batchCanCreate || batchActionId === 'create'" @click="createBatch">{{ batchActionId === 'create' ? '创建中…' : '创建批次' }}</button>
      <span v-if="selectedTasks.length && !selectedProjectId" class="field-hint">请选择同一项目的任务</span>
    </div>
    <div v-if="batchLoading" class="loading-line"><span class="spinner" />正在读取批次…</div>
    <div v-else-if="selectedProjectId && batches.length" class="batch-list">
      <article v-for="batch in batches" :key="batch.id" class="batch-row">
        <div><strong>{{ batch.label }}</strong><small>{{ batch.succeeded_count }}/{{ batch.total_count }} 完成 · {{ batch.failed_count }} 个失败</small></div>
        <span class="task-status" :class="batch.status"><i />{{ formatBatchStatus(batch.status) }}</span>
        <button v-if="['failed', 'partial'].includes(batch.status)" class="secondary-button" type="button" :disabled="batchActionId === batch.id" @click="resumeBatch(batch)">{{ batchActionId === batch.id ? '恢复中…' : '恢复失败任务' }}</button>
      </article>
    </div>
    <div v-else class="batch-empty">选择任务后可查看该项目的历史批次。</div>
  </section>

  <section class="card task-center-card">
    <div class="task-toolbar"><div><h2>任务列表</h2><p>任务状态来自服务端持久化记录，刷新页面后仍可继续追踪。</p></div><select v-model="statusFilter" @change="refresh"><option value="all">全部状态</option><option value="queued">排队中</option><option value="running">处理中</option><option value="succeeded">已完成</option><option value="failed">失败</option></select></div>
    <div v-if="errorMessage" class="alert-card error-card task-error"><div><strong>任务读取失败</strong><p>{{ errorMessage }}</p></div><button class="secondary-button" type="button" @click="refresh">重试</button></div>
    <div v-else-if="loading && tasks.length === 0" class="task-empty"><span class="spinner" />正在读取任务…</div>
    <div v-else-if="filteredTasks.length === 0" class="task-empty"><strong>暂无任务记录</strong><span>创建字幕、音频或视频任务后，结果会出现在这里。</span></div>
    <div v-else class="task-list">
      <article v-for="task in filteredTasks" :key="task.id" class="task-row" :class="{ expanded: expandedTaskId === task.id }">
        <label class="task-select" :aria-label="`选择${formatKind(task.kind)}`"><input type="checkbox" :checked="selectedTaskIds.includes(task.id)" @change="toggleSelected(task.id)" @click.stop /></label>
        <button class="task-row-main" type="button" @click="toggle(task.id)">
          <span class="task-type-mark" :class="task.status">{{ task.kind.includes('subtitle') ? '字' : task.kind.includes('audio') ? '声' : 'V' }}</span>
          <span class="task-main-copy"><strong>{{ formatKind(task.kind) }}</strong><small>{{ task.id }}</small></span>
          <span class="task-progress"><i><b :style="{ width: `${task.progress}%` }" /></i><small>{{ task.progress }}%</small></span>
          <span class="task-status" :class="task.status"><i />{{ formatStatus(task.status) }}</span>
          <span class="task-time">{{ formatTime(task.updated_at) }}</span>
          <span class="task-chevron">{{ expandedTaskId === task.id ? '⌃' : '⌄' }}</span>
        </button>
        <div v-if="expandedTaskId === task.id" class="task-detail">
          <div class="task-detail-grid"><div><dt>Project ID</dt><dd>{{ task.project_id }}</dd></div><div><dt>当前阶段</dt><dd>{{ task.current_stage || '—' }}</dd></div><div><dt>创建时间</dt><dd>{{ formatTime(task.created_at) }}</dd></div><div><dt>Artifact</dt><dd>{{ task.artifacts.length }} 个产物</dd></div></div>
          <div v-if="task.error" class="task-failure"><strong>{{ task.error.code }}</strong><span>{{ task.error.message }}</span><button class="secondary-button" type="button" :disabled="retryingTaskId === task.id" @click.stop="retry(task)">{{ retryingTaskId === task.id ? '重试中…' : '重试任务' }}</button></div>
          <div v-if="task.artifacts.length" class="task-artifacts"><span>产物</span><div v-for="artifact in task.artifacts" :key="artifact.id" class="artifact-chip"><strong>{{ artifact.type }}</strong><small>{{ artifact.provider }} · {{ artifact.id }}</small></div></div>
        </div>
      </article>
    </div>
  </section>
</template>

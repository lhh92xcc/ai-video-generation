<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { ApiClientError } from '../api/client'
import { createNovelProject, getNovelProjects } from '../api/novels'
import { createTopicGeneration, createTopicProject, getTopicProjects } from '../api/projects'
import { getArtifacts, getTask, getTasks } from '../api/tasks'
import CreatorProjectWorkspace from './CreatorProjectWorkspace.vue'
import MediaPreview from './MediaPreview.vue'
import { friendlyErrorMessage, formatStatus as formatTaskStatus, formatTaskKind as formatTaskKindLabel, taskErrorDetail } from '../utils/taskStatus'
import type { ArtifactRecord, GenerationTaskRecord, NovelProjectRecord, TaskStatus } from '../types/task'
import type { TopicProjectAspectRatio, TopicProjectRecord } from '../types/project'

type OperatorView = 'overview' | 'subtitle' | 'tasks' | 'queue' | 'artifacts' | 'workbench'

type CreatorProjectCard = {
  kind: 'novel' | 'topic'
  project: NovelProjectRecord | TopicProjectRecord
  meta: string
}

type TopicLaunchState = {
  project: TopicProjectRecord
  task: GenerationTaskRecord
}

const emit = defineEmits<{ openOperator: [view?: OperatorView] }>()

const projects = ref<NovelProjectRecord[]>([])
const topicProjects = ref<TopicProjectRecord[]>([])
const tasks = ref<GenerationTaskRecord[]>([])
const videos = ref<ArtifactRecord[]>([])
const loading = ref(true)
const errorMessage = ref<string | null>(null)
const showCreatePanel = ref(false)
const creating = ref(false)
const createMessage = ref<string | null>(null)
const createError = ref<string | null>(null)
const activeProjectId = ref<string | null>(null)
const sourceMode = ref<'novel' | 'topic'>('novel')
const title = ref('')
const episodeCount = ref(3)
// A short first run gives the portfolio gate enough material to judge without
// making the creator wait through a full long-form episode. Longer production
// remains available from the same selector.
const episodeDuration = ref(60)
const rightsConfirmed = ref(false)
const topicTitle = ref('')
const topicText = ref('')
const topicDuration = ref(60)
const topicAspectRatio = ref<TopicProjectAspectRatio>('9:16')
const topicTone = ref('清晰、实用')
const topicLaunch = ref<TopicLaunchState | null>(null)
const topicTaskLoading = ref(false)
const topicTaskError = ref<string | null>(null)
let dashboardRequestId = 0
let dashboardRefreshTimer: ReturnType<typeof window.setInterval> | null = null
let topicTaskPollTimer: ReturnType<typeof window.setInterval> | null = null

const activeTasks = computed(() => tasks.value.filter((task) => ['created', 'queued', 'running'].includes(task.status)).length)
const totalProjects = computed(() => projects.value.length + topicProjects.value.length)
const currentProject = computed(() => projects.value[0] ?? null)
const activeProject = computed(() => projects.value.find((project) => project.id === activeProjectId.value) ?? null)
const latestActiveTask = computed(() => [...tasks.value]
  .filter((task) => ['created', 'queued', 'running'].includes(task.status))
  .sort((left, right) => right.updated_at.localeCompare(left.updated_at))[0] ?? null)
const latestScriptTask = computed(() => [...tasks.value]
  .filter((task) => ['novel_episode_script', 'info_script'].includes(task.kind))
  .sort((left, right) => right.updated_at.localeCompare(left.updated_at))[0] ?? null)
const heroScriptTitle = computed(() => {
  const task = latestScriptTask.value
  const topicScript = task?.kind === 'info_script'
  if (topicScript && task?.status === 'succeeded') return '主题脚本已就绪'
  if (topicScript && task && ['created', 'queued', 'running'].includes(task.status)) return '主题脚本处理中'
  if (topicScript && task?.status === 'failed') return '主题脚本需要处理'
  if (task?.status === 'succeeded') return '分场剧本已就绪'
  if (task && ['created', 'queued', 'running'].includes(task.status)) return '分场剧本处理中'
  if (task?.status === 'failed') return '分场剧本需要处理'
  if (currentProject.value) return '等待分场剧本'
  return '等待小说输入'
})
const heroScriptMark = computed(() => {
  const status = latestScriptTask.value?.status
  if (status === 'succeeded') return '✓'
  if (status === 'failed') return '!'
  if (status && ['created', 'queued', 'running'].includes(status)) return '↻'
  return '—'
})
const heroTaskProgress = computed(() => {
  const progress = Number(latestActiveTask.value?.progress)
  return Number.isFinite(progress) && progress > 0 ? Math.max(0, Math.min(100, Math.round(progress))) : null
})
const heroPreviewCaption = computed(() => {
  if (latestActiveTask.value) return formatTaskKind(latestActiveTask.value.kind)
  if (videos.value.length > 0) return '最近成片已就绪'
  if (totalProjects.value > 0) return '工作区已就绪'
  return '等待你的第一个项目'
})
const heroRenderTitle = computed(() => {
  if (latestActiveTask.value) return `${formatStatus(latestActiveTask.value.status)} · ${formatTaskKind(latestActiveTask.value.kind)}`
  if (videos.value.length > 0) return '成片可以开始复核'
  if (totalProjects.value > 0) return '可以继续制作'
  return '从一个小说项目开始'
})
const heroRenderDetail = computed(() => {
  const task = latestActiveTask.value
  if (task) return task.current_stage ? `当前阶段：${task.current_stage}` : '任务已进入可观察的生产队列'
  if (videos.value.length > 0) return '最近结果已进入媒体资产库，可播放、下载并继续审核。'
  if (totalProjects.value > 0) return '选择一个项目，继续推进剧本、资产、媒体和成片。'
  return '创建项目后，这里会显示真实的生产状态。'
})
const recentTaskGroups = computed(() => {
  const groups = new Map<string, { task: GenerationTaskRecord; count: number }>()
  for (const task of [...tasks.value].sort((left, right) => right.updated_at.localeCompare(left.updated_at))) {
    const key = `${task.kind}:${task.status}:${task.error?.code ?? ''}`
    const existing = groups.get(key)
    if (existing) existing.count += 1
    else groups.set(key, { task, count: 1 })
  }
  return [...groups.values()].slice(0, 4)
})
const canCreate = computed(() => Boolean(title.value.trim()) && rightsConfirmed.value && !creating.value && sourceMode.value === 'novel')
const canCreateTopic = computed(() => Boolean(topicTitle.value.trim() && topicText.value.trim()) && !creating.value && sourceMode.value === 'topic')
const topicTaskProgress = computed(() => {
  const progress = Number(topicLaunch.value?.task.progress)
  return Number.isFinite(progress) ? Math.max(0, Math.min(100, Math.round(progress))) : 0
})
const topicTaskStatusLabel = computed(() => topicLaunch.value ? formatStatus(topicLaunch.value.task.status) : '未提交')
const topicTaskTitle = computed(() => {
  const task = topicLaunch.value?.task
  if (!task) return '等待提交主题脚本'
  if (task.status === 'succeeded') return '主题脚本已生成'
  if (task.status === 'failed') return '主题脚本生成失败'
  if (task.status === 'canceled') return '主题脚本已取消'
  return '主题脚本正在后台生成'
})
const topicTaskDetail = computed(() => {
  const launch = topicLaunch.value
  if (!launch) return ''
  if (launch.task.error) return taskErrorDetail(launch.task.error)
  if (topicTaskError.value) return topicTaskError.value
  if (launch.task.status === 'succeeded') return '脚本 JSON 已进入任务中心；素材、配音、字幕和成片仍需在后续流水线中继续接入。'
  if (launch.task.current_stage) return `当前阶段：${launch.task.current_stage}，页面会自动刷新任务状态。`
  return '任务已提交到异步队列，页面会自动刷新任务状态。'
})
const recentProjects = computed<CreatorProjectCard[]>(() => {
  const cards: CreatorProjectCard[] = [
    ...projects.value.map((project) => ({
      kind: 'novel' as const,
      project,
      meta: `${project.target_episode_count} 集 · 每集约 ${project.target_episode_duration_seconds} 秒`,
    })),
    ...topicProjects.value.map((project) => ({
      kind: 'topic' as const,
      project,
      meta: `主题短视频 · ${project.target_duration_seconds} 秒 · ${project.aspect_ratio}`,
    })),
  ]
  return cards.sort((left, right) => String(right.project.updated_at ?? '').localeCompare(String(left.project.updated_at ?? ''))).slice(0, 4)
})

function formatTaskKind(kind: string) {
  return formatTaskKindLabel(kind)
}

function formatStatus(status: TaskStatus) {
  return formatTaskStatus(status)
}

function projectTitle(projectId: string) {
  return projects.value.find((project) => project.id === projectId)?.title
    ?? topicProjects.value.find((project) => project.id === projectId)?.title
    ?? '项目'
}

function projectStatusLabel(status: string) {
  if (status === 'ready') return '可继续制作'
  if (status === 'generating' || status === 'analyzing') return '生成中'
  if (status === 'failed') return '需要处理'
  return '草稿'
}

function formatTime(value: string) {
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('zh-CN', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
}

function displayError(error: unknown) {
  if (error instanceof ApiClientError) return taskErrorDetail({ code: error.code, message: error.message })
  return '暂时无法读取工作区数据，请稍后重试。'
}

async function refreshDashboard() {
  const requestId = ++dashboardRequestId
  loading.value = true
  errorMessage.value = null
  try {
    const [projectItems, topicProjectResponse, taskResponse, videoResponse] = await Promise.all([
      getNovelProjects(),
      getTopicProjects(),
      getTasks({ limit: 8 }),
      getArtifacts({ type: 'rendered_video', limit: 6, expiresInSeconds: 3600 }),
    ])
    if (requestId !== dashboardRequestId) return
    projects.value = projectItems
    topicProjects.value = topicProjectResponse.items
    tasks.value = taskResponse.items
    videos.value = videoResponse.items
  } catch (error) {
    if (requestId !== dashboardRequestId) return
    errorMessage.value = displayError(error)
  } finally {
    if (requestId === dashboardRequestId) loading.value = false
  }
}

function openCreatePanel(mode: 'novel' | 'topic' = 'novel') {
  createMessage.value = null
  createError.value = null
  sourceMode.value = mode
  showCreatePanel.value = true
}

function closeCreatePanel() {
  if (!creating.value) showCreatePanel.value = false
}

function openProject(projectId: string) {
  activeProjectId.value = projectId
  window.setTimeout(() => document.getElementById('creator-project-workspace')?.scrollIntoView({ behavior: 'smooth', block: 'start' }), 0)
}

function openProjectCard(card: CreatorProjectCard) {
  if (card.kind === 'novel') {
    openProject(card.project.id)
    return
  }
  const task = [...tasks.value]
    .filter((item) => item.project_id === card.project.id && item.kind === 'info_script')
    .sort((left, right) => right.updated_at.localeCompare(left.updated_at))[0]
  if (task) {
    topicLaunch.value = { project: card.project, task }
    topicTaskError.value = null
    startTopicTaskPolling()
  }
  openOperator('tasks')
}

function openBatchProduction() {
  if (currentProject.value) {
    openProject(currentProject.value.id)
    return
  }
  openCreatePanel()
}

function closeProjectWorkspace() {
  activeProjectId.value = null
  void refreshDashboard()
}

function openOperator(view?: OperatorView) {
  emit('openOperator', view)
}

function stopTopicTaskPolling() {
  if (topicTaskPollTimer !== null) {
    window.clearInterval(topicTaskPollTimer)
    topicTaskPollTimer = null
  }
}

async function refreshTopicTask() {
  const launch = topicLaunch.value
  if (!launch) return
  topicTaskLoading.value = true
  try {
    const task = await getTask(launch.task.id)
    if (!topicLaunch.value || topicLaunch.value.task.id !== task.id) return
    topicLaunch.value = { ...topicLaunch.value, task }
    if (!['created', 'queued', 'running'].includes(task.status)) {
      stopTopicTaskPolling()
      void refreshDashboard()
    }
  } catch (error) {
    topicTaskError.value = displayError(error)
  } finally {
    topicTaskLoading.value = false
  }
}

function startTopicTaskPolling() {
  stopTopicTaskPolling()
  void refreshTopicTask()
  topicTaskPollTimer = window.setInterval(() => void refreshTopicTask(), 2000)
}

function dismissTopicLaunch() {
  stopTopicTaskPolling()
  topicLaunch.value = null
  topicTaskError.value = null
}

function openTopicTaskCenter() {
  openOperator('tasks')
}

async function createWorkspace() {
  if (!canCreate.value) return
  creating.value = true
  createMessage.value = null
  createError.value = null
  try {
    const project = await createNovelProject({
      title: title.value.trim(),
      target_episode_count: episodeCount.value,
      target_episode_duration_seconds: episodeDuration.value,
      rights_status: 'confirmed',
    })
    projects.value = [project, ...projects.value.filter((item) => item.id !== project.id)]
    activeProjectId.value = project.id
    showCreatePanel.value = false
    createMessage.value = '项目已创建，已打开创作者制作工作区。'
    title.value = ''
    rightsConfirmed.value = false
  } catch (error) {
    createError.value = displayError(error)
  } finally {
    creating.value = false
  }
}

async function createTopicWorkspace() {
  if (!canCreateTopic.value) return
  creating.value = true
  createMessage.value = null
  createError.value = null
  topicTaskError.value = null
  try {
    const project = await createTopicProject({
      title: topicTitle.value.trim(),
      topic: topicText.value.trim(),
      language: 'zh-CN',
      target_duration_seconds: topicDuration.value,
      aspect_ratio: topicAspectRatio.value,
      tone: topicTone.value,
    })
    topicProjects.value = [project, ...topicProjects.value.filter((item) => item.id !== project.id)]
    const task = await createTopicGeneration(project.id, `creator-topic-generation-${project.id}`)
    topicLaunch.value = { project, task }
    showCreatePanel.value = false
    topicTitle.value = ''
    topicText.value = ''
    startTopicTaskPolling()
    void refreshDashboard()
  } catch (error) {
    createError.value = displayError(error)
  } finally {
    creating.value = false
  }
}

onMounted(() => {
  void refreshDashboard()
  // The landing page shows a live summary. Refresh it while no project detail
  // workspace is open, but never let a slow old response overwrite newer data.
  dashboardRefreshTimer = window.setInterval(() => {
    if (!activeProjectId.value && !creating.value) void refreshDashboard()
  }, 8000)
})

onUnmounted(() => {
  if (dashboardRefreshTimer !== null) window.clearInterval(dashboardRefreshTimer)
  stopTopicTaskPolling()
})
</script>

<template>
  <div class="creator-shell">
    <header class="creator-header">
      <a class="creator-brand" href="#" @click.prevent="refreshDashboard">
        <span class="creator-brand-mark">V</span>
        <span><strong>Video Forge</strong><small>AI VIDEO STUDIO</small></span>
      </a>
      <nav class="creator-nav" aria-label="创作者导航">
        <template v-if="activeProject">
          <a href="#projects" @click.prevent="closeProjectWorkspace">返回项目列表</a>
          <a href="#creator-project-workspace">当前项目</a>
          <a href="#creator-workflow-status">制作流程</a>
        </template>
        <template v-else>
          <a href="#create" @click.prevent="openCreatePanel()">创建视频</a>
          <a href="#projects">我的项目</a>
          <a href="#progress">制作进度</a>
        </template>
      </nav>
      <div class="creator-header-actions">
        <span class="creator-env-badge"><i />本地体验环境</span>
        <button class="creator-admin-link" type="button" @click="emit('openOperator')">进入制作后台 <span>↗</span></button>
        <span class="creator-user-avatar">Q</span>
      </div>
    </header>

    <main class="creator-main">
      <section v-if="!activeProject" class="creator-hero">
        <div class="creator-hero-copy">
          <p class="creator-eyebrow">AI VIDEO WORKSPACE</p>
          <h1>把一个想法，<br /><span>变成可审核的成片。</span></h1>
          <p class="creator-hero-description">从小说到剧本、资产、镜头和成片，Video Forge 把内容生产拆成可追踪、可审核、可恢复的工作流。</p>
          <div class="creator-hero-actions">
            <button class="creator-primary-button" type="button" @click="openCreatePanel()">开始创建 <span>→</span></button>
            <button class="creator-ghost-button" type="button" @click="emit('openOperator')">查看制作后台</button>
          </div>
          <div class="creator-trust-row"><span><i>✓</i>脚本可编辑</span><span><i>✓</i>任务可追踪</span><span><i>✓</i>成片可预览</span></div>
        </div>
        <div class="creator-hero-visual" aria-label="视频生产流程预览">
          <div class="creator-visual-glow" />
          <div class="creator-preview-window">
            <div class="creator-preview-topbar"><span /><span /><span /><small>VIDEO FORGE / PREVIEW</small></div>
            <div class="creator-preview-scene"><div class="creator-preview-sun" /><div class="creator-preview-mountain mountain-one" /><div class="creator-preview-mountain mountain-two" /><div class="creator-preview-ground" /><div class="creator-preview-caption">{{ heroPreviewCaption }}</div></div>
            <div class="creator-preview-timeline"><span v-for="segment in 4" :key="segment" :class="{ complete: heroTaskProgress !== null && heroTaskProgress >= segment * 25 }" /></div>
          </div>
          <div class="creator-floating-card creator-floating-script"><span class="creator-floating-icon">✦</span><div><small>AI SCRIPT</small><strong>{{ heroScriptTitle }}</strong></div><b>{{ heroScriptMark }}</b></div>
          <div class="creator-floating-card creator-floating-render"><span class="creator-floating-icon">◉</span><div><small>LIVE PIPELINE</small><strong>{{ heroRenderTitle }}</strong><small>{{ heroRenderDetail }}</small></div><b>{{ heroTaskProgress === null ? (latestActiveTask ? 'RUN' : 'READY') : `${heroTaskProgress}%` }}</b></div>
        </div>
      </section>

      <section v-if="errorMessage && !activeProject" class="creator-alert"><strong>工作区暂时不可用</strong><span>{{ errorMessage }}</span><button type="button" @click="refreshDashboard">重试</button></section>

      <CreatorProjectWorkspace
        v-if="activeProject"
        id="creator-project-workspace"
        :project="activeProject"
        @back="closeProjectWorkspace"
        @changed="refreshDashboard"
        @open-operator="openOperator"
      />

      <section v-if="!activeProject" class="creator-metrics" aria-label="工作区概览">
        <article><span class="creator-metric-icon purple">✦</span><div><small>我的项目</small><strong>{{ loading ? '—' : totalProjects }}</strong></div><span class="creator-metric-note">小说 + 主题</span></article>
        <article><span class="creator-metric-icon blue">↻</span><div><small>进行中的任务</small><strong>{{ loading ? '—' : activeTasks }}</strong></div><span class="creator-metric-note" :class="{ good: activeTasks === 0 }">{{ activeTasks ? '实时处理中' : '当前空闲' }}</span></article>
        <article><span class="creator-metric-icon purple">◇</span><div><small>生产流程</small><strong>5</strong></div><span class="creator-metric-note good">可观察</span></article>
      </section>

      <section v-if="!activeProject" id="create" class="creator-section creator-workflow-section">
        <div class="creator-section-heading"><div><p class="creator-eyebrow">START WITH A BRIEF</p><h2>从内容开始创建</h2><p>选择一种内容入口，剩下的工作交给可观察的生产流水线。</p></div><button class="creator-link-button" type="button" @click="openCreatePanel()">创建一个项目 <span>→</span></button></div>
        <div class="creator-workflow-cards">
          <button class="creator-workflow-card selected" type="button" @click="openCreatePanel()"><span class="creator-card-number">01</span><span class="creator-workflow-icon novel">▤</span><strong>小说短剧</strong><p>导入小说，生成 StoryBible、分集剧本、分镜和视频片段。</p><span class="creator-card-link">立即开始 <b>→</b></span></button>
          <button class="creator-workflow-card" type="button" @click="openCreatePanel('topic')"><span class="creator-card-number">02</span><span class="creator-workflow-icon topic">✦</span><strong>主题短视频</strong><p>输入主题、时长和风格，异步生成结构化信息短视频脚本。</p><span class="creator-card-link">开始生成 <b>→</b></span></button>
          <button class="creator-workflow-card" type="button" @click="openBatchProduction"><span class="creator-card-number">03</span><span class="creator-workflow-icon series">▦</span><strong>分集批量生产</strong><p>在项目内选择多集，按依赖自动排队生成，并支持失败任务恢复。</p><span class="creator-card-link">{{ currentProject ? '打开项目计划' : '先创建项目' }} <b>→</b></span></button>
        </div>
      </section>

      <section v-if="topicLaunch && !activeProject" class="creator-section creator-topic-launch" data-test="topic-launch-card" aria-live="polite">
        <div class="creator-topic-launch-icon" :class="topicLaunch.task.status"><span>{{ topicLaunch.task.status === 'succeeded' ? '✓' : topicLaunch.task.status === 'failed' ? '!' : '✦' }}</span></div>
        <div class="creator-topic-launch-copy"><p class="creator-eyebrow">TOPIC SCRIPT JOB</p><h2>{{ topicTaskTitle }}</h2><strong>{{ topicLaunch.project.title }}</strong><small>{{ topicLaunch.project.topic }}</small><p>{{ topicTaskDetail }}</p></div>
        <div class="creator-topic-launch-progress"><div class="creator-topic-launch-status"><span>{{ topicTaskStatusLabel }}</span><small v-if="topicTaskLoading">刷新中…</small></div><div class="creator-topic-progress-track"><i :style="{ width: `${topicTaskProgress}%` }" /></div><small>{{ topicTaskProgress }}%</small></div>
        <div class="creator-topic-launch-actions"><button class="creator-small-button" type="button" @click="openTopicTaskCenter">查看生产任务 <span>↗</span></button><button class="creator-topic-dismiss" type="button" aria-label="关闭主题任务提示" @click="dismissTopicLaunch">×</button></div>
      </section>

      <section v-if="!activeProject" id="projects" class="creator-section creator-dashboard-grid">
        <article class="creator-panel creator-project-panel">
          <div class="creator-panel-heading"><div><p class="creator-eyebrow">YOUR CONTENT</p><h2>最近项目</h2></div><button class="creator-small-link" type="button" @click="emit('openOperator')">管理项目 <span>→</span></button></div>
          <div v-if="loading" class="creator-empty"><span class="spinner" />正在读取项目…</div>
          <div v-else-if="recentProjects.length === 0" class="creator-empty"><strong>还没有项目</strong><span>从上方开始创建你的第一个项目。</span><button class="creator-small-button" type="button" @click="openCreatePanel()">创建项目</button></div>
          <div v-else class="creator-project-list">
            <button v-for="card in recentProjects" :key="`${card.kind}-${card.project.id}`" class="creator-project-row" type="button" @click="openProjectCard(card)"><span class="creator-project-cover" :class="{ topic: card.kind === 'topic' }">{{ card.project.title.slice(0, 1) }}</span><div><strong>{{ card.project.title }}</strong><small>{{ card.meta }}</small></div><span class="creator-project-status">{{ projectStatusLabel(card.project.status) }}</span><b>→</b></button>
          </div>
        </article>

        <article id="progress" class="creator-panel creator-task-panel">
          <div class="creator-panel-heading"><div><p class="creator-eyebrow">LIVE PIPELINE</p><h2>制作进度</h2></div><button class="creator-small-link" type="button" @click="emit('openOperator', 'tasks')">查看全部 <span>→</span></button></div>
          <div v-if="loading" class="creator-empty"><span class="spinner" />正在读取任务…</div>
          <div v-else-if="tasks.length === 0" class="creator-empty"><strong>暂无制作任务</strong><span>创建项目后，任务进度会显示在这里。</span></div>
          <div v-else class="creator-task-list"><div v-for="group in recentTaskGroups" :key="`${group.task.id}-${group.task.status}-${group.task.error?.code ?? ''}`" class="creator-task-row"><span class="creator-task-mark" :class="group.task.status">{{ group.task.status === 'succeeded' ? '✓' : group.task.status === 'failed' ? '!' : '↻' }}</span><div><strong>{{ formatTaskKind(group.task.kind) }}<em v-if="group.count > 1">共 {{ group.count }} 条记录</em></strong><small>{{ projectTitle(group.task.project_id) }} · {{ group.task.error ? friendlyErrorMessage(group.task.error) : formatTime(group.task.updated_at) }}</small></div><span class="creator-task-status" :class="group.task.status">{{ formatStatus(group.task.status) }}</span></div></div>
        </article>
      </section>

      <section v-if="videos.length && !activeProject" class="creator-section creator-output-section">
        <div class="creator-section-heading"><div><p class="creator-eyebrow">YOUR OUTPUTS</p><h2>最近成片</h2><p>已完成的视频可以在媒体资产中预览和下载。</p></div><button class="creator-link-button" type="button" @click="emit('openOperator', 'artifacts')">打开媒体资产 <span>→</span></button></div>
        <div class="creator-output-strip"><article v-for="video in videos.slice(0, 3)" :key="video.id" class="creator-output-card"><div class="creator-output-thumbnail"><MediaPreview :artifact="video" variant="thumb" :controls="false" alt="最近成片预览" /><span class="creator-output-play">▶</span><small>{{ video.metadata.duration_seconds ? `${Number(video.metadata.duration_seconds).toFixed(0)}s` : 'VIDEO' }}</small></div><div><strong>成片视频</strong><small>{{ video.provider }} · {{ formatTime(video.created_at) }}</small></div></article></div>
      </section>
    </main>

    <footer class="creator-footer"><span>Video Forge · AI 内容生产平台</span><span>生成结果需要经过人工审核后再发布</span></footer>

    <div v-if="showCreatePanel" class="creator-modal-backdrop" @click.self="closeCreatePanel">
      <section class="creator-create-modal" role="dialog" aria-modal="true" aria-labelledby="creator-create-title">
        <div class="creator-modal-heading"><div><p class="creator-eyebrow">NEW WORKSPACE</p><h2 id="creator-create-title">创建一个内容项目</h2><p>选择内容入口，提交后任务会在后台异步执行。</p></div><button class="creator-modal-close" type="button" aria-label="关闭" @click="closeCreatePanel">×</button></div>
        <div class="creator-mode-picker"><button type="button" :class="{ active: sourceMode === 'novel' }" @click="sourceMode = 'novel'"><strong>小说短剧</strong><small>多阶段生产</small></button><button type="button" :class="{ active: sourceMode === 'topic' }" @click="sourceMode = 'topic'"><strong>主题短视频</strong><small>脚本任务已可用</small></button></div>
        <div v-if="sourceMode === 'novel'" class="creator-create-form"><label><span>项目名称</span><input v-model="title" maxlength="120" placeholder="例如：雨夜来信" /></label><div class="creator-form-row"><label><span>预计集数</span><select v-model="episodeCount"><option :value="1">1 集</option><option :value="3">3 集</option><option :value="6">6 集</option><option :value="12">12 集</option></select></label><label><span>目标单集时长</span><select v-model="episodeDuration"><option :value="45">约 45 秒 · 作品集</option><option :value="60">约 60 秒 · 推荐</option><option :value="90">约 90 秒</option><option :value="180">约 3 分钟</option><option :value="300">约 5 分钟</option></select><small class="creator-field-hint">作品集 Demo 建议先选 45–60 秒；长内容可在这里继续创建。</small></label></div><div class="creator-create-note"><span>✦</span><div><strong>推荐先做一集短样片</strong><small>先验证剧本、身份一致性、声音和字幕，再扩展到批量生产。</small></div></div><label class="creator-checkbox"><input v-model="rightsConfirmed" type="checkbox" /><span>我确认已获得该内容的创作或改编授权</span></label><div v-if="createError" class="creator-form-error">{{ createError }}</div><div v-if="createMessage" class="creator-form-success">{{ createMessage }}</div><div class="creator-modal-actions"><button class="creator-ghost-button" type="button" @click="closeCreatePanel">稍后再做</button><button class="creator-primary-button" type="button" :disabled="!canCreate" @click="createWorkspace">{{ creating ? '创建中…' : '创建项目并继续' }} <span>→</span></button></div></div>
        <div v-else class="creator-create-form"><label><span>视频标题</span><input v-model="topicTitle" maxlength="120" placeholder="例如：新手露营装备怎么选" /></label><label><span>主题 / 关键词</span><textarea v-model="topicText" maxlength="500" rows="3" placeholder="例如：面向第一次周末露营的人，讲清帐篷、睡袋和照明的选择顺序。" /></label><div class="creator-form-row"><label><span>目标时长</span><select v-model="topicDuration"><option :value="15">约 15 秒 · 快速测试</option><option :value="30">约 30 秒</option><option :value="45">约 45 秒 · 作品集</option><option :value="60">约 60 秒 · 推荐</option><option :value="90">约 90 秒</option><option :value="120">约 2 分钟</option><option :value="180">约 3 分钟</option></select></label><label><span>画面比例</span><select v-model="topicAspectRatio"><option value="9:16">9:16 · 竖屏</option><option value="16:9">16:9 · 横屏</option><option value="1:1">1:1 · 方形</option></select></label></div><label><span>表达风格</span><select v-model="topicTone"><option value="清晰、实用">清晰、实用</option><option value="轻松口语">轻松口语</option><option value="专业可信">专业可信</option><option value="悬念盘点">悬念盘点</option><option value="故事化表达">故事化表达</option></select></label><div class="creator-create-note"><span>✦</span><div><strong>当前先生成结构化脚本</strong><small>任务会输出可审核的 script JSON；素材搜索、配音、字幕和成片合成会沿用同一队列继续扩展。</small></div></div><div v-if="createError" class="creator-form-error">{{ createError }}</div><div class="creator-modal-actions"><button class="creator-ghost-button" type="button" @click="closeCreatePanel">稍后再做</button><button class="creator-primary-button" type="button" :disabled="!canCreateTopic" @click="createTopicWorkspace">{{ creating ? '提交中…' : '创建并生成脚本' }} <span>→</span></button></div></div>
      </section>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ApiClientError } from '../api/client'
import { createNovelProject, getNovelProjects } from '../api/novels'
import { getArtifacts, getTasks } from '../api/tasks'
import CreatorProjectWorkspace from './CreatorProjectWorkspace.vue'
import MediaPreview from './MediaPreview.vue'
import { friendlyErrorMessage, formatStatus as formatTaskStatus, formatTaskKind as formatTaskKindLabel, taskErrorDetail } from '../utils/taskStatus'
import type { ArtifactRecord, GenerationTaskRecord, NovelProjectRecord, TaskStatus } from '../types/task'

type OperatorView = 'overview' | 'subtitle' | 'tasks' | 'queue' | 'artifacts' | 'workbench'

const emit = defineEmits<{ openOperator: [view?: OperatorView] }>()

const projects = ref<NovelProjectRecord[]>([])
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
const episodeDuration = ref(90)
const rightsConfirmed = ref(false)

const activeTasks = computed(() => tasks.value.filter((task) => ['created', 'queued', 'running'].includes(task.status)).length)
const completedTasks = computed(() => tasks.value.filter((task) => task.status === 'succeeded').length)
const currentProject = computed(() => projects.value[0] ?? null)
const activeProject = computed(() => projects.value.find((project) => project.id === activeProjectId.value) ?? null)
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

function formatTaskKind(kind: string) {
  return formatTaskKindLabel(kind)
}

function formatStatus(status: TaskStatus) {
  return formatTaskStatus(status)
}

function projectStatusLabel(status: string) {
  return status === 'ready' ? '可继续制作' : status === 'failed' ? '需要处理' : '草稿'
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
  loading.value = true
  errorMessage.value = null
  try {
    const [projectItems, taskResponse, videoResponse] = await Promise.all([
      getNovelProjects(),
      getTasks({ limit: 8 }),
      getArtifacts({ type: 'rendered_video', limit: 6, expiresInSeconds: 3600 }),
    ])
    projects.value = projectItems
    tasks.value = taskResponse.items
    videos.value = videoResponse.items
  } catch (error) {
    errorMessage.value = displayError(error)
  } finally {
    loading.value = false
  }
}

function openCreatePanel() {
  createMessage.value = null
  createError.value = null
  showCreatePanel.value = true
}

function closeCreatePanel() {
  if (!creating.value) showCreatePanel.value = false
}

function openProject(projectId: string) {
  activeProjectId.value = projectId
  window.setTimeout(() => document.getElementById('creator-project-workspace')?.scrollIntoView({ behavior: 'smooth', block: 'start' }), 0)
}

function closeProjectWorkspace() {
  activeProjectId.value = null
  void refreshDashboard()
}

function openOperator(view?: OperatorView) {
  emit('openOperator', view)
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

onMounted(refreshDashboard)
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
          <a href="#create" @click.prevent="openCreatePanel">创建视频</a>
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
          <h1>把一个想法，<br /><span>变成可发布的视频。</span></h1>
          <p class="creator-hero-description">从小说、主题到分镜和成片，Video Forge 帮你把内容生产拆成可追踪、可审核、可恢复的工作流。</p>
          <div class="creator-hero-actions">
            <button class="creator-primary-button" type="button" @click="openCreatePanel">开始创建 <span>→</span></button>
            <button class="creator-ghost-button" type="button" @click="emit('openOperator')">查看制作后台</button>
          </div>
          <div class="creator-trust-row"><span><i>✓</i>脚本可编辑</span><span><i>✓</i>任务可追踪</span><span><i>✓</i>成片可预览</span></div>
        </div>
        <div class="creator-hero-visual" aria-label="视频生产流程预览">
          <div class="creator-visual-glow" />
          <div class="creator-preview-window">
            <div class="creator-preview-topbar"><span /><span /><span /><small>VIDEO FORGE / PREVIEW</small></div>
            <div class="creator-preview-scene"><div class="creator-preview-sun" /><div class="creator-preview-mountain mountain-one" /><div class="creator-preview-mountain mountain-two" /><div class="creator-preview-ground" /><div class="creator-preview-caption">故事正在生成中</div></div>
            <div class="creator-preview-timeline"><span /><span /><span /><span /></div>
          </div>
          <div class="creator-floating-card creator-floating-script"><span class="creator-floating-icon">✦</span><div><small>AI SCRIPT</small><strong>分场剧本已就绪</strong></div><b>✓</b></div>
          <div class="creator-floating-card creator-floating-render"><span class="creator-floating-icon">◉</span><div><small>RENDER QUEUE</small><strong>正在合成第 03 集</strong></div><b>72%</b></div>
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
        <article><span class="creator-metric-icon purple">✦</span><div><small>我的项目</small><strong>{{ loading ? '—' : projects.length }}</strong></div><span class="creator-metric-note">内容空间</span></article>
        <article><span class="creator-metric-icon blue">↻</span><div><small>进行中的任务</small><strong>{{ loading ? '—' : activeTasks }}</strong></div><span class="creator-metric-note" :class="{ good: activeTasks === 0 }">{{ activeTasks ? '实时处理中' : '当前空闲' }}</span></article>
        <article><span class="creator-metric-icon green">✓</span><div><small>已完成步骤</small><strong>{{ loading ? '—' : completedTasks }}</strong></div><span class="creator-metric-note good">可复用</span></article>
      </section>

      <section v-if="!activeProject" id="create" class="creator-section creator-workflow-section">
        <div class="creator-section-heading"><div><p class="creator-eyebrow">START WITH A BRIEF</p><h2>从内容开始创建</h2><p>选择一种内容入口，剩下的工作交给可观察的生产流水线。</p></div><button class="creator-link-button" type="button" @click="openCreatePanel">创建一个项目 <span>→</span></button></div>
        <div class="creator-workflow-cards">
          <button class="creator-workflow-card selected" type="button" @click="openCreatePanel"><span class="creator-card-number">01</span><span class="creator-workflow-icon novel">▤</span><strong>小说短剧</strong><p>导入小说，生成 StoryBible、分集剧本、分镜和视频片段。</p><span class="creator-card-link">立即开始 <b>→</b></span></button>
          <article class="creator-workflow-card disabled"><span class="creator-card-number">02</span><span class="creator-workflow-icon topic">✦</span><strong>主题短视频</strong><p>输入一个主题，生成信息型短视频脚本和成片。</p><span class="creator-coming-soon">即将接入</span></article>
          <article class="creator-workflow-card disabled"><span class="creator-card-number">03</span><span class="creator-workflow-icon series">▦</span><strong>批量生产</strong><p>一次配置多个选题，自动排队生成并统一查看结果。</p><span class="creator-coming-soon">规划中</span></article>
        </div>
      </section>

      <section v-if="!activeProject" id="projects" class="creator-section creator-dashboard-grid">
        <article class="creator-panel creator-project-panel">
          <div class="creator-panel-heading"><div><p class="creator-eyebrow">YOUR CONTENT</p><h2>最近项目</h2></div><button class="creator-small-link" type="button" @click="emit('openOperator')">管理项目 <span>→</span></button></div>
          <div v-if="loading" class="creator-empty"><span class="spinner" />正在读取项目…</div>
          <div v-else-if="projects.length === 0" class="creator-empty"><strong>还没有项目</strong><span>从上方开始创建你的第一个项目。</span><button class="creator-small-button" type="button" @click="openCreatePanel">创建项目</button></div>
          <div v-else class="creator-project-list">
            <button v-for="project in projects.slice(0, 4)" :key="project.id" class="creator-project-row" type="button" @click="openProject(project.id)"><span class="creator-project-cover">{{ project.title.slice(0, 1) }}</span><div><strong>{{ project.title }}</strong><small>{{ project.target_episode_count }} 集 · 每集约 {{ project.target_episode_duration_seconds }} 秒</small></div><span class="creator-project-status">{{ projectStatusLabel(project.status) }}</span><b>→</b></button>
          </div>
        </article>

        <article id="progress" class="creator-panel creator-task-panel">
          <div class="creator-panel-heading"><div><p class="creator-eyebrow">LIVE PIPELINE</p><h2>制作进度</h2></div><button class="creator-small-link" type="button" @click="emit('openOperator', 'tasks')">查看全部 <span>→</span></button></div>
          <div v-if="loading" class="creator-empty"><span class="spinner" />正在读取任务…</div>
          <div v-else-if="tasks.length === 0" class="creator-empty"><strong>暂无制作任务</strong><span>创建项目后，任务进度会显示在这里。</span></div>
          <div v-else class="creator-task-list"><div v-for="group in recentTaskGroups" :key="`${group.task.id}-${group.task.status}-${group.task.error?.code ?? ''}`" class="creator-task-row"><span class="creator-task-mark" :class="group.task.status">{{ group.task.status === 'succeeded' ? '✓' : group.task.status === 'failed' ? '!' : '↻' }}</span><div><strong>{{ formatTaskKind(group.task.kind) }}<em v-if="group.count > 1">×{{ group.count }}</em></strong><small>{{ group.task.error ? friendlyErrorMessage(group.task.error) : formatTime(group.task.updated_at) }}</small></div><span class="creator-task-status" :class="group.task.status">{{ formatStatus(group.task.status) }}</span></div></div>
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
        <div class="creator-modal-heading"><div><p class="creator-eyebrow">NEW WORKSPACE</p><h2 id="creator-create-title">创建一个内容项目</h2><p>先建立项目空间，再进入制作后台导入小说和生成剧本。</p></div><button class="creator-modal-close" type="button" aria-label="关闭" @click="closeCreatePanel">×</button></div>
        <div class="creator-mode-picker"><button type="button" :class="{ active: sourceMode === 'novel' }" @click="sourceMode = 'novel'"><strong>小说短剧</strong><small>当前可用</small></button><button type="button" :class="{ active: sourceMode === 'topic' }" @click="sourceMode = 'topic'"><strong>主题短视频</strong><small>即将接入</small></button></div>
        <div v-if="sourceMode === 'novel'" class="creator-create-form"><label><span>项目名称</span><input v-model="title" maxlength="120" placeholder="例如：雨夜来信" /></label><div class="creator-form-row"><label><span>预计集数</span><select v-model="episodeCount"><option :value="1">1 集</option><option :value="3">3 集</option><option :value="6">6 集</option><option :value="12">12 集</option></select></label><label><span>单集时长</span><select v-model="episodeDuration"><option :value="60">约 60 秒</option><option :value="90">约 90 秒</option><option :value="180">约 3 分钟</option><option :value="300">约 5 分钟</option></select></label></div><label class="creator-checkbox"><input v-model="rightsConfirmed" type="checkbox" /><span>我确认已获得该内容的创作或改编授权</span></label><div v-if="createError" class="creator-form-error">{{ createError }}</div><div v-if="createMessage" class="creator-form-success">{{ createMessage }}</div><div class="creator-modal-actions"><button class="creator-ghost-button" type="button" @click="closeCreatePanel">稍后再做</button><button class="creator-primary-button" type="button" :disabled="!canCreate" @click="createWorkspace">{{ creating ? '创建中…' : '创建项目并继续' }} <span>→</span></button></div></div>
        <div v-else class="creator-coming-panel"><span class="creator-coming-icon">✦</span><h3>主题短视频入口正在准备</h3><p>当前先完成小说短剧的真实生产闭环。主题脚本 Provider 接入后，这里会支持从关键词直接创建短视频。</p><button class="creator-ghost-button" type="button" @click="sourceMode = 'novel'">使用小说短剧</button></div>
      </section>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { ApiClientError } from '../api/client'
import { getNovelProjects } from '../api/novels'
import { getArtifacts, getTasks } from '../api/tasks'
import type { ArtifactRecord, GenerationTaskRecord, NovelProjectRecord } from '../types/task'
import { friendlyErrorMessage, formatTaskKind, hasBinaryArtifact, isActiveTask, isMediaTaskKind } from '../utils/taskStatus'
import MediaPreview from './MediaPreview.vue'

const projects = ref<NovelProjectRecord[]>([])
const artifacts = ref<ArtifactRecord[]>([])
const tasks = ref<GenerationTaskRecord[]>([])
const selectedArtifactId = ref<string | null>(null)
const projectId = ref('')
type ArtifactFilter = 'all' | 'rendered_video' | 'video' | 'reference_image' | 'audio' | 'subtitle' | 'document'
const artifactType = ref<ArtifactFilter>('all')
const searchQuery = ref('')
const loading = ref(false)
const loadingTasks = ref(false)
const loadingProjects = ref(false)
const errorMessage = ref<string | null>(null)
const tasksError = ref<string | null>(null)

const emit = defineEmits<{ openTasks: [] }>()

const typeLabels: Record<string, string> = {
  all: '全部类型', rendered_video: '成片视频', video_clip: '视频片段', lip_synced_video: '唇形同步视频', audio_narration: '旁白音频', audio_bgm: 'BGM 音频', subtitle_srt: 'SRT 字幕', reference_image: '参考图', script_json: '脚本 JSON', story_bible_json: 'StoryBible', episode_outline_json: '分集大纲', episode_script_json: '分场剧本', shot_list_json: '分镜 JSON',
}
const artifactFilterLabels: Record<ArtifactFilter, string> = {
  all: '全部资产',
  rendered_video: '成片',
  video: '视频片段',
  reference_image: '参考图',
  audio: '音频',
  subtitle: '字幕',
  document: '结构化文件',
}
const selectedArtifact = computed(() => artifacts.value.find((item) => item.id === selectedArtifactId.value) ?? null)
const artifactPriority: Record<string, number> = {
  rendered_video: 1,
  lip_synced_video: 2,
  video_clip: 3,
  reference_image: 4,
  audio_narration: 5,
  audio_bgm: 6,
  subtitle_srt: 7,
  script_json: 8,
  story_bible_json: 9,
  episode_outline_json: 10,
  episode_script_json: 11,
  shot_list_json: 12,
}
const orderedArtifacts = computed(() => [...artifacts.value].sort((left, right) => {
  const priorityDifference = (artifactPriority[left.type] ?? 99) - (artifactPriority[right.type] ?? 99)
  if (priorityDifference !== 0) return priorityDifference
  return right.created_at.localeCompare(left.created_at)
}))
const artifactCounts = computed(() => ({
  video: orderedArtifacts.value.filter(isVideoArtifact).length,
  image: orderedArtifacts.value.filter((artifact) => artifact.type === 'reference_image').length,
  audio: orderedArtifacts.value.filter(isAudioArtifact).length,
}))
const artifactFilterOptions = computed(() => [
  { key: 'all' as ArtifactFilter, label: artifactFilterLabels.all, count: orderedArtifacts.value.length },
  { key: 'rendered_video' as ArtifactFilter, label: artifactFilterLabels.rendered_video, count: orderedArtifacts.value.filter((artifact) => artifact.type === 'rendered_video').length },
  { key: 'video' as ArtifactFilter, label: artifactFilterLabels.video, count: orderedArtifacts.value.filter((artifact) => artifact.type === 'video_clip' || artifact.type === 'lip_synced_video').length },
  { key: 'reference_image' as ArtifactFilter, label: artifactFilterLabels.reference_image, count: orderedArtifacts.value.filter((artifact) => artifact.type === 'reference_image').length },
  { key: 'audio' as ArtifactFilter, label: artifactFilterLabels.audio, count: orderedArtifacts.value.filter(isAudioArtifact).length },
  { key: 'subtitle' as ArtifactFilter, label: artifactFilterLabels.subtitle, count: orderedArtifacts.value.filter((artifact) => artifact.type === 'subtitle_srt').length },
  { key: 'document' as ArtifactFilter, label: artifactFilterLabels.document, count: orderedArtifacts.value.filter(isDocumentArtifact).length },
])
const filteredArtifacts = computed(() => {
  const query = searchQuery.value.trim().toLowerCase()
  const typeFiltered = orderedArtifacts.value.filter((artifact) => matchesArtifactFilter(artifact, artifactType.value))
  if (!query) return typeFiltered
  return typeFiltered.filter((artifact) => [
    typeLabels[artifact.type] ?? artifact.type,
    artifact.type,
    artifact.provider,
    artifact.id,
    String(artifact.metadata.storage_key ?? ''),
  ].join(' ').toLowerCase().includes(query))
})
const selectedProject = computed(() => projects.value.find((project) => project.id === projectId.value) ?? null)
const visualArtifactCount = computed(() => artifacts.value.filter(isVisualArtifact).length)
const visualTasks = computed(() => tasks.value.filter((task) => isVisualTaskKind(task.kind)))
const activeVisualTaskCount = computed(() => visualTasks.value.filter(isActiveTask).length)
const failedVisualTasks = computed(() => visualTasks.value.filter((task) => task.status === 'failed'))
const succeededVisualTasksWithoutArtifact = computed(() => visualTasks.value.filter((task) => task.status === 'succeeded' && !hasBinaryArtifact(task)))
const visualFailureGroups = computed(() => {
  const groups = new Map<string, { kind: string; code: string; message: string; count: number }>()
  for (const task of failedVisualTasks.value) {
    const code = task.error?.code ?? 'TASK_FAILED'
    const key = `${task.kind}:${code}`
    const existing = groups.get(key)
    if (existing) existing.count += 1
    else groups.set(key, { kind: task.kind, code, message: friendlyErrorMessage(task.error), count: 1 })
  }
  return [...groups.values()].sort((left, right) => right.count - left.count)
})
const visualStatusTone = computed<'ready' | 'working' | 'attention' | 'empty' | 'unknown'>(() => {
  if (visualArtifactCount.value > 0) return 'ready'
  if (loadingTasks.value) return 'working'
  if (tasksError.value) return 'unknown'
  if (activeVisualTaskCount.value > 0) return 'working'
  if (failedVisualTasks.value.length > 0 || succeededVisualTasksWithoutArtifact.value.length > 0) return 'attention'
  return 'empty'
})
const visualStatusTitle = computed(() => {
  if (visualArtifactCount.value > 0) return '图片和视频产物可预览'
  if (loadingTasks.value) return '正在同步图片和视频任务'
  if (tasksError.value) return '生成任务状态暂时不可用'
  if (activeVisualTaskCount.value > 0) return '图片和视频正在生成'
  if (failedVisualTasks.value.length > 0) return '生成任务失败，当前没有图片或视频产物'
  if (succeededVisualTasksWithoutArtifact.value.length > 0) return '任务已完成，但产物内容不可读取'
  return '当前项目还没有图片或视频产物'
})
const visualStatusDescription = computed(() => {
  if (visualArtifactCount.value > 0) return `已找到 ${visualArtifactCount.value} 个可预览的图片/视频文件；点击左侧条目即可在右侧打开。`
  if (loadingTasks.value) return '正在读取任务状态。页面会区分生成中、失败和存储异常，不会把它们误报为浏览器故障。'
  if (tasksError.value) return '无法读取任务状态，因此暂时不能判断是尚未生成还是生成失败；请打开生产任务查看。'
  if (activeVisualTaskCount.value > 0) return `当前有 ${activeVisualTaskCount.value} 个图片/视频任务处理中，完成后刷新本页即可预览。`
  if (failedVisualTasks.value.length > 0) {
    const firstGroup = visualFailureGroups.value[0]
    const reason = firstGroup ? `${formatTaskKind(firstGroup.kind)} ${firstGroup.count} 个：${firstGroup.message}` : '请查看任务详情。'
    return `这不是浏览器预览故障。${reason} 可前往生产任务重试，历史失败记录会保留。`
  }
  if (succeededVisualTasksWithoutArtifact.value.length > 0) return '任务状态显示成功，但没有可读取的二进制 Artifact；请检查 Worker 和对象存储。'
  return projectId.value ? '请先完成分镜、参考图或视频片段任务；结构化文件和音频不会自动变成图片/视频。' : '选择一个项目可查看它的生成状态；全局列表会显示所有可预览产物。'
})
const emptyStateTitle = computed(() => {
  if (artifacts.value.length > 0) return artifactType.value === 'all' ? '没有匹配的资产' : `暂无${artifactFilterLabels[artifactType.value] ?? '该类型'}产物`
  if (loadingTasks.value) return '正在同步任务状态'
  if (tasksError.value) return '暂时无法判断产物状态'
  if (activeVisualTaskCount.value > 0) return '图片或视频正在生成'
  if (failedVisualTasks.value.length > 0) return '图片或视频任务失败'
  return projectId.value ? '该项目暂无可预览产物' : '暂无可预览资产'
})
const emptyStateDescription = computed(() => {
  if (artifacts.value.length > 0) return searchQuery.value.trim() ? '尝试更换搜索词或清空搜索。' : '当前筛选条件下没有结果，请切换资产类型。'
  return visualStatusDescription.value
})
const selectedIdentityAudit = computed<Record<string, unknown> | null>(() => {
  const value = selectedArtifact.value?.metadata.identity_audit
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null
  return value as Record<string, unknown>
})
const selectedIdentityStatus = computed(() => String(selectedIdentityAudit.value?.status ?? 'unknown'))

function displayError(error: unknown) {
  if (error instanceof ApiClientError) return `${error.message} · ${error.code}`
  return '媒体资产暂时无法读取，请稍后重试。'
}

function isVisualArtifact(artifact: ArtifactRecord) {
  return isVideoArtifact(artifact) || artifact.type === 'reference_image'
}

function isDocumentArtifact(artifact: ArtifactRecord) {
  return !isVisualArtifact(artifact) && !isAudioArtifact(artifact)
}

function matchesArtifactFilter(artifact: ArtifactRecord, filter: ArtifactFilter) {
  if (filter === 'all') return true
  if (filter === 'rendered_video') return artifact.type === 'rendered_video'
  if (filter === 'video') return artifact.type === 'video_clip' || artifact.type === 'lip_synced_video'
  if (filter === 'reference_image') return artifact.type === 'reference_image'
  if (filter === 'audio') return isAudioArtifact(artifact)
  if (filter === 'subtitle') return artifact.type === 'subtitle_srt'
  return isDocumentArtifact(artifact)
}

function isVisualTaskKind(kind: string) {
  return isMediaTaskKind(kind)
}

function metadataText(artifact: ArtifactRecord, key: string, fallback = '—') {
  const value = artifact.metadata[key]
  return value === undefined || value === null || value === '' ? fallback : String(value)
}

function isVideoArtifact(artifact: ArtifactRecord) {
  return artifact.type === 'rendered_video' || artifact.type === 'video_clip' || artifact.type === 'lip_synced_video'
}

function isAudioArtifact(artifact: ArtifactRecord) {
  return artifact.type === 'audio_narration' || artifact.type === 'audio_bgm'
}

function setArtifactFilter(filter: ArtifactFilter) {
  artifactType.value = filter
}

function artifactRowMeta(artifact: ArtifactRecord) {
  if (isVideoArtifact(artifact) || isAudioArtifact(artifact)) return formatDuration(artifact.metadata.duration_seconds)
  return formatTime(artifact.created_at)
}

function artifactStatusLabel(artifact: ArtifactRecord) {
  if (!artifact.metadata.storage_key) return '已登记 · 待读取'
  if (isVisualArtifact(artifact)) return '已生成 · 可预览'
  return '已登记'
}

function formatBytes(value: unknown) {
  const size = Number(value)
  if (!Number.isFinite(size) || size < 0) return '—'
  if (size < 1024) return `${Math.round(size)} B`
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`
  if (size < 1024 * 1024 * 1024) return `${(size / (1024 * 1024)).toFixed(1)} MB`
  return `${(size / (1024 * 1024 * 1024)).toFixed(1)} GB`
}

function formatDuration(value: unknown) {
  const seconds = Number(value)
  return Number.isFinite(seconds) && seconds > 0 ? `${seconds.toFixed(1)} 秒` : '—'
}

function identityAuditLabel(status: string) {
  const labels: Record<string, string> = {
    passed: '自动初审通过',
    failed: '相似度未达标',
    not_applicable: '无需人脸审核',
    unavailable: '等待人工审核',
    no_face: '未检测到人脸',
    reference_no_face: '标准图无人脸',
    error: '审核执行异常',
    unknown: '未生成审核结果',
  }
  return labels[status] ?? '需人工确认'
}

function identityAuditClass(status: string) {
  if (status === 'passed') return 'passed'
  if (status === 'not_applicable') return 'not-applicable'
  return 'needs-review'
}

function identityAuditMessage(status: string) {
  if (status === 'passed') return '抽样帧达到配置阈值；仍建议人工快速看片，确认没有身份漂移或局部变脸。'
  if (status === 'not_applicable') return '本镜头没有角色资产，因此不执行人脸身份比对。'
  return '自动检查无法确认身份，必须打开视频人工判断后再决定是否进入成片。'
}

function identityAuditMetric(key: string) {
  const value = selectedIdentityAudit.value?.[key]
  return typeof value === 'number' ? value.toFixed(3) : '—'
}

function identityAuditText(key: string) {
  const value = selectedIdentityAudit.value?.[key]
  return value === undefined || value === null || value === '' ? '—' : String(value)
}

function formatTime(value: string) {
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
}

function selectArtifact(artifactId: string) {
  selectedArtifactId.value = artifactId
}

function preferredArtifactId(items: ArtifactRecord[]): string | null {
  const preferredTypes = ['rendered_video', 'lip_synced_video', 'video_clip', 'reference_image', 'audio_narration', 'audio_bgm']
  return preferredTypes.reduce<string | null>((selected, type) => selected ?? items.find((artifact) => artifact.type === type)?.id ?? null, null)
    ?? items[0]?.id
    ?? null
}

async function loadProjects() {
  loadingProjects.value = true
  try {
    projects.value = await getNovelProjects()
  } catch (error) {
    errorMessage.value = displayError(error)
  } finally {
    loadingProjects.value = false
  }
}

async function loadArtifacts() {
  loading.value = true
  loadingTasks.value = true
  errorMessage.value = null
  tasksError.value = null
  selectedArtifactId.value = null
  try {
    const [artifactResult, taskResult] = await Promise.allSettled([
      getArtifacts({
        projectId: projectId.value || undefined,
        limit: 200,
        expiresInSeconds: 3600,
      }),
      getTasks({ projectId: projectId.value || undefined, limit: 200 }),
    ])
    if (artifactResult.status === 'rejected') throw artifactResult.reason
    artifacts.value = artifactResult.value.items
    if (taskResult.status === 'fulfilled') tasks.value = taskResult.value.items
    else {
      tasks.value = []
      tasksError.value = displayError(taskResult.reason)
    }
    const initialArtifactId = preferredArtifactId(filteredArtifacts.value)
    if (initialArtifactId) selectArtifact(initialArtifactId)
  } catch (error) {
    artifacts.value = []
    tasks.value = []
    errorMessage.value = displayError(error)
  } finally {
    loading.value = false
    loadingTasks.value = false
  }
}

watch(projectId, loadArtifacts)
watch(filteredArtifacts, (items) => {
  if (!items.some((artifact) => artifact.id === selectedArtifactId.value)) {
    const initialArtifactId = preferredArtifactId(items)
    selectedArtifactId.value = initialArtifactId
  }
})
onMounted(async () => {
  await loadProjects()
  await loadArtifacts()
})
</script>

<template>
  <section class="page-header">
    <div>
      <p class="page-kicker">MEDIA / ARTIFACT LIBRARY</p>
      <h1>媒体资产</h1>
      <p>集中查看生产结果。图片和视频会直接显示缩略图，点击后可播放、下载并检查生成信息。</p>
    </div>
    <button class="primary-button" type="button" @click="loadArtifacts">刷新资产</button>
  </section>

  <section class="card artifact-toolbar">
    <label class="form-field"><span>小说项目</span><select v-model="projectId" :disabled="loadingProjects"><option value="">全部项目</option><option v-for="project in projects" :key="project.id" :value="project.id">{{ project.title }}</option></select></label>
    <label class="form-field"><span>资产筛选</span><select v-model="artifactType"><option v-for="filter in artifactFilterOptions" :key="filter.key" :value="filter.key">{{ filter.label }} · {{ filter.count }}</option></select></label>
    <label class="form-field artifact-search-field"><span>搜索资产</span><input v-model="searchQuery" type="search" placeholder="类型、Provider 或 Artifact ID" /></label>
    <div class="artifact-toolbar-note"><span class="note-icon">✓</span><span>共 {{ artifacts.length }} 个 Artifact · 视频 {{ artifactCounts.video }} · 图片 {{ artifactCounts.image }} · 音频 {{ artifactCounts.audio }}；预览走授权内容接口，不暴露磁盘路径。</span></div>
    <div class="artifact-filter-strip" role="toolbar" aria-label="快速筛选资产">
      <button v-for="filter in artifactFilterOptions" :key="filter.key" type="button" :class="{ active: artifactType === filter.key }" @click="setArtifactFilter(filter.key)">{{ filter.label }} <b>{{ filter.count }}</b></button>
    </div>
  </section>

  <div v-if="errorMessage" class="alert-card error-card"><div><strong>媒体资产读取失败</strong><p>{{ errorMessage }}</p></div><button class="secondary-button" type="button" @click="loadArtifacts">重试</button></div>

  <section v-if="projectId" class="artifact-task-status" :class="`is-${visualStatusTone}`" aria-live="polite">
    <div class="artifact-task-status-copy"><span class="artifact-task-status-icon">{{ visualStatusTone === 'ready' ? '✓' : visualStatusTone === 'working' ? '↻' : visualStatusTone === 'attention' ? '!' : 'i' }}</span><div><strong>{{ selectedProject?.title || '当前项目' }} · {{ visualStatusTitle }}</strong><p>{{ visualStatusDescription }}</p></div></div>
    <div class="artifact-task-status-metrics"><span>图片/视频 <b>{{ visualArtifactCount }}</b></span><span>处理中 <b>{{ activeVisualTaskCount }}</b></span><span>失败 <b>{{ failedVisualTasks.length }}</b></span></div>
    <button v-if="visualStatusTone !== 'ready' && !tasksError" class="artifact-task-status-action" type="button" @click="emit('openTasks')">查看生产任务 <span>↗</span></button>
    <button v-else-if="visualStatusTone === 'ready'" class="artifact-task-status-action secondary" type="button" @click="artifactType = 'reference_image'">只看参考图 <span>→</span></button>
  </section>
  <div v-if="tasksError" class="artifact-task-status-error"><strong>任务状态读取失败</strong><span>{{ tasksError }}</span><button type="button" @click="loadArtifacts">重新读取</button></div>

  <section class="artifact-workspace">
    <article class="card artifact-list-card">
      <div class="card-header table-heading"><div><h2>资产列表</h2><p>{{ filteredArtifacts.length }} 个结果<span v-if="filteredArtifacts.length !== artifacts.length"> · 共 {{ artifacts.length }} 个</span> · 选择一项查看详情</p></div><span class="table-count">{{ loading ? '读取中' : `${filteredArtifacts.length} 个` }}</span></div>
      <div v-if="loading" class="task-empty"><span class="spinner" />正在读取媒体资产…</div>
      <div v-else-if="filteredArtifacts.length === 0" class="task-empty"><strong>{{ emptyStateTitle }}</strong><span>{{ emptyStateDescription }}</span><button v-if="artifacts.length && artifactType !== 'all'" class="creator-small-button" type="button" @click="artifactType = 'all'">显示全部类型</button><button v-else-if="projectId && !tasksError" class="creator-small-button" type="button" @click="emit('openTasks')">前往生产任务</button></div>
      <div v-else class="artifact-list">
        <button v-for="artifact in filteredArtifacts" :key="artifact.id" class="artifact-list-row" :class="{ selected: selectedArtifactId === artifact.id }" type="button" :aria-label="`${typeLabels[artifact.type] || artifact.type}，${artifactStatusLabel(artifact)}，点击查看详情`" @click="selectArtifact(artifact.id)">
          <span class="artifact-list-thumb" :class="artifact.type"><MediaPreview :artifact="artifact" variant="thumb" :controls="false" :lazy="false" :alt="`${typeLabels[artifact.type] || artifact.type}缩略图`" /><span v-if="isVideoArtifact(artifact)" class="artifact-list-play" aria-hidden="true">▶</span><span v-else-if="isDocumentArtifact(artifact)" class="artifact-list-file-mark" aria-hidden="true">文</span></span>
          <span class="artifact-list-copy"><strong>{{ typeLabels[artifact.type] || artifact.type }}</strong><small>{{ artifact.provider }} · {{ artifactRowMeta(artifact) }}</small><em>{{ artifactStatusLabel(artifact) }}</em></span>
          <span class="artifact-list-arrow">›</span>
        </button>
      </div>
    </article>

    <article class="card artifact-preview-card">
      <div v-if="selectedArtifact" class="artifact-preview-content">
        <div class="artifact-preview-heading"><div><span class="page-kicker">SELECTED ARTIFACT</span><h2>{{ typeLabels[selectedArtifact.type] || selectedArtifact.type }}</h2><p>{{ selectedArtifact.provider }} · {{ selectedArtifact.id }}</p></div><span class="configured-tag">{{ artifactStatusLabel(selectedArtifact).split(' · ')[0] }}</span></div>
        <div class="media-preview-box"><MediaPreview :artifact="selectedArtifact" variant="panel" :alt="`${typeLabels[selectedArtifact.type] || selectedArtifact.type}预览`" show-download /></div>
        <div v-if="selectedIdentityAudit" class="artifact-identity-audit" :class="identityAuditClass(selectedIdentityStatus)">
          <div class="artifact-identity-audit-heading"><div><strong>角色身份一致性初审</strong><span>InsightFace 抽样结果 · 不是最终人工验收</span></div><span class="identity-audit-pill" :class="identityAuditClass(selectedIdentityStatus)">{{ identityAuditLabel(selectedIdentityStatus) }}</span></div>
          <p>{{ identityAuditMessage(selectedIdentityStatus) }}</p>
          <div v-if="selectedIdentityStatus !== 'not_applicable'" class="artifact-identity-audit-metrics"><span>最低相似度 <strong>{{ identityAuditMetric('min_similarity') }}</strong></span><span>平均相似度 <strong>{{ identityAuditMetric('mean_similarity') }}</strong></span><span>抽样帧 <strong>{{ identityAuditText('sampled_frame_count') }}</strong></span></div>
        </div>
        <dl class="artifact-meta-grid"><div><dt>项目 ID</dt><dd>{{ selectedArtifact.project_id }}</dd></div><div><dt>文件类型</dt><dd>{{ metadataText(selectedArtifact, 'content_type', selectedArtifact.type) }}</dd></div><div><dt>时长</dt><dd>{{ formatDuration(selectedArtifact.metadata.duration_seconds) }}</dd></div><div><dt>文件大小</dt><dd>{{ formatBytes(selectedArtifact.metadata.size_bytes) }}</dd></div><div><dt>SHA-256</dt><dd>{{ metadataText(selectedArtifact, 'sha256') }}</dd></div><div><dt>生成时间</dt><dd>{{ formatTime(selectedArtifact.created_at) }}</dd></div></dl>
        <div v-if="selectedArtifact.metadata.quality" class="artifact-quality-note"><strong>质量复核 metadata</strong><span>{{ JSON.stringify(selectedArtifact.metadata.quality) }}</span></div>
        <div class="artifact-actions"><span class="field-hint">图片和视频支持直接预览；音频可播放，结构化文件请使用上方下载链接查看。</span></div>
      </div>
      <div v-else class="task-empty"><strong>选择一个资产开始查看</strong><span>图片和视频会在这里播放，下面同时显示文件状态和生成信息。</span></div>
    </article>
  </section>
</template>

<style scoped>
.artifact-task-status { display: flex; align-items: center; justify-content: space-between; gap: 16px; margin: -4px 0 18px; border: 1px solid #dfe1fa; border-radius: 11px; padding: 13px 15px; background: linear-gradient(135deg, #f7f7ff, #fff 72%); }
.artifact-task-status.is-ready { border-color: #d8eee1; background: linear-gradient(135deg, #f4fbf7, #fff 72%); }
.artifact-task-status.is-attention { border-color: #f1d5d9; background: linear-gradient(135deg, #fff6f7, #fff 72%); }
.artifact-task-status.is-working { border-color: #e3e2fb; background: linear-gradient(135deg, #f8f7ff, #fff 72%); }
.artifact-task-status-copy { display: flex; align-items: flex-start; gap: 10px; min-width: 0; flex: 1; }
.artifact-task-status-icon { display: grid; place-items: center; flex: 0 0 27px; width: 27px; height: 27px; border-radius: 8px; color: #6264d9; background: #e9eaff; font-size: 12px; font-weight: 800; }
.artifact-task-status.is-ready .artifact-task-status-icon { color: #2f956a; background: #def4e8; }
.artifact-task-status.is-attention .artifact-task-status-icon { color: #c25763; background: #ffe7ea; }
.artifact-task-status-copy strong, .artifact-task-status-copy p { display: block; }
.artifact-task-status-copy strong { color: #4a546c; font-size: 10px; }
.artifact-task-status-copy p { margin: 4px 0 0; color: #8992a5; font-size: 9px; line-height: 1.5; }
.artifact-task-status-metrics { display: flex; flex-wrap: wrap; gap: 6px; flex: 0 0 auto; }
.artifact-task-status-metrics span { border-radius: 999px; padding: 5px 7px; color: #8a93a6; background: rgba(255,255,255,.8); font-size: 8px; white-space: nowrap; }
.artifact-task-status-metrics b { margin-left: 3px; color: #566078; font-size: 9px; }
.artifact-task-status-action { flex: 0 0 auto; border: 1px solid #cfd1f3; border-radius: 7px; padding: 8px 10px; color: #5d60cd; background: #fff; font-size: 9px; font-weight: 700; white-space: nowrap; }
.artifact-task-status-action:hover { border-color: #9fa2e8; background: #f8f8ff; }
.artifact-task-status-action.secondary { border-color: #d9e9df; color: #398767; }
.artifact-task-status-error { display: flex; align-items: center; gap: 9px; margin: -4px 0 18px; border: 1px solid #f1d5d9; border-radius: 8px; padding: 9px 11px; color: #b14e5c; background: #fff5f6; font-size: 9px; }
.artifact-task-status-error strong { flex: 0 0 auto; }
.artifact-task-status-error span { min-width: 0; flex: 1; }
.artifact-task-status-error button { flex: 0 0 auto; border: 0; color: #b14e5c; background: transparent; font-size: 9px; font-weight: 700; }
.artifact-filter-strip { grid-column: 1 / -1; display: flex; flex-wrap: wrap; gap: 6px; border-top: 1px solid var(--border-soft); padding-top: 12px; }
.artifact-filter-strip button { border: 1px solid #e1e4ee; border-radius: 999px; padding: 6px 9px; color: #7b8498; background: #fff; font-size: 9px; font-weight: 700; transition: 150ms ease; }
.artifact-filter-strip button:hover, .artifact-filter-strip button.active { border-color: #a8aae8; color: #5d60cd; background: #f4f4ff; }
.artifact-filter-strip button b { margin-left: 3px; color: #a3aabd; font-size: 8px; }
.artifact-filter-strip button.active b { color: #777bd5; }
.artifact-list-thumb { position: relative; display: grid; place-items: center; flex: 0 0 58px; width: 58px; height: 44px; overflow: hidden; border: 1px solid #e8eaf2; border-radius: 9px; background: #f6f7fb; }
.artifact-list-thumb img, .artifact-list-thumb video { display: block; width: 100%; height: 100%; object-fit: cover; }
.artifact-list-thumb.video_clip, .artifact-list-thumb.lip_synced_video, .artifact-list-thumb.rendered_video { background: #eef0ff; }
.artifact-list-thumb.audio_narration, .artifact-list-thumb.audio_bgm { background: #eefaf4; }
.artifact-list-thumb.reference_image { background: #fff8e9; }
.artifact-list-play { position: absolute; right: 5px; bottom: 4px; display: grid; place-items: center; width: 17px; height: 17px; border: 1px solid rgba(255,255,255,.8); border-radius: 50%; color: #fff; background: rgba(30,35,65,.7); font-size: 7px; pointer-events: none; }
.artifact-list-file-mark { position: absolute; right: 4px; bottom: 3px; border-radius: 4px; padding: 3px 4px; color: #fff; background: rgba(78,87,112,.72); font-size: 7px; pointer-events: none; }
.artifact-identity-audit { display: grid; gap: 8px; margin: 14px 0; border: 1px solid #efd2a5; border-radius: 10px; padding: 12px 13px; color: #805817; background: #fffaf0; font-size: 10px; line-height: 1.5; }
.artifact-identity-audit.passed { border-color: #b9e3cf; color: #246746; background: #f2fbf6; }
.artifact-identity-audit.not-applicable { border-color: #d8deea; color: var(--text-muted); background: #f8f9fc; }
.artifact-identity-audit-heading { display: flex; align-items: center; justify-content: space-between; gap: 10px; }
.artifact-identity-audit-heading > div { display: grid; gap: 2px; }
.artifact-identity-audit-heading strong { color: var(--text); font-size: 11px; }
.artifact-identity-audit-heading span { font-size: 9px; }
.identity-audit-pill { flex: 0 0 auto; border-radius: 999px; padding: 4px 8px; color: #8a5a0a; background: #ffedc7; font-size: 9px; font-weight: 700; }
.identity-audit-pill.passed { color: #236745; background: #dff5e9; }
.identity-audit-pill.not-applicable { color: #5f6a7d; background: #e9edf5; }
.artifact-identity-audit p { margin: 0; }
.artifact-identity-audit-metrics { display: flex; flex-wrap: wrap; gap: 7px; }
.artifact-identity-audit-metrics span { border-radius: 999px; padding: 3px 7px; color: inherit; background: rgba(255, 255, 255, .72); }
.artifact-identity-audit-metrics strong { color: var(--text); }
.artifact-list-copy em { display: block; overflow: hidden; margin-top: 4px; color: #8b94a7; font-size: 8px; font-style: normal; text-overflow: ellipsis; white-space: nowrap; }
.artifact-list-row.selected .artifact-list-copy em { color: #6b70c9; }
@media (max-width: 720px) {
  .artifact-task-status { align-items: stretch; flex-direction: column; }
  .artifact-task-status-metrics { flex-basis: auto; }
  .artifact-task-status-action { width: 100%; }
  .artifact-task-status-error { align-items: flex-start; flex-wrap: wrap; }
  .artifact-task-status-error span { flex-basis: 100%; }
  .artifact-task-status-error button { margin-left: auto; }
  .artifact-filter-strip { padding-top: 10px; }
}
</style>

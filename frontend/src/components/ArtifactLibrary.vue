<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { ApiClientError } from '../api/client'
import { getNovelProjects } from '../api/novels'
import { getArtifacts } from '../api/tasks'
import type { ArtifactRecord, NovelProjectRecord } from '../types/task'
import MediaPreview from './MediaPreview.vue'

const projects = ref<NovelProjectRecord[]>([])
const artifacts = ref<ArtifactRecord[]>([])
const selectedArtifactId = ref<string | null>(null)
const projectId = ref('')
const artifactType = ref('all')
const searchQuery = ref('')
const loading = ref(false)
const loadingProjects = ref(false)
const errorMessage = ref<string | null>(null)

const typeLabels: Record<string, string> = {
  all: '全部类型', rendered_video: '成片视频', video_clip: '视频片段', lip_synced_video: '唇形同步视频', audio_narration: '旁白音频', audio_bgm: 'BGM 音频', subtitle_srt: 'SRT 字幕', reference_image: '参考图', script_json: '脚本 JSON', story_bible_json: 'StoryBible', episode_outline_json: '分集大纲', episode_script_json: '分场剧本', shot_list_json: '分镜 JSON',
}
const selectedArtifact = computed(() => artifacts.value.find((item) => item.id === selectedArtifactId.value) ?? null)
const filteredArtifacts = computed(() => {
  const query = searchQuery.value.trim().toLowerCase()
  if (!query) return artifacts.value
  return artifacts.value.filter((artifact) => [
    typeLabels[artifact.type] ?? artifact.type,
    artifact.type,
    artifact.provider,
    artifact.id,
    String(artifact.metadata.storage_key ?? ''),
  ].join(' ').toLowerCase().includes(query))
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
  errorMessage.value = null
  selectedArtifactId.value = null
  try {
    const response = await getArtifacts({
      projectId: projectId.value || undefined,
      type: artifactType.value === 'all' ? undefined : artifactType.value,
      limit: 200,
      expiresInSeconds: 3600,
    })
    artifacts.value = response.items
    if (response.items[0]) selectArtifact(response.items[0].id)
  } catch (error) {
    artifacts.value = []
    errorMessage.value = displayError(error)
  } finally {
    loading.value = false
  }
}

watch([projectId, artifactType], loadArtifacts)
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
      <p>集中预览和下载已完成的成片、视频片段、音频、字幕与参考图。所有文件都来自服务端 Artifact Registry。</p>
    </div>
    <button class="primary-button" type="button" @click="loadArtifacts">刷新资产</button>
  </section>

  <section class="card artifact-toolbar">
    <label class="form-field"><span>小说项目</span><select v-model="projectId" :disabled="loadingProjects"><option value="">全部项目</option><option v-for="project in projects" :key="project.id" :value="project.id">{{ project.title }}</option></select></label>
    <label class="form-field"><span>资产类型</span><select v-model="artifactType"><option v-for="(label, value) in typeLabels" :key="value" :value="value">{{ label }}</option></select></label>
    <label class="form-field artifact-search-field"><span>搜索资产</span><input v-model="searchQuery" type="search" placeholder="类型、Provider 或 Artifact ID" /></label>
    <div class="artifact-toolbar-note"><span class="note-icon">✓</span><span>预览优先走同源授权接口，签名链接仅作为回退；不会暴露磁盘路径。</span></div>
  </section>

  <div v-if="errorMessage" class="alert-card error-card"><div><strong>媒体资产读取失败</strong><p>{{ errorMessage }}</p></div><button class="secondary-button" type="button" @click="loadArtifacts">重试</button></div>

  <section class="artifact-workspace">
    <article class="card artifact-list-card">
      <div class="card-header table-heading"><div><h2>资产列表</h2><p>{{ filteredArtifacts.length }} 个结果<span v-if="filteredArtifacts.length !== artifacts.length"> · 共 {{ artifacts.length }} 个</span>，点击查看详情</p></div><span class="table-count">已加载</span></div>
      <div v-if="loading" class="task-empty"><span class="spinner" />正在读取媒体资产…</div>
      <div v-else-if="filteredArtifacts.length === 0" class="task-empty"><strong>{{ artifacts.length ? '没有匹配的资产' : '暂无可预览资产' }}</strong><span>{{ artifacts.length ? '尝试更换搜索词或筛选条件。' : '完成视频、音频或参考图任务后，产物会出现在这里。' }}</span></div>
      <div v-else class="artifact-list">
        <button v-for="artifact in filteredArtifacts" :key="artifact.id" class="artifact-list-row" :class="{ selected: selectedArtifactId === artifact.id }" type="button" @click="selectArtifact(artifact.id)">
          <span class="artifact-list-thumb" :class="artifact.type"><MediaPreview :artifact="artifact" variant="thumb" :controls="false" /></span>
          <span class="artifact-list-copy"><strong>{{ typeLabels[artifact.type] || artifact.type }}</strong><small>{{ artifact.provider }} · {{ isVideoArtifact(artifact) || isAudioArtifact(artifact) ? formatDuration(artifact.metadata.duration_seconds) : formatTime(artifact.created_at) }}</small></span>
          <span class="artifact-list-arrow">›</span>
        </button>
      </div>
    </article>

    <article class="card artifact-preview-card">
      <div v-if="selectedArtifact" class="artifact-preview-content">
        <div class="artifact-preview-heading"><div><span class="page-kicker">SELECTED ARTIFACT</span><h2>{{ typeLabels[selectedArtifact.type] || selectedArtifact.type }}</h2><p>{{ selectedArtifact.provider }} · {{ selectedArtifact.id }}</p></div><span class="configured-tag">已完成</span></div>
        <div class="media-preview-box"><MediaPreview :artifact="selectedArtifact" variant="panel" :alt="`${typeLabels[selectedArtifact.type] || selectedArtifact.type}预览`" show-download /></div>
        <div v-if="selectedIdentityAudit" class="artifact-identity-audit" :class="identityAuditClass(selectedIdentityStatus)">
          <div class="artifact-identity-audit-heading"><div><strong>角色身份一致性初审</strong><span>InsightFace 抽样结果 · 不是最终人工验收</span></div><span class="identity-audit-pill" :class="identityAuditClass(selectedIdentityStatus)">{{ identityAuditLabel(selectedIdentityStatus) }}</span></div>
          <p>{{ identityAuditMessage(selectedIdentityStatus) }}</p>
          <div v-if="selectedIdentityStatus !== 'not_applicable'" class="artifact-identity-audit-metrics"><span>最低相似度 <strong>{{ identityAuditMetric('min_similarity') }}</strong></span><span>平均相似度 <strong>{{ identityAuditMetric('mean_similarity') }}</strong></span><span>抽样帧 <strong>{{ identityAuditText('sampled_frame_count') }}</strong></span></div>
        </div>
        <dl class="artifact-meta-grid"><div><dt>项目 ID</dt><dd>{{ selectedArtifact.project_id }}</dd></div><div><dt>文件类型</dt><dd>{{ metadataText(selectedArtifact, 'content_type', selectedArtifact.type) }}</dd></div><div><dt>时长</dt><dd>{{ formatDuration(selectedArtifact.metadata.duration_seconds) }}</dd></div><div><dt>文件大小</dt><dd>{{ formatBytes(selectedArtifact.metadata.size_bytes) }}</dd></div><div><dt>SHA-256</dt><dd>{{ metadataText(selectedArtifact, 'sha256') }}</dd></div><div><dt>生成时间</dt><dd>{{ formatTime(selectedArtifact.created_at) }}</dd></div></dl>
        <div v-if="selectedArtifact.metadata.quality" class="artifact-quality-note"><strong>质量复核 metadata</strong><span>{{ JSON.stringify(selectedArtifact.metadata.quality) }}</span></div>
        <div class="artifact-actions"><span class="field-hint">预览与下载均经过项目权限校验；结构化文件请在上方下载后查看。</span></div>
      </div>
      <div v-else class="task-empty"><strong>选择一个 Artifact</strong><span>右侧会显示可用的预览和安全 metadata。</span></div>
    </article>
  </section>
</template>

<style scoped>
.artifact-list-thumb { display: grid; place-items: center; flex: 0 0 42px; width: 42px; height: 42px; overflow: hidden; border: 1px solid #e8eaf2; border-radius: 9px; background: #f6f7fb; }
.artifact-list-thumb img, .artifact-list-thumb video { display: block; width: 100%; height: 100%; object-fit: cover; }
.artifact-list-thumb.video_clip, .artifact-list-thumb.lip_synced_video, .artifact-list-thumb.rendered_video { background: #eef0ff; }
.artifact-list-thumb.audio_narration, .artifact-list-thumb.audio_bgm { background: #eefaf4; }
.artifact-list-thumb.reference_image { background: #fff8e9; }
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
</style>

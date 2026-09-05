<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { ApiClientError } from '../api/client'
import { getEpisodes, getNovelProjects } from '../api/novels'
import { getArtifacts } from '../api/tasks'
import { createSubtitleASRTask, createSubtitleAlignmentTask } from '../api/subtitles'
import { useProviderProfiles } from '../composables/useProviderProfiles'
import type { ArtifactRecord, EpisodeRecord, NovelProjectRecord, GenerationTaskRecord } from '../types/task'

type SubtitleMode = 'asr' | 'align'

const emit = defineEmits<{ submitted: [task: GenerationTaskRecord] }>()

const { profiles, availableProfiles, selectedProfileId, isLoading: profilesLoading } = useProviderProfiles()
const projects = ref<NovelProjectRecord[]>([])
const episodes = ref<EpisodeRecord[]>([])
const audioArtifacts = ref<ArtifactRecord[]>([])
const projectId = ref('')
const episodeId = ref('')
const audioArtifactId = ref('')
const mode = ref<SubtitleMode>('asr')
const language = ref('zh-CN')
const referenceText = ref('')
const alignmentText = ref('')
const alignmentDuration = ref('')
const loadingProjects = ref(false)
const loadingResources = ref(false)
const submitting = ref(false)
const submitError = ref<string | null>(null)
const submittedTask = ref<GenerationTaskRecord | null>(null)

const selectedArtifact = computed(() => audioArtifacts.value.find((item) => item.id === audioArtifactId.value) ?? null)
const effectiveDuration = computed(() => {
  const metadataDuration = selectedArtifact.value?.metadata.duration_seconds
  return typeof metadataDuration === 'number' ? metadataDuration : Number(alignmentDuration.value)
})
const canSubmit = computed(() => {
  if (!episodeId.value || submitting.value) return false
  if (mode.value === 'asr') return Boolean(audioArtifactId.value && selectedProfileId.value)
  return Boolean(alignmentText.value.trim() && effectiveDuration.value > 0)
})

function displayError(error: unknown): string {
  if (error instanceof ApiClientError) return `${error.message} · ${error.code}`
  return '请求失败，请检查 API、Worker 和输入资源。'
}

async function loadProjects() {
  loadingProjects.value = true
  try {
    projects.value = await getNovelProjects()
    if (!projectId.value && projects.value.length > 0) projectId.value = projects.value[0].id
  } catch (error) {
    submitError.value = displayError(error)
  } finally {
    loadingProjects.value = false
  }
}

async function loadProjectResources() {
  episodes.value = []
  audioArtifacts.value = []
  episodeId.value = ''
  audioArtifactId.value = ''
  if (!projectId.value) return
  loadingResources.value = true
  try {
    const [episodeItems, artifactResponse] = await Promise.all([
      getEpisodes(projectId.value),
      getArtifacts({ projectId: projectId.value, type: 'audio_narration' }),
    ])
    episodes.value = episodeItems
    audioArtifacts.value = artifactResponse.items
    episodeId.value = episodeItems[0]?.id ?? ''
    audioArtifactId.value = artifactResponse.items[0]?.id ?? ''
  } catch (error) {
    submitError.value = displayError(error)
  } finally {
    loadingResources.value = false
  }
}

async function submit() {
  if (!canSubmit.value) return
  submitting.value = true
  submitError.value = null
  submittedTask.value = null
  try {
    const task = mode.value === 'asr'
      ? await createSubtitleASRTask(episodeId.value, {
          audio_artifact_id: audioArtifactId.value,
          language: language.value,
          reference_text: referenceText.value.trim() || undefined,
          provider_profile_id: selectedProfileId.value ?? undefined,
        }, crypto.randomUUID())
      : await createSubtitleAlignmentTask(episodeId.value, {
          text: alignmentText.value.trim(),
          language: language.value,
          audio_duration_seconds: effectiveDuration.value,
        }, crypto.randomUUID())
    submittedTask.value = task
    emit('submitted', task)
  } catch (error) {
    submitError.value = displayError(error)
  } finally {
    submitting.value = false
  }
}

watch(projectId, loadProjectResources)
watch(mode, () => {
  submitError.value = null
  submittedTask.value = null
})

onMounted(loadProjects)
</script>

<template>
  <section class="page-header">
    <div>
      <p class="page-kicker">PRODUCTION / SUBTITLES</p>
      <h1>字幕任务</h1>
      <p>从已生成的旁白 Artifact 创建 ASR 或句子级对齐任务，Worker 会异步处理并保留可追踪结果。</p>
    </div>
    <span class="workspace-badge">异步任务</span>
  </section>

  <section class="workflow-banner">
    <div class="workflow-step active"><span>1</span><div><strong>选择项目与音频</strong><small>使用已通过 FFprobe 的 audio_narration Artifact</small></div></div>
    <span class="workflow-arrow">→</span>
    <div class="workflow-step"><span>2</span><div><strong>提交识别任务</strong><small>Provider 只由服务端读取密钥</small></div></div>
    <span class="workflow-arrow">→</span>
    <div class="workflow-step"><span>3</span><div><strong>任务中心跟踪</strong><small>完成后生成 subtitle_srt Artifact</small></div></div>
  </section>

  <section class="content-grid subtitle-layout">
    <article class="card subtitle-form-card">
      <div class="card-header">
        <div><h2>创建字幕任务</h2><p>HTTP 请求只负责创建任务，实际处理在 Worker 中完成。</p></div>
        <span class="status-pill" :class="{ neutral: !canSubmit }">{{ canSubmit ? '可提交' : '待补充' }}</span>
      </div>

      <div class="mode-switch" role="tablist" aria-label="字幕生成方式">
        <button type="button" :class="{ active: mode === 'asr' }" @click="mode = 'asr'">真实 ASR 转写</button>
        <button type="button" :class="{ active: mode === 'align' }" @click="mode = 'align'">句子级对齐</button>
      </div>

      <div class="form-grid">
        <label class="form-field form-field-wide"><span>小说项目</span><select v-model="projectId" :disabled="loadingProjects || loadingResources"><option value="">请选择项目</option><option v-for="project in projects" :key="project.id" :value="project.id">{{ project.title }}</option></select></label>
        <label class="form-field"><span>目标分集</span><select v-model="episodeId" :disabled="loadingResources || episodes.length === 0"><option value="">请选择分集</option><option v-for="episode in episodes" :key="episode.id" :value="episode.id">第 {{ episode.episode_number }} 集 · {{ episode.outline.title }}</option></select></label>
        <label class="form-field"><span>语言</span><input v-model="language" maxlength="20" placeholder="zh-CN" /></label>
        <label class="form-field form-field-wide"><span>旁白 Artifact</span><select v-model="audioArtifactId" :disabled="loadingResources || audioArtifacts.length === 0"><option value="">请选择 audio_narration</option><option v-for="artifact in audioArtifacts" :key="artifact.id" :value="artifact.id">{{ artifact.metadata.label || artifact.provider }} · {{ artifact.metadata.duration_seconds ? `${Number(artifact.metadata.duration_seconds).toFixed(1)} 秒` : '已校验音频' }}</option></select><small v-if="loadingResources" class="field-hint">正在读取项目资源…</small><small v-else-if="audioArtifacts.length === 0" class="field-hint">该项目暂无可用旁白 Artifact，请先完成音频任务。</small></label>
      </div>

      <div v-if="mode === 'asr'" class="mode-panel">
        <label class="form-field"><span>ASR Provider</span><select v-model="selectedProfileId" :disabled="profilesLoading"><option v-for="profile in availableProfiles" :key="profile.profile_id" :value="profile.profile_id">{{ profile.label }} · {{ profile.model }}{{ profile.default ? ' · 默认' : '' }}</option></select><small class="field-hint">当前可用：{{ availableProfiles.length }} 个；未配置档案不会出现在提交选项中。</small></label>
        <label class="form-field"><span>参考文本（可选）</span><textarea v-model="referenceText" rows="4" maxlength="5000" placeholder="如果有已审核的旁白文本，可填入用于 CER 和质量复核。" /></label>
      </div>
      <div v-else class="mode-panel">
        <label class="form-field"><span>字幕文本</span><textarea v-model="alignmentText" rows="5" maxlength="5000" placeholder="输入与旁白对应的句子或段落文本，系统按音频时长生成句子级时间轴。" /></label>
        <label class="form-field"><span>音频时长（秒）</span><input v-model="alignmentDuration" :placeholder="selectedArtifact ? '已从 Artifact 读取，可覆盖' : '例如 45.9'" inputmode="decimal" /></label>
      </div>

      <div v-if="submitError" class="inline-error"><strong>提交失败</strong><span>{{ submitError }}</span></div>
      <div v-if="submittedTask" class="inline-success"><strong>任务已创建</strong><span>{{ submittedTask.id }} · 已进入任务中心</span></div>
      <div class="form-footer"><span class="security-note compact"><span class="note-icon">✓</span><span>API Key 只在 API / Worker 环境变量中使用，前端不会接收密钥。</span></span><button class="primary-button" type="button" :disabled="!canSubmit" @click="submit">{{ submitting ? '正在创建…' : '创建字幕任务' }}</button></div>
    </article>

    <article class="card subtitle-guide-card">
      <div class="card-header"><div><h2>运行说明</h2><p>选择不同模式对应不同的质量预期。</p></div><span class="guide-badge">质量边界</span></div>
      <div class="quality-choice" :class="{ selected: mode === 'asr' }"><span class="quality-icon">A</span><div><strong>ASR 转写</strong><p>适合已有音频、需要从语音识别字幕的场景。Alibaba DashScope 可返回句子级时间戳，SiliconFlow 当前是文本级句子估算。</p></div></div>
      <div class="quality-choice" :class="{ selected: mode === 'align' }"><span class="quality-icon">↔</span><div><strong>句子级对齐</strong><p>适合旁白文本已知的场景，按总时长估算句子时间轴；结果必须人工复核，不等同于逐词对齐。</p></div></div>
      <div class="guide-callout"><strong>下一步</strong><p>任务完成后，在任务中心查看 `subtitle_srt` Artifact、质量 metadata 和可用的下载链接。</p></div>
    </article>
  </section>
</template>

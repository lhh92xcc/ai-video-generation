<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { ApiClientError } from '../api/client'
import { createAudioNarrationTask } from '../api/audio'
import {
  calibrateIdentityThresholds,
  createLipSyncTask,
  createVoiceAsset,
  getVoiceAssets,
  retryIdentityFailedVideoClips,
} from '../api/productionQuality'
import { getAssets } from '../api/novelWorkbench'
import type {
  AssetRecord,
  EpisodeScriptRecord,
  IdentityAuditStatus,
  IdentityCalibrationResponse,
  VoiceAssetRecord,
} from '../types/novel'
import type { ArtifactSummary, EpisodeRecord, GenerationTaskRecord } from '../types/task'

const props = defineProps<{
  projectId: string
  episode: EpisodeRecord
  script: EpisodeScriptRecord | null
  audioArtifact: ArtifactSummary | null
  tasks: GenerationTaskRecord[]
}>()

const emit = defineEmits<{ changed: [] }>()

const voiceAssets = ref<VoiceAssetRecord[]>([])
const characterAssets = ref<AssetRecord[]>([])
const loadingAssets = ref(false)
const savingVoiceAsset = ref(false)
const action = ref<string | null>(null)
const errorMessage = ref<string | null>(null)
const successMessage = ref<string | null>(null)

const voiceLabel = ref('')
const voiceProvider = ref('edge_tts')
const voiceModel = ref('default')
const voiceName = ref('zh-CN-YunyangNeural')
const voiceRate = ref('-35%')
const voiceVolume = ref('+0%')
const voiceStyle = ref('')
const characterAssetId = ref('')
const selectedVoiceBySpeaker = ref<Record<string, string>>({})

const thresholdText = ref('0.30, 0.35, 0.40, 0.45, 0.50')
const calibration = ref<IdentityCalibrationResponse | null>(null)
const retryStatuses = ref<IdentityAuditStatus[]>(['failed', 'no_face', 'reference_no_face'])
const retryMaxTasks = ref(20)

const selectedVideoArtifactId = ref('')
const lipSyncFaceRegion = ref<'auto' | 'full_frame'>('auto')
const lipSyncFacePadding = ref(0)

const statusLabels: Record<string, string> = {
  created: '已创建',
  queued: '排队中',
  running: '处理中',
  succeeded: '已完成',
  failed: '失败',
  canceled: '已取消',
}

const identityStatusLabels: Record<IdentityAuditStatus, string> = {
  passed: '通过',
  failed: '相似度未达标',
  no_face: '未检测到人脸',
  reference_no_face: '标准图无人脸',
  unavailable: '审核不可用',
  error: '审核异常',
  not_applicable: '无需审核',
}

const speakers = computed(() => {
  const result: string[] = []
  for (const scene of props.script?.content.scenes ?? []) {
    for (const dialogue of scene.dialogues) {
      if (!result.includes(dialogue.speaker)) result.push(dialogue.speaker)
    }
  }
  return result
})

const dialogueLines = computed(() => (props.script?.content.scenes ?? []).flatMap((scene) => scene.dialogues).map((dialogue, index) => ({
  line_index: index + 1,
  speaker: dialogue.speaker,
  text: dialogue.text,
  voice_asset_id: selectedVoiceBySpeaker.value[dialogue.speaker] || undefined,
  pause_after_seconds: 0.12,
})))

const multiVoiceReady = computed(() => Boolean(
  props.script
  && dialogueLines.value.length > 0
  && speakers.value.every((speaker) => Boolean(selectedVoiceBySpeaker.value[speaker]))
  && !action.value,
))

const videoArtifacts = computed(() => {
  const items: Array<{ artifact: ArtifactSummary; task: GenerationTaskRecord; shotLabel: string }> = []
  for (const task of props.tasks) {
    if (!['video_clip', 'lip_sync'].includes(task.kind) || String(task.input_data.episode_id ?? '') !== props.episode.id) continue
    for (const artifact of task.artifacts) {
      if (!['video_clip', 'lip_synced_video'].includes(artifact.type)) continue
      items.push({
        artifact,
        task,
        shotLabel: task.kind === 'video_clip' && task.input_data.shot_index
          ? `镜头 ${String(task.input_data.shot_index).padStart(2, '0')}`
          : '唇形同步结果',
      })
    }
  }
  return items.filter((item, index, all) => all.findIndex((candidate) => candidate.artifact.id === item.artifact.id) === index)
})

const latestLipSyncTask = computed(() => props.tasks
  .filter((task) => task.kind === 'lip_sync' && String(task.input_data.episode_id ?? '') === props.episode.id)
  .sort((left, right) => right.updated_at.localeCompare(left.updated_at))[0] ?? null)

const identityRetryCount = computed(() => props.tasks.filter((task) => {
  if (task.kind !== 'video_clip' || task.status !== 'succeeded' || String(task.input_data.episode_id ?? '') !== props.episode.id) return false
  return task.artifacts.some((artifact) => {
    const report = artifact.metadata.identity_audit
    return report && typeof report === 'object' && !Array.isArray(report) && retryStatuses.value.includes(String((report as Record<string, unknown>).status) as IdentityAuditStatus)
  })
}).length)

function displayError(error: unknown): string {
  if (error instanceof ApiClientError) return `${error.message} · ${error.code}`
  return '质量工具暂时不可用，请稍后重试。'
}

function idempotencyKey(name: string): string {
  const suffix = globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(16).slice(2)}`
  return `creator:${props.projectId}:${name}:${suffix}`
}

function formatStatus(status: string | undefined): string {
  return statusLabels[status ?? ''] ?? status ?? '未创建'
}

function formatPercent(value: number): string {
  return `${(value * 100).toFixed(1)}%`
}

function parseThresholds(): number[] {
  return [...new Set(thresholdText.value.split(/[,，\s]+/).map(Number).filter((value) => Number.isFinite(value) && value >= -1 && value <= 1))]
    .sort((left, right) => left - right)
}

function applySpeakerDefaults() {
  const next = { ...selectedVoiceBySpeaker.value }
  for (const speaker of speakers.value) {
    const matching = voiceAssets.value.find((asset) => asset.status === 'ready' && asset.label === speaker)
    if (matching && !next[speaker]) next[speaker] = matching.id
  }
  selectedVoiceBySpeaker.value = next
}

async function loadAssets() {
  loadingAssets.value = true
  try {
    const voiceResult = await getVoiceAssets(props.projectId)
    voiceAssets.value = voiceResult
    applySpeakerDefaults()
    try {
      characterAssets.value = await getAssets(props.projectId, 'character')
    } catch {
      characterAssets.value = []
    }
  } catch (error) {
    errorMessage.value = displayError(error)
  } finally {
    loadingAssets.value = false
  }
}

async function saveVoiceProfile() {
  if (!voiceLabel.value.trim() || savingVoiceAsset.value) return
  savingVoiceAsset.value = true
  errorMessage.value = null
  successMessage.value = null
  try {
    const saved = await createVoiceAsset(props.projectId, {
      label: voiceLabel.value.trim(),
      provider: voiceProvider.value,
      model: voiceModel.value.trim() || 'default',
      voice: voiceName.value.trim(),
      rate: voiceRate.value.trim(),
      volume: voiceVolume.value.trim(),
      style: voiceStyle.value.trim(),
      character_asset_id: characterAssetId.value || undefined,
      status: 'ready',
    })
    voiceAssets.value = [saved, ...voiceAssets.value.filter((asset) => asset.id !== saved.id)]
    if (speakers.value.includes(saved.label)) {
      selectedVoiceBySpeaker.value = { ...selectedVoiceBySpeaker.value, [saved.label]: saved.id }
    }
    voiceLabel.value = ''
    voiceStyle.value = ''
    successMessage.value = `声音资产“${saved.label}”已保存，可用于多角色配音。`
    emit('changed')
  } catch (error) {
    errorMessage.value = displayError(error)
  } finally {
    savingVoiceAsset.value = false
  }
}

async function generateMultiVoice() {
  if (!multiVoiceReady.value || !props.episode) return
  action.value = 'multi-voice'
  errorMessage.value = null
  successMessage.value = null
  try {
    await createAudioNarrationTask(
      props.episode.id,
      { text: '', voice_lines: dialogueLines.value },
      idempotencyKey(`multi-voice-${props.episode.id}`),
    )
    successMessage.value = '多角色配音任务已提交；每句声音会按角色资产生成并由 FFmpeg 拼接。'
    emit('changed')
  } catch (error) {
    errorMessage.value = displayError(error)
  } finally {
    action.value = null
  }
}

async function runCalibration() {
  const thresholds = parseThresholds()
  if (!thresholds.length) {
    errorMessage.value = '请至少输入一个 -1 到 1 之间的阈值。'
    return
  }
  action.value = 'calibration'
  errorMessage.value = null
  try {
    calibration.value = await calibrateIdentityThresholds(props.projectId, {
      episode_id: props.episode.id,
      thresholds,
      max_artifacts: 500,
    })
  } catch (error) {
    errorMessage.value = displayError(error)
  } finally {
    action.value = null
  }
}

async function retryFailedClips() {
  if (!retryStatuses.value.length) {
    errorMessage.value = '请至少选择一种需要重试的身份审核状态。'
    return
  }
  action.value = 'identity-retry'
  errorMessage.value = null
  successMessage.value = null
  try {
    const response = await retryIdentityFailedVideoClips(
      props.projectId,
      {
        episode_id: props.episode.id,
        identity_statuses: retryStatuses.value,
        max_tasks: retryMaxTasks.value,
        label: `第 ${props.episode.episode_number} 集身份失败镜头重试`,
      },
      idempotencyKey(`identity-retry-${props.episode.id}`),
    )
    successMessage.value = response.retried_task_ids.length
      ? `已提交 ${response.retried_task_ids.length} 个身份失败镜头重试任务。`
      : '没有发现符合条件的最新失败镜头；正在处理或已通过的镜头不会重复提交。'
    emit('changed')
  } catch (error) {
    errorMessage.value = displayError(error)
  } finally {
    action.value = null
  }
}

async function runLipSync() {
  if (!selectedVideoArtifactId.value || !props.audioArtifact || action.value) return
  action.value = 'lip-sync'
  errorMessage.value = null
  successMessage.value = null
  try {
    await createLipSyncTask(
      props.episode.id,
      {
        video_artifact_id: selectedVideoArtifactId.value,
        audio_artifact_id: props.audioArtifact.id,
        face_region: lipSyncFaceRegion.value,
        face_padding: lipSyncFacePadding.value,
      },
      idempotencyKey(`lip-sync-${selectedVideoArtifactId.value}-${props.audioArtifact.id}`),
    )
    successMessage.value = 'MuseTalk 唇形同步任务已提交；完成后可把 lip_synced_video 用于成片编排。'
    emit('changed')
  } catch (error) {
    errorMessage.value = displayError(error)
  } finally {
    action.value = null
  }
}

watch(() => props.projectId, () => { void loadAssets() })
watch(speakers, applySpeakerDefaults)
onMounted(() => { void loadAssets() })
</script>

<template>
  <section class="creator-workspace-card creator-quality-card">
    <div class="creator-workspace-card-heading">
      <div><p class="creator-eyebrow">QUALITY &amp; VOICE ASSETS</p><h3>身份审核与角色声音</h3><p>把自动初审、失败重试和角色声音配置集中到当前分集，任务仍然异步执行并保留历史 Artifact。</p></div>
      <span class="creator-card-state ready">生产工具</span>
    </div>

    <div v-if="errorMessage" class="quality-alert error"><strong>操作失败</strong><span>{{ errorMessage }}</span></div>
    <div v-if="successMessage" class="quality-alert success"><strong>已提交</strong><span>{{ successMessage }}</span></div>

    <div class="quality-grid">
      <article class="quality-panel">
        <div class="quality-panel-heading"><div><strong>声音资产库</strong><small>同一 TTS Provider 的声音档案才能绑定到任务。</small></div><span>{{ voiceAssets.length }} 个</span></div>
        <div class="quality-form-grid">
          <label><span>角色 / 档案名</span><input v-model="voiceLabel" maxlength="120" placeholder="例如：林默" /></label>
          <label><span>绑定角色资产</span><select v-model="characterAssetId"><option value="">不绑定角色</option><option v-for="asset in characterAssets" :key="asset.id" :value="asset.id">{{ asset.name }} · v{{ asset.version }}</option></select></label>
          <label><span>Provider</span><select v-model="voiceProvider"><option value="edge_tts">Edge TTS</option><option value="chattts">ChatTTS</option><option value="macos_say">macOS say</option><option value="mock">Mock（测试）</option></select></label>
          <label><span>声音名 / voice</span><input v-model="voiceName" maxlength="120" placeholder="zh-CN-YunyangNeural" /></label>
          <label><span>语速</span><input v-model="voiceRate" maxlength="20" placeholder="-35%" /></label>
          <label><span>音量</span><input v-model="voiceVolume" maxlength="20" placeholder="+0%" /></label>
          <label class="quality-wide"><span>声音备注</span><input v-model="voiceStyle" maxlength="300" placeholder="例如：低沉、克制、悬疑感" /></label>
        </div>
        <div class="quality-actions"><button class="creator-primary-button" type="button" :disabled="!voiceLabel.trim() || savingVoiceAsset" @click="saveVoiceProfile">{{ savingVoiceAsset ? '保存中…' : '保存声音资产' }} <span>→</span></button><button class="creator-ghost-button" type="button" :disabled="loadingAssets" @click="loadAssets">刷新</button></div>
        <div v-if="loadingAssets" class="quality-loading"><span class="spinner" />正在读取声音与角色资产…</div>
        <div v-else-if="voiceAssets.length" class="voice-asset-list"><div v-for="asset in voiceAssets" :key="asset.id"><span class="voice-asset-icon">♪</span><div><strong>{{ asset.label }}</strong><small>{{ asset.provider }} · {{ asset.voice }} · {{ asset.rate }}</small></div><em :class="asset.status">{{ asset.status === 'ready' ? '可用' : asset.status }}</em></div></div>
        <div v-else class="quality-empty">还没有声音资产。先保存一套档案，再为每个说话人选择声音。</div>
      </article>

      <article class="quality-panel">
        <div class="quality-panel-heading"><div><strong>多角色配音</strong><small>按剧本对白行绑定声音，整段任务完成后才登记一个连续音频 Artifact。</small></div><span>{{ dialogueLines.length }} 句</span></div>
        <div v-if="!dialogueLines.length" class="quality-empty">当前剧本还没有对白，生成或编辑剧本后这里会出现角色选择。</div>
        <div v-else class="speaker-list"><label v-for="speaker in speakers" :key="speaker"><span>{{ speaker }}</span><select v-model="selectedVoiceBySpeaker[speaker]"><option value="">请选择声音资产</option><option v-for="asset in voiceAssets.filter((item) => item.status === 'ready')" :key="asset.id" :value="asset.id">{{ asset.label }} · {{ asset.voice }}</option></select></label></div>
        <div class="quality-note"><span>i</span><p>当前多角色模式使用“每句生成 + 静音拼接”的稳定工程实现；它能保证角色绑定和可恢复性，但不是连续对话的最终混音质量。</p></div>
        <div class="quality-actions"><button class="creator-primary-button" type="button" :disabled="!multiVoiceReady" @click="generateMultiVoice">{{ action === 'multi-voice' ? '提交中…' : '生成多角色配音' }} <span>→</span></button></div>
      </article>

      <article class="quality-panel">
        <div class="quality-panel-heading"><div><strong>身份阈值校准</strong><small>只读比较不同阈值下的通过率，不会自动修改服务端生产阈值。</small></div><span v-if="calibration">{{ calibration.eligible_sample_count }} 个样本</span></div>
        <label class="quality-wide"><span>候选阈值</span><input v-model="thresholdText" maxlength="200" placeholder="0.30, 0.35, 0.40, 0.45, 0.50" /></label>
        <div class="quality-actions"><button class="creator-primary-button" type="button" :disabled="action !== null" @click="runCalibration">{{ action === 'calibration' ? '计算中…' : '运行校准' }} <span>→</span></button><span class="quality-current-threshold">当前生产阈值：{{ calibration?.current_threshold ?? '—' }}</span></div>
        <div v-if="calibration" class="calibration-result"><div class="calibration-summary"><span>视频 Artifact <strong>{{ calibration.artifact_count }}</strong></span><span>已审核 <strong>{{ calibration.audited_artifact_count }}</strong></span><span>排除 <strong>{{ calibration.excluded_artifact_count }}</strong></span></div><table><thead><tr><th>阈值</th><th>通过</th><th>失败</th><th>通过率</th></tr></thead><tbody><tr v-for="item in calibration.thresholds" :key="item.threshold"><td>{{ item.threshold.toFixed(2) }}</td><td>{{ item.passed_count }}</td><td>{{ item.failed_count }}</td><td>{{ formatPercent(item.pass_rate) }}</td></tr></tbody></table></div>
        <div v-else class="quality-empty">运行后可以看到当前分集样本的阈值敏感性。</div>
      </article>

      <article class="quality-panel">
        <div class="quality-panel-heading"><div><strong>失败镜头批量重试</strong><small>只选择每个镜头最新任务，旧 Artifact 不会被覆盖。</small></div><span>{{ identityRetryCount }} 个候选</span></div>
        <div class="quality-checkboxes"><label v-for="status in (['failed', 'no_face', 'reference_no_face', 'unavailable', 'error'] as IdentityAuditStatus[])" :key="status"><input v-model="retryStatuses" type="checkbox" :value="status" /><span>{{ identityStatusLabels[status] }}</span></label></div>
        <label><span>最多重试镜头数</span><input v-model.number="retryMaxTasks" type="number" min="1" max="100" /></label>
        <div class="quality-note"><span>!</span><p>“审核不可用”和“审核异常”通常应先检查本机 helper、模型目录或 Docker 挂载；确认环境正常后再重试。</p></div>
        <div class="quality-actions"><button class="creator-primary-button" type="button" :disabled="action !== null || !retryStatuses.length" @click="retryFailedClips">{{ action === 'identity-retry' ? '提交中…' : '一键重试失败镜头' }} <span>→</span></button></div>
      </article>

      <article class="quality-panel quality-panel-wide">
        <div class="quality-panel-heading"><div><strong>MuseTalk 唇形同步</strong><small>选择已生成的视频片段和当前旁白音频，完成后会得到 Assembly 可直接读取的 lip_synced_video Artifact。</small></div><span>{{ latestLipSyncTask ? formatStatus(latestLipSyncTask.status) : '未运行' }}</span></div>
        <div class="quality-form-grid">
          <label class="quality-wide"><span>视频源</span><select v-model="selectedVideoArtifactId"><option value="">请选择视频 Artifact</option><option v-for="item in videoArtifacts" :key="item.artifact.id" :value="item.artifact.id">{{ item.shotLabel }} · {{ item.artifact.type }} · {{ item.artifact.provider }}</option></select></label>
          <label><span>人脸区域</span><select v-model="lipSyncFaceRegion"><option value="auto">自动检测</option><option value="full_frame">整帧</option></select></label>
          <label><span>边缘扩展</span><input v-model.number="lipSyncFacePadding" type="number" min="0" max="100" /></label>
        </div>
        <div v-if="!audioArtifact" class="quality-empty">请先生成本集旁白音频，再创建唇形同步任务。</div>
        <div v-else class="quality-note"><span>♪</span><p>当前音频：{{ audioArtifact.provider }} · {{ audioArtifact.metadata.duration_seconds ? `${Number(audioArtifact.metadata.duration_seconds).toFixed(1)} 秒` : '已通过音频校验' }}。Provider 为 Mock 时只透传视频，不会产生真实嘴型运动。</p></div>
        <div v-if="latestLipSyncTask?.status === 'failed'" class="quality-alert error"><strong>唇形同步失败</strong><span>{{ latestLipSyncTask.error?.message ?? '请检查 MuseTalk runtime、wrapper 和模型目录。' }}</span></div>
        <div class="quality-actions"><button class="creator-primary-button" type="button" :disabled="!selectedVideoArtifactId || !audioArtifact || action !== null" @click="runLipSync">{{ action === 'lip-sync' ? '提交中…' : '创建 MuseTalk 任务' }} <span>→</span></button><span class="quality-current-threshold">真实 MuseTalk 需要配置 subprocess Provider；Mock 仅用于联调。</span></div>
      </article>
    </div>
  </section>
</template>

<style scoped>
.creator-quality-card { border-color: #dce4f1; }
.quality-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; margin-top: 16px; }
.quality-panel { display: grid; gap: 12px; min-width: 0; padding: 14px; border: 1px solid #e2e8f1; border-radius: 14px; background: #fbfcfe; }
.quality-panel-wide { grid-column: 1 / -1; }
.quality-panel-heading { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; }
.quality-panel-heading > div { display: grid; gap: 4px; }
.quality-panel-heading strong { color: var(--text); font-size: 13px; }
.quality-panel-heading small { color: var(--text-muted); font-size: 10px; line-height: 1.5; }
.quality-panel-heading > span { flex: 0 0 auto; border-radius: 999px; padding: 4px 8px; color: #56657d; background: #edf1f7; font-size: 10px; }
.quality-form-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }
.quality-form-grid label, .quality-panel > label { display: grid; gap: 5px; min-width: 0; }
.quality-form-grid label > span, .quality-panel > label > span { color: #536177; font-size: 10px; font-weight: 600; }
.quality-form-grid input, .quality-form-grid select, .quality-panel input, .quality-panel select { width: 100%; min-width: 0; border: 1px solid #d9e0eb; border-radius: 8px; padding: 8px 9px; color: var(--text); background: #fff; font: inherit; font-size: 11px; }
.quality-wide { grid-column: 1 / -1; }
.quality-actions { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; }
.quality-current-threshold { color: var(--text-muted); font-size: 10px; }
.quality-loading, .quality-empty { color: var(--text-muted); font-size: 10px; line-height: 1.5; }
.quality-loading { display: flex; align-items: center; gap: 7px; }
.quality-alert { display: flex; align-items: baseline; gap: 8px; margin-top: 12px; border-radius: 9px; padding: 9px 11px; font-size: 10px; }
.quality-alert strong { color: var(--text); }
.quality-alert span { color: inherit; }
.quality-alert.error { color: #a44b55; background: #fff0f1; }
.quality-alert.success { color: #2c7650; background: #effaf4; }
.voice-asset-list { display: grid; gap: 6px; max-height: 180px; overflow: auto; }
.voice-asset-list > div { display: flex; align-items: center; gap: 8px; padding: 8px 9px; border: 1px solid #e7ebf2; border-radius: 9px; background: #fff; }
.voice-asset-icon { display: grid; place-items: center; width: 24px; height: 24px; border-radius: 7px; color: #5964b6; background: #eef0ff; font-size: 14px; }
.voice-asset-list > div > div { display: grid; gap: 2px; min-width: 0; flex: 1; }
.voice-asset-list strong { font-size: 11px; }
.voice-asset-list small { overflow: hidden; color: var(--text-muted); font-size: 9px; text-overflow: ellipsis; white-space: nowrap; }
.voice-asset-list em { flex: 0 0 auto; color: #2d7d55; font-size: 9px; font-style: normal; }
.speaker-list { display: grid; gap: 8px; max-height: 220px; overflow: auto; }
.speaker-list label { display: grid; grid-template-columns: minmax(60px, .45fr) minmax(0, 1fr); align-items: center; gap: 8px; }
.speaker-list label > span { overflow: hidden; color: var(--text); font-size: 10px; font-weight: 700; text-overflow: ellipsis; white-space: nowrap; }
.quality-note { display: flex; align-items: flex-start; gap: 8px; border-radius: 9px; padding: 9px 10px; color: #59677d; background: #f1f5fa; font-size: 10px; line-height: 1.5; }
.quality-note > span { display: grid; place-items: center; flex: 0 0 auto; width: 16px; height: 16px; border-radius: 50%; color: #3f6da8; background: #dae8fb; font-size: 9px; font-weight: 700; }
.quality-note p { margin: 0; }
.calibration-result { display: grid; gap: 8px; }
.calibration-summary { display: flex; flex-wrap: wrap; gap: 6px; }
.calibration-summary span { border-radius: 999px; padding: 4px 7px; color: #68758a; background: #eef2f7; font-size: 9px; }
.calibration-summary strong { color: var(--text); }
.calibration-result table { width: 100%; border-collapse: collapse; font-size: 10px; }
.calibration-result th, .calibration-result td { padding: 6px 5px; border-bottom: 1px solid #e7ebf1; text-align: left; }
.calibration-result th { color: var(--text-muted); font-size: 9px; font-weight: 600; }
.quality-checkboxes { display: flex; flex-wrap: wrap; gap: 7px; }
.quality-checkboxes label { display: inline-flex; align-items: center; gap: 5px; color: #58667a; font-size: 10px; }
.quality-checkboxes input { width: auto; }
@media (max-width: 860px) { .quality-grid { grid-template-columns: 1fr; } .quality-panel-wide { grid-column: auto; } }
</style>

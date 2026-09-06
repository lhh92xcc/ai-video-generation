<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type { EpisodeRecord, NovelProjectRecord, ArtifactSummary, GenerationTaskRecord } from '../types/task'

type GateState = 'passed' | 'pending' | 'blocked' | 'not_applicable'

interface ReadinessGate {
  id: string
  label: string
  description: string
  evidence: string
  state: GateState
  blocking: boolean
  target: string | null
}

interface HumanReviewItem {
  id: string
  label: string
  description: string
}

const props = defineProps<{
  project: NovelProjectRecord
  episode: EpisodeRecord | null
  sourceReady: boolean
  storyBibleReady: boolean
  episodesReady: boolean
  scriptReady: boolean
  shotListReady: boolean
  assetGateReady: boolean
  shotTotalCount: number
  videoClipReadyCount: number
  videoClipSucceededCount: number
  audioArtifact: ArtifactSummary | null
  subtitleArtifact: ArtifactSummary | null
  renderedVideoArtifact: ArtifactSummary | null
  tasks: GenerationTaskRecord[]
}>()

const emit = defineEmits<{ locate: [target: string] }>()

const reviewItems: HumanReviewItem[] = [
  { id: 'story_fidelity', label: '改编忠实度', description: '剧本和分镜没有偏离原文核心人物、冲突与结局。' },
  { id: 'character_identity', label: '角色一致性', description: '参考图与各镜头中的脸型、发型、服装和色彩保持一致。' },
  { id: 'motion_continuity', label: '动作与镜头', description: '没有明显变脸、肢体崩坏、跳切或“图片拼接感”。' },
  { id: 'voice_naturalness', label: '声音自然度', description: '语速、停顿、发音和场景之间的衔接可以正常听清。' },
  { id: 'subtitle_alignment', label: '字幕同步', description: '字幕文本正确，出现和消失时间与声音基本一致。' },
  { id: 'final_story_flow', label: '成片观感', description: '完整观看后节奏、信息密度和竖屏构图适合作品集展示。' },
]

const reviewState = ref<Record<string, boolean>>({})

const storageKey = computed(() => (
  `ai-video-generation:portfolio-review:${props.project.id}:${props.episode?.id ?? 'project'}`
))

function loadReviewState() {
  const next: Record<string, boolean> = {}
  try {
    const raw = window.localStorage.getItem(storageKey.value)
    const parsed: unknown = raw ? JSON.parse(raw) : null
    if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
      for (const item of reviewItems) {
        if (typeof (parsed as Record<string, unknown>)[item.id] === 'boolean') {
          next[item.id] = Boolean((parsed as Record<string, unknown>)[item.id])
        }
      }
    }
  } catch {
    // Browser storage may be disabled; the checklist still works in memory.
  }
  reviewState.value = next
}

function persistReviewState() {
  try {
    window.localStorage.setItem(storageKey.value, JSON.stringify(reviewState.value))
  } catch {
    // A review checkbox must never block the production workspace.
  }
}

function resetReviewState() {
  reviewState.value = {}
  try {
    window.localStorage.removeItem(storageKey.value)
  } catch {
    // Keep the in-memory reset even when storage is unavailable.
  }
}

watch(storageKey, loadReviewState, { immediate: true })
watch(reviewState, persistReviewState, { deep: true })

function stateFor(ready: boolean, available = true, invalid = false): GateState {
  if (!available) return 'not_applicable'
  if (ready) return 'passed'
  return invalid ? 'blocked' : 'pending'
}

function formatState(state: GateState): string {
  if (state === 'passed') return '已通过'
  if (state === 'blocked') return '待处理'
  if (state === 'not_applicable') return '不适用'
  return '待完成'
}

function stateIcon(state: GateState): string {
  if (state === 'passed') return '✓'
  if (state === 'blocked') return '!'
  if (state === 'not_applicable') return '—'
  return '○'
}

function numberFrom(value: unknown): number {
  const result = typeof value === 'number' ? value : Number(value)
  return Number.isFinite(result) ? result : 0
}

const identityAuditSummary = computed(() => {
  const latestTaskByShot = new Map<number, GenerationTaskRecord>()
  for (const task of props.tasks) {
    if (task.kind !== 'video_clip' || String(task.input_data.episode_id ?? '') !== props.episode?.id) continue
    const shotIndex = numberFrom(task.input_data.shot_index)
    if (!Number.isInteger(shotIndex) || shotIndex < 1) continue
    const existing = latestTaskByShot.get(shotIndex)
    if (!existing || existing.updated_at.localeCompare(task.updated_at) < 0) {
      latestTaskByShot.set(shotIndex, task)
    }
  }
  let audited = 0
  let passed = 0
  let failed = 0
  for (const task of latestTaskByShot.values()) {
    if (task.status !== 'succeeded') continue
    for (const artifact of task.artifacts) {
      const report = artifact.metadata.identity_audit
      if (!report || typeof report !== 'object' || Array.isArray(report)) continue
      const status = String((report as Record<string, unknown>).status ?? '')
      if (!status) continue
      audited += 1
      if (status === 'passed') passed += 1
      if (['failed', 'no_face', 'reference_no_face', 'error'].includes(status)) failed += 1
    }
  }
  return { audited, passed, failed }
})

const outputProbe = computed(() => {
  const metadata = props.renderedVideoArtifact?.metadata ?? {}
  const nested = metadata.ffprobe
  const probe = nested && typeof nested === 'object' && !Array.isArray(nested)
    ? nested as Record<string, unknown>
    : metadata
  const width = numberFrom(probe.width)
  const height = numberFrom(probe.height)
  const durationCandidate = probe.duration_seconds ?? metadata.duration_seconds
  const duration = numberFrom(
    durationCandidate ?? (metadata.duration_ms ? numberFrom(metadata.duration_ms) / 1000 : 0),
  )
  return { width, height, duration }
})

const outputIsVertical = computed(() => {
  const { width, height } = outputProbe.value
  return width > 0 && height > 0 && Math.abs(width / height - 9 / 16) < 0.035
})

const outputDurationReady = computed(() => outputProbe.value.duration >= 45 && outputProbe.value.duration <= 60)

const nonRealMotionProviders = new Set(['mock', 'local_fixture', 'ffmpeg_motion'])
const realMotionProviders = new Set(['comfyui_wan_i2v', 'openai_compatible', 'siliconflow'])

const motionProviderSummary = computed(() => {
  if (!props.episode) return { state: 'not_applicable' as GateState, providers: [] as string[], evidence: '等待选择分集' }
  const latestSuccessfulTaskByShot = new Map<number, GenerationTaskRecord>()
  for (const task of props.tasks) {
    if (
      task.kind !== 'video_clip'
      || task.status !== 'succeeded'
      || String(task.input_data.episode_id ?? '') !== props.episode.id
    ) continue
    const shotIndex = numberFrom(task.input_data.shot_index)
    if (!Number.isInteger(shotIndex) || shotIndex < 1) continue
    if (!task.artifacts.some((artifact) => artifact.type === 'video_clip')) continue
    const existing = latestSuccessfulTaskByShot.get(shotIndex)
    if (!existing || existing.updated_at.localeCompare(task.updated_at) < 0) {
      latestSuccessfulTaskByShot.set(shotIndex, task)
    }
  }
  const artifacts = [...latestSuccessfulTaskByShot.values()]
    .flatMap((task) => task.artifacts.filter((artifact) => artifact.type === 'video_clip'))
  const providers = [...new Set(artifacts.map((artifact) => String(artifact.provider ?? '').trim().toLowerCase()).filter(Boolean))]
  if (!providers.length) {
    return { state: 'pending' as GateState, providers, evidence: '尚无成功视频片段，等待真实视频 Provider 产物' }
  }
  const staticArtifact = artifacts.find((artifact) => {
    const motion = String(artifact.metadata.motion ?? '').trim().toLowerCase()
    const source = String(artifact.metadata.source ?? '').trim().toLowerCase()
    return motion === 'ken_burns' || motion === 'fixture-color-card' || source === 'deterministic_color_fallback'
  })
  if (staticArtifact || providers.some((provider) => nonRealMotionProviders.has(provider) || !realMotionProviders.has(provider))) {
    return {
      state: 'blocked' as GateState,
      providers,
      evidence: `片段来源 ${providers.join('、')} 不属于真实运动模型，不能作为正式作品集证据`,
    }
  }
  if (providers.length > 1) {
    return { state: 'blocked' as GateState, providers, evidence: `检测到多个视频 Provider：${providers.join('、')}，请勿混用` }
  }
  return { state: 'passed' as GateState, providers, evidence: `${providers[0]} · ${artifacts.length} 个片段来自真实视频 Provider` }
})

const machineGates = computed<ReadinessGate[]>(() => [
  {
    id: 'content',
    label: '内容链路',
    description: '原文、StoryBible 与分集大纲都已形成可追踪输入。',
    evidence: props.sourceReady && props.storyBibleReady && props.episodesReady ? '原文 / 设定 / 分集已就绪' : '仍有内容前置步骤未完成',
    state: stateFor(props.sourceReady && props.storyBibleReady && props.episodesReady),
    blocking: true,
    target: 'creator-step-source',
  },
  {
    id: 'script',
    label: '剧本与分镜',
    description: '当前分集有结构化剧本和可审核的镜头清单。',
    evidence: props.scriptReady && props.shotListReady ? '剧本与分镜 Artifact 已登记' : '等待当前分集剧本或分镜',
    state: stateFor(Boolean(props.episode) && props.scriptReady && props.shotListReady, Boolean(props.episode)),
    blocking: true,
    target: 'creator-step-script',
  },
  {
    id: 'assets',
    label: '资产门禁',
    description: '角色、场景、道具均已审核、唯一绑定并具备参考图。',
    evidence: !props.episode || props.shotTotalCount === 0
      ? '等待当前分集分镜清单'
      : props.assetGateReady
        ? `${props.shotTotalCount} 个镜头均通过资产门禁`
        : `${Math.max(0, props.shotTotalCount - props.videoClipReadyCount)} 个镜头仍需处理`,
    state: stateFor(props.assetGateReady && props.shotTotalCount > 0, Boolean(props.episode), props.shotTotalCount > 0),
    blocking: true,
    target: 'creator-step-assets',
  },
  {
    id: 'identity',
    label: '身份初审',
    description: '视频镜头已经有自动身份审核结果，可定位疑似变脸镜头。',
    evidence: identityAuditSummary.value.audited ? `${identityAuditSummary.value.passed}/${identityAuditSummary.value.audited} 个审核通过` : '尚未登记身份审核结果',
    state: !props.episode
      ? 'not_applicable'
      : identityAuditSummary.value.audited === 0
        ? 'pending'
        : stateFor(
          identityAuditSummary.value.failed === 0,
          true,
          identityAuditSummary.value.failed > 0,
        ),
    blocking: Boolean(props.episode),
    target: 'creator-step-quality',
  },
  {
    id: 'media',
    label: '声音、字幕与真实片段',
    description: '旁白、字幕和全部可生成镜头都有成功 Artifact，片段来自真实运动模型。',
    evidence: !props.episode || props.shotTotalCount === 0
      ? '等待当前分集分镜清单'
      : props.audioArtifact && props.subtitleArtifact
      ? `${props.videoClipSucceededCount}/${props.videoClipReadyCount || props.shotTotalCount} 个视频片段已完成 · ${motionProviderSummary.value.evidence}`
      : `旁白或字幕 Artifact 尚未完成 · ${motionProviderSummary.value.evidence}`,
    state: stateFor(Boolean(
      props.audioArtifact
      && props.subtitleArtifact
      && props.videoClipReadyCount > 0
      && props.videoClipSucceededCount >= props.videoClipReadyCount
      && motionProviderSummary.value.state === 'passed',
    ), Boolean(props.episode), Boolean(
      props.audioArtifact
      || props.subtitleArtifact
      || props.videoClipSucceededCount > 0
      || motionProviderSummary.value.state === 'blocked',
    )),
    blocking: true,
    target: 'creator-step-audio',
  },
  {
    id: 'shot_budget',
    label: '镜头规模',
    description: '作品集样片保持 8～12 个短镜头，既能展示链路又方便人工筛选。',
    evidence: `${props.shotTotalCount} 个镜头 · 目标 8～12 个`,
    state: stateFor(
      props.shotTotalCount >= 8 && props.shotTotalCount <= 12,
      Boolean(props.episode),
      props.shotTotalCount > 0,
    ),
    blocking: true,
    target: 'creator-step-video',
  },
  {
    id: 'output',
    label: '成片规格',
    description: '最终视频可读取、时长在 45～60 秒，并保持 9:16 竖屏。',
    evidence: props.renderedVideoArtifact
      ? `${outputProbe.value.width || '?'}×${outputProbe.value.height || '?'} · ${outputProbe.value.duration.toFixed(1)} 秒`
      : '尚未生成最终成片',
    state: stateFor(
      Boolean(props.renderedVideoArtifact && outputIsVertical.value && outputDurationReady.value),
      Boolean(props.episode),
      Boolean(props.renderedVideoArtifact),
    ),
    blocking: true,
    target: 'creator-step-assembly',
  },
])

const machinePassedCount = computed(() => machineGates.value.filter((gate) => gate.blocking && gate.state === 'passed').length)
const machineGateCount = computed(() => machineGates.value.filter((gate) => gate.blocking && gate.state !== 'not_applicable').length)
const manualPassedCount = computed(() => reviewItems.filter((item) => reviewState.value[item.id]).length)
const allMachinePassed = computed(() => machineGateCount.value > 0 && machinePassedCount.value === machineGateCount.value)
const allManualPassed = computed(() => manualPassedCount.value === reviewItems.length)
const readinessLabel = computed(() => {
  if (!props.episode) return '等待选择分集'
  if (allMachinePassed.value && allManualPassed.value) return '作品集就绪'
  if (allMachinePassed.value) return '可以开始人工验收'
  return '还需完成生产步骤'
})
const readinessClass = computed(() => {
  if (allMachinePassed.value && allManualPassed.value) return 'ready'
  if (allMachinePassed.value) return 'review'
  return 'pending'
})
const readinessPercent = computed(() => {
  const total = machineGateCount.value + reviewItems.length
  return total ? Math.round(((machinePassedCount.value + manualPassedCount.value) / total) * 100) : 0
})
const unresolvedMachineGates = computed(() => machineGates.value.filter((gate) => gate.blocking && gate.state !== 'passed' && gate.state !== 'not_applicable'))

const portfolioReport = computed(() => ({
  report_schema_version: 1,
  generated_at: new Date().toISOString(),
  project: {
    id: props.project.id,
    title: props.project.title,
    language: props.project.language,
  },
  episode: props.episode ? {
    id: props.episode.id,
    episode_number: props.episode.episode_number,
    title: props.episode.outline.title,
    target_duration_seconds: props.episode.outline.target_duration_seconds,
  } : null,
  target: {
    duration_seconds: { min: 45, max: 60 },
    aspect_ratio: '9:16',
    shot_count: { min: 8, max: 12 },
    style: '2D manhwa / dynamic comic',
  },
  readiness: {
    label: readinessLabel.value,
    status: allMachinePassed.value && allManualPassed.value
      ? 'ready_for_portfolio'
      : allMachinePassed.value
        ? 'ready_for_human_review'
        : 'incomplete',
    percent: readinessPercent.value,
    machine_passed: machinePassedCount.value,
    machine_total: machineGateCount.value,
    human_passed: manualPassedCount.value,
    human_total: reviewItems.length,
  },
  output: {
    artifact_present: Boolean(props.renderedVideoArtifact),
    width: outputProbe.value.width || null,
    height: outputProbe.value.height || null,
    duration_seconds: outputProbe.value.duration ? Number(outputProbe.value.duration.toFixed(3)) : null,
    vertical_9_16: outputIsVertical.value,
    duration_in_target: outputDurationReady.value,
    provider: props.renderedVideoArtifact?.provider ?? null,
  },
  motion: {
    state: motionProviderSummary.value.state,
    providers: motionProviderSummary.value.providers,
    evidence: motionProviderSummary.value.evidence,
  },
  machine_checks: machineGates.value.map((gate) => ({
    id: gate.id,
    label: gate.label,
    state: gate.state,
    status: formatState(gate.state),
    blocking: gate.blocking,
    evidence: gate.evidence,
  })),
  human_reviews: reviewItems.map((item) => ({
    id: item.id,
    label: item.label,
    status: reviewState.value[item.id] ? 'passed' : 'pending',
    description: item.description,
  })),
  next_actions: unresolvedMachineGates.value.map((gate) => gate.label),
}))

function markdownCell(value: unknown): string {
  return String(value ?? '—').replaceAll('|', '\\|').replaceAll('\n', ' ')
}

function downloadTextFile(filename: string, content: string, mimeType: string) {
  const blob = new Blob([content], { type: `${mimeType};charset=utf-8` })
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = filename
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
  window.setTimeout(() => URL.revokeObjectURL(url), 1000)
}

function reportFilename(extension: string): string {
  const project = props.project.title.trim().replace(/[^\p{L}\p{N}_-]+/gu, '-').replace(/^-+|-+$/g, '') || 'ai-video-project'
  const episode = props.episode ? `-episode-${String(props.episode.episode_number).padStart(2, '0')}` : ''
  return `${project}${episode}-portfolio-report.${extension}`
}

function exportJsonReport() {
  if (!props.episode) return
  downloadTextFile(reportFilename('json'), `${JSON.stringify(portfolioReport.value, null, 2)}\n`, 'application/json')
}

function exportMarkdownReport() {
  if (!props.episode) return
  const report = portfolioReport.value
  const machineRows = report.machine_checks
    .map((gate) => `| ${markdownCell(gate.label)} | ${markdownCell(gate.status)} | ${markdownCell(gate.evidence)} |`)
    .join('\n')
  const humanRows = report.human_reviews
    .map((item) => `| ${markdownCell(item.label)} | ${item.status === 'passed' ? '已完成' : '待审核'} | ${markdownCell(item.description)} |`)
    .join('\n')
  const output = report.output
  const nextActions = report.next_actions.length
    ? report.next_actions.map((item) => `- ${markdownCell(item)}`).join('\n')
    : '- 机器门禁已通过；请完成并保存人工审核清单。'
  const content = [
    `# ${markdownCell(report.project.title)} · 第 ${report.episode?.episode_number ?? '—'} 集作品集验收报告`,
    '',
    `生成时间：${report.generated_at}`,
    '',
    '## 当前结论',
    '',
    `- 状态：**${markdownCell(report.readiness.label)}**`,
    `- 总进度：${report.readiness.percent}%（机器 ${report.readiness.machine_passed}/${report.readiness.machine_total}，人工 ${report.readiness.human_passed}/${report.readiness.human_total}）`,
    `- 真实运动 Provider：${report.motion.providers.length ? report.motion.providers.join('、') : '尚未确认'}`,
    '',
    '## 成片规格',
    '',
    `- 输出：${output.width ?? '—'}×${output.height ?? '—'}`,
    `- 时长：${output.duration_seconds ?? '—'} 秒`,
    `- 9:16 竖屏：${output.vertical_9_16 ? '是' : '否'}`,
    `- 时长达标：${output.duration_in_target ? '是' : '否'}`,
    `- 成片 Provider：${output.provider ?? '—'}`,
    '',
    '## 机器门禁',
    '',
    '| 门禁 | 状态 | 证据 |',
    '| --- | --- | --- |',
    machineRows,
    '',
    '## 人工审核',
    '',
    '| 项目 | 状态 | 审核标准 |',
    '| --- | --- | --- |',
    humanRows,
    '',
    '## 下一步',
    '',
    nextActions,
    '',
    '> 说明：机器门禁只证明工程和文件条件；画面、声音、字幕与完整观看感受必须由人工确认。',
    '',
  ].join('\n')
  downloadTextFile(reportFilename('md'), content, 'text/markdown')
}

function locate(gate: ReadinessGate) {
  if (gate.target) emit('locate', gate.target)
}
</script>

<template>
  <section id="creator-step-quality" class="portfolio-readiness-card" aria-labelledby="portfolio-readiness-title">
    <div class="portfolio-readiness-heading">
      <div>
        <p class="creator-eyebrow">PORTFOLIO READINESS</p>
        <h3 id="portfolio-readiness-title">作品集 Demo 就绪度</h3>
        <p>把“代码跑通”和“成片能拿去面试”分开验收。机器门禁只证明工程条件，画面、声音和字幕仍需你亲自确认。</p>
      </div>
      <span class="portfolio-readiness-badge" :class="readinessClass">{{ readinessLabel }}</span>
    </div>

    <div class="portfolio-readiness-progress">
      <div class="portfolio-readiness-progress-copy"><strong>{{ readinessPercent }}%</strong><span>机器门禁 {{ machinePassedCount }}/{{ machineGateCount }} · 人工审核 {{ manualPassedCount }}/{{ reviewItems.length }}</span></div>
      <div class="portfolio-readiness-progress-track"><i :style="{ width: `${readinessPercent}%` }" /></div>
    </div>

    <div class="portfolio-export-bar">
      <div><strong>作品集交付材料</strong><small>导出当前状态，方便和成片、截图一起提交；不会包含密钥或本地路径。</small></div>
      <div class="portfolio-export-actions">
        <button type="button" class="portfolio-export-button" :disabled="!episode" @click="exportMarkdownReport">下载 Markdown</button>
        <button type="button" class="portfolio-export-button primary" :disabled="!episode" @click="exportJsonReport">下载 JSON</button>
      </div>
    </div>

    <div class="portfolio-readiness-columns">
      <section class="portfolio-readiness-section">
        <div class="portfolio-readiness-section-heading"><div><strong>机器可验证门禁</strong><small>来自真实任务和 Artifact 状态</small></div><span>{{ machinePassedCount }}/{{ machineGateCount }}</span></div>
        <div class="portfolio-gate-list">
          <button v-for="gate in machineGates" :key="gate.id" type="button" class="portfolio-gate" :class="gate.state" :disabled="!gate.target" @click="locate(gate)">
            <span class="portfolio-gate-icon">{{ stateIcon(gate.state) }}</span>
            <span class="portfolio-gate-copy"><strong>{{ gate.label }}</strong><small>{{ gate.evidence }}</small><em>{{ gate.description }}</em></span>
            <b>{{ formatState(gate.state) }}</b>
          </button>
        </div>
      </section>

      <section class="portfolio-readiness-section">
        <div class="portfolio-readiness-section-heading"><div><strong>人工验收清单</strong><small>勾选只保存在当前浏览器，不会伪造服务端质量结论</small></div><button type="button" class="portfolio-reset-button" @click="resetReviewState">清空</button></div>
        <div class="portfolio-review-list">
          <label v-for="item in reviewItems" :key="item.id" class="portfolio-review-item" :class="{ checked: reviewState[item.id] }">
            <input v-model="reviewState[item.id]" type="checkbox" />
            <span class="portfolio-review-mark">{{ reviewState[item.id] ? '✓' : '' }}</span>
            <span><strong>{{ item.label }}</strong><small>{{ item.description }}</small></span>
          </label>
        </div>
      </section>
    </div>

    <div v-if="unresolvedMachineGates.length" class="portfolio-readiness-blockers"><span>下一步</span><p>优先处理：{{ unresolvedMachineGates.map((gate) => gate.label).join('、') }}。点击左侧门禁可以直接定位到对应操作区。</p></div>
    <div v-else class="portfolio-readiness-success"><span>✓</span><p>机器门禁已全部通过。完成右侧人工清单后，这一集才可以作为最终作品集样片对外展示。</p></div>
  </section>
</template>

<style scoped>
.portfolio-readiness-card { margin-top: 16px; border: 1px solid #dfe1fa; border-radius: 16px; padding: 18px; background: radial-gradient(circle at 100% 0%, rgba(119,121,220,.09), transparent 34%), linear-gradient(145deg, #fafaff 0%, #fff 63%); box-shadow: 0 12px 30px rgba(79, 82, 157, .05); }
.portfolio-readiness-heading { display: flex; align-items: flex-start; justify-content: space-between; gap: 18px; }
.portfolio-readiness-heading h3 { margin: 0; color: #3f4770; font-size: 17px; letter-spacing: -.025em; }
.portfolio-readiness-heading p:last-child { max-width: 680px; margin: 7px 0 0; color: #7f879c; font-size: 10px; line-height: 1.65; }
.portfolio-readiness-badge { flex: 0 0 auto; border-radius: 999px; padding: 7px 10px; color: #767e91; background: #eef1f7; font-size: 10px; font-weight: 750; }
.portfolio-readiness-badge.review { color: #756329; background: #fff4d9; }.portfolio-readiness-badge.ready { color: #287b55; background: #e5f7ed; }
.portfolio-readiness-progress { margin-top: 16px; border: 1px solid #e6e7f4; border-radius: 11px; padding: 11px 12px; background: rgba(255,255,255,.82); }
.portfolio-readiness-progress-copy { display: flex; align-items: baseline; justify-content: space-between; gap: 12px; }.portfolio-readiness-progress-copy strong { color: #5d61ce; font-size: 19px; }.portfolio-readiness-progress-copy span { color: #9299aa; font-size: 9px; }
.portfolio-readiness-progress-track { height: 5px; margin-top: 8px; overflow: hidden; border-radius: 999px; background: #eeeff6; }.portfolio-readiness-progress-track i { display: block; height: 100%; border-radius: inherit; background: linear-gradient(90deg, #7476dc, #4ca77e); transition: width 180ms ease; }
.portfolio-export-bar { display: flex; align-items: center; justify-content: space-between; gap: 16px; margin-top: 14px; border: 1px solid #e7e8f3; border-radius: 11px; padding: 12px 14px; background: rgba(255,255,255,.78); }
.portfolio-export-bar strong, .portfolio-export-bar small { display: block; }
.portfolio-export-bar strong { color: #566078; font-size: 11px; }
.portfolio-export-bar small { margin-top: 4px; color: #969eaf; font-size: 9px; line-height: 1.5; }
.portfolio-export-actions { display: flex; flex: 0 0 auto; gap: 7px; }
.portfolio-export-button { border: 1px solid #dfe2ee; border-radius: 7px; padding: 7px 10px; color: #656e83; background: #fff; font-size: 9px; font-weight: 700; cursor: pointer; }
.portfolio-export-button:hover:not(:disabled) { border-color: #afb2e8; color: #5e62cd; background: #fafaff; }
.portfolio-export-button.primary { border-color: #686add; color: #fff; background: #6264d8; }
.portfolio-export-button.primary:hover:not(:disabled) { border-color: #5355c7; background: #5355c7; }
.portfolio-export-button:disabled { cursor: not-allowed; opacity: .5; }
.portfolio-readiness-columns { display: grid; grid-template-columns: minmax(0, 1.1fr) minmax(0, .9fr); gap: 13px; margin-top: 14px; }.portfolio-readiness-section { min-width: 0; border: 1px solid #e5e7f0; border-radius: 12px; padding: 14px; background: rgba(255,255,255,.72); }
.portfolio-readiness-section-heading { display: flex; align-items: flex-start; justify-content: space-between; gap: 10px; }.portfolio-readiness-section-heading strong, .portfolio-readiness-section-heading small { display: block; }.portfolio-readiness-section-heading strong { color: #566078; font-size: 11px; }.portfolio-readiness-section-heading small { margin-top: 4px; color: #9ba3b3; font-size: 9px; line-height: 1.45; }.portfolio-readiness-section-heading > span { color: #6469ce; font-size: 11px; font-weight: 750; }.portfolio-reset-button { border: 0; padding: 0; color: #8a92a5; background: transparent; font-size: 9px; cursor: pointer; }.portfolio-reset-button:hover { color: #5e63c7; }
.portfolio-gate-list, .portfolio-review-list { display: grid; gap: 7px; margin-top: 11px; }.portfolio-gate { display: flex; align-items: center; gap: 9px; width: 100%; min-width: 0; border: 1px solid #eceef4; border-radius: 9px; padding: 9px; color: inherit; background: #fff; text-align: left; cursor: pointer; transition: border-color 150ms ease, background 150ms ease, transform 150ms ease; }.portfolio-gate:hover:not(:disabled) { border-color: #c9ccf1; background: #fcfcff; transform: translateY(-1px); }.portfolio-gate:disabled { cursor: default; }.portfolio-gate-icon, .portfolio-review-mark { display: grid; place-items: center; flex: 0 0 23px; width: 23px; height: 23px; border-radius: 8px; color: #8d96a8; background: #f0f2f6; font-size: 11px; font-weight: 800; }.portfolio-gate.passed .portfolio-gate-icon { color: #fff; background: #4aa77d; }.portfolio-gate.blocked .portfolio-gate-icon { color: #fff; background: #d86c77; }.portfolio-gate-copy { min-width: 0; flex: 1; }.portfolio-gate-copy strong, .portfolio-gate-copy small, .portfolio-gate-copy em { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }.portfolio-gate-copy strong { color: #5b657a; font-size: 10px; }.portfolio-gate-copy small { margin-top: 3px; color: #6f78ce; font-size: 9px; }.portfolio-gate-copy em { margin-top: 3px; color: #a1a8b5; font-size: 8px; font-style: normal; }.portfolio-gate > b { flex: 0 0 auto; color: #a0a8b5; font-size: 9px; font-weight: 650; }.portfolio-gate.passed > b { color: #3d936d; }.portfolio-gate.pending > b { color: #9b7b35; }
.portfolio-review-item { display: flex; align-items: flex-start; gap: 9px; min-width: 0; border: 1px solid #eceef4; border-radius: 9px; padding: 9px; background: #fff; cursor: pointer; transition: border-color 150ms ease, background 150ms ease; }.portfolio-review-item:hover { border-color: #d2d4f4; }.portfolio-review-item input { position: absolute; width: 1px; height: 1px; opacity: 0; }.portfolio-review-item.checked { border-color: #cde9da; background: #f8fdf9; }.portfolio-review-item.checked .portfolio-review-mark { color: #fff; background: #4aa77d; }.portfolio-review-item > span:last-child { min-width: 0; }.portfolio-review-item strong, .portfolio-review-item small { display: block; }.portfolio-review-item strong { color: #5b657a; font-size: 10px; }.portfolio-review-item small { margin-top: 4px; color: #99a1b0; font-size: 9px; line-height: 1.5; }
.portfolio-readiness-blockers, .portfolio-readiness-success { display: flex; align-items: flex-start; gap: 9px; margin-top: 14px; border-radius: 9px; padding: 10px 11px; font-size: 10px; line-height: 1.55; }.portfolio-readiness-blockers { color: #806831; background: #fff9e9; }.portfolio-readiness-success { color: #397958; background: #effaf4; }.portfolio-readiness-blockers > span, .portfolio-readiness-success > span { display: grid; place-items: center; flex: 0 0 18px; width: 18px; height: 18px; border-radius: 50%; color: inherit; background: rgba(255,255,255,.72); font-weight: 800; }.portfolio-readiness-blockers p, .portfolio-readiness-success p { margin: 0; }
@media (max-width: 860px) { .portfolio-readiness-heading { flex-direction: column; gap: 9px; }.portfolio-readiness-columns { grid-template-columns: 1fr; } }
@media (max-width: 620px) { .portfolio-export-bar { align-items: flex-start; flex-direction: column; }.portfolio-export-actions { width: 100%; }.portfolio-export-button { flex: 1; } }

/* Keep the readiness panel legible when used as the final portfolio gate. */
.portfolio-readiness-card { padding: 22px; background: radial-gradient(circle at 100% 0%, rgba(119,121,220,.12), transparent 34%), linear-gradient(145deg, #fafaff 0%, #fff 63%); box-shadow: 0 16px 38px rgba(79, 82, 157, .07); }
.portfolio-readiness-heading h3 { font-size: 19px; }
.portfolio-readiness-heading p:last-child { font-size: 12px; line-height: 1.7; }
.portfolio-readiness-badge { font-size: 11px; }
.portfolio-readiness-progress { margin-top: 18px; padding: 13px 14px; }
.portfolio-readiness-progress-copy strong { font-size: 22px; }
.portfolio-readiness-progress-copy span { font-size: 10px; }
.portfolio-readiness-progress-track { height: 7px; margin-top: 10px; }
.portfolio-readiness-columns { gap: 15px; margin-top: 16px; }
.portfolio-readiness-section { padding: 16px; }
.portfolio-readiness-section-heading strong { font-size: 12px; }
.portfolio-readiness-section-heading small { font-size: 10px; }
.portfolio-readiness-section-heading > span { font-size: 12px; }
.portfolio-reset-button { font-size: 10px; }
.portfolio-export-bar { padding: 14px; }
.portfolio-export-bar strong { font-size: 12px; }
.portfolio-export-bar small { font-size: 10px; }
.portfolio-export-button { font-size: 10px; }
.portfolio-gate, .portfolio-review-item { padding: 11px; }
.portfolio-gate-copy strong, .portfolio-review-item strong { font-size: 11px; }
.portfolio-gate-copy small, .portfolio-review-item small { font-size: 10px; }
.portfolio-gate-copy em { font-size: 9px; }
.portfolio-gate > b { font-size: 10px; }
.portfolio-readiness-blockers, .portfolio-readiness-success { font-size: 11px; }
@media (max-width: 860px) { .portfolio-readiness-card { padding: 18px; } }
</style>

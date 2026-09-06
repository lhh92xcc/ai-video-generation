<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { ApiClientError } from '../api/client'
import { createAudioBGMTask, createAudioNarrationTask } from '../api/audio'
import { createSubtitleASRTask, createSubtitleAlignmentTask } from '../api/subtitles'
import { createVideoAssemblyTask, createVideoClipTask } from '../api/video'
import type { VideoAssemblyCreateRequest } from '../api/video'
import {
  createEpisodePlanTask,
  createEpisodeTaskPlan,
  createEpisodeScriptTask,
  createEpisodeShotTask,
  createStoryBibleTask,
  getEpisodes,
  getNovelChapters,
  getNovelProject,
  startProductionRun,
  uploadNovelSource,
} from '../api/novels'
import { getEpisodeScript, getEpisodeShots } from '../api/novelWorkbench'
import { getArtifact, getTasks } from '../api/tasks'
import { useProviderProfiles } from '../composables/useProviderProfiles'
import { friendlyErrorMessage, formatStatus as formatTaskStatus, formatTaskKind as formatTaskKindLabel, taskErrorDetail } from '../utils/taskStatus'
import MediaPreview from './MediaPreview.vue'
import ProductionQualityPanel from './ProductionQualityPanel.vue'
import type { EpisodeScriptRecord, ShotContent, ShotListRecord } from '../types/novel'
import type {
  ChapterRecord,
  EpisodeRecord,
  GenerationTaskRecord,
  ArtifactRecord,
  NovelProjectRecord,
  RightsStatus,
  TaskStatus,
  EpisodeTaskPlanResponse,
  ProductionRunResponse,
} from '../types/task'

type SubtitleMode = 'asr' | 'align'
type ShotFilter = 'all' | 'ready' | 'failed' | 'succeeded' | 'blocked'

type AssemblyClipSelection = {
  shotIndex: number
  task: GenerationTaskRecord
  artifact: ArtifactSummary
  source: 'video_clip' | 'lip_sync'
}

type RecentTaskGroup = {
  task: GenerationTaskRecord
  count: number
}

const props = defineProps<{ project: NovelProjectRecord }>()
type OperatorView = 'overview' | 'subtitle' | 'tasks' | 'queue' | 'artifacts' | 'workbench'

const emit = defineEmits<{
  back: []
  changed: []
  openOperator: [view?: OperatorView]
}>()

const { availableProfiles, selectedProfileId, isLoading: profilesLoading } = useProviderProfiles()

const projectData = ref(props.project)
const chapters = ref<ChapterRecord[]>([])
const episodes = ref<EpisodeRecord[]>([])
const planEpisodeIds = ref<string[]>([])
const planLabel = ref('分集生产计划')
const planResponse = ref<EpisodeTaskPlanResponse | null>(null)
const productionRun = ref<ProductionRunResponse | null>(null)
const tasks = ref<GenerationTaskRecord[]>([])
const selectedEpisodeId = ref<string | null>(null)
const selectedScript = ref<EpisodeScriptRecord | null>(null)
const selectedShotList = ref<ShotListRecord | null>(null)
const narrationText = ref('')
const narrationTextDirty = ref(false)
const subtitleMode = ref<SubtitleMode>('asr')
const subtitleText = ref('')
const subtitleTextDirty = ref(false)
const bgmLabel = ref('')
const bgmSourcePath = ref('')
const bgmRightsStatus = ref<RightsStatus>('unknown')
const bgmRightsHolder = ref('')
const bgmRightsReference = ref('')
const assemblyUseNarration = ref(true)
const assemblyUseBgm = ref(true)
const assemblyUseSubtitles = ref(true)
const assemblyBgmVolume = ref(0.18)
const assemblyUseLipSyncByShot = ref<Record<number, boolean>>({})
const shotFilter = ref<ShotFilter>('all')
const shotSearch = ref('')
const renderedVideoArtifactRecord = ref<ArtifactRecord | null>(null)
const renderedVideoArtifactLoading = ref(false)
const selectedFile = ref<File | null>(null)
const fileInput = ref<HTMLInputElement | null>(null)
const loading = ref(true)
const refreshing = ref(false)
const errorMessage = ref<string | null>(null)
const noticeMessage = ref<string | null>(null)
const action = ref<string | null>(null)
let pollTimer: number | null = null
let loadedRenderedVideoArtifactId: string | null = null

const selectedEpisode = computed(() => episodes.value.find((episode) => episode.id === selectedEpisodeId.value) ?? null)
const sourceReady = computed(() => Boolean(projectData.value.source_id))
const storyBibleTask = computed(() => latestTask('novel_story_bible'))
const storyBibleReady = computed(() => Boolean(projectData.value.story_bible_id) || storyBibleTask.value?.status === 'succeeded')
const episodePlanTask = computed(() => latestTask('novel_episode_plan'))
const episodesReady = computed(() => episodes.value.length > 0)
const planCanSubmit = computed(() => planEpisodeIds.value.length > 0 && !action.value)
const productionRunCanSubmit = computed(() => sourceReady.value && !action.value)
const scriptTask = computed(() => selectedEpisodeId.value ? latestTask('novel_episode_script', selectedEpisodeId.value) : null)
const shotTask = computed(() => selectedEpisodeId.value ? latestTask('novel_shot_list', selectedEpisodeId.value) : null)
const audioTask = computed(() => selectedEpisodeId.value ? latestTask('audio_narration', selectedEpisodeId.value) : null)
const completedAudioTask = computed(() => selectedEpisodeId.value ? latestSucceededTask('audio_narration', selectedEpisodeId.value) : null)
const audioArtifact = computed(() => completedAudioTask.value?.artifacts.find((artifact) => artifact.type === 'audio_narration') ?? null)
const subtitleTask = computed(() => selectedEpisodeId.value ? latestTaskByKinds(['subtitle_asr', 'subtitle_align'], selectedEpisodeId.value) : null)
const completedSubtitleTask = computed(() => selectedEpisodeId.value ? latestSucceededTaskByKinds(['subtitle_asr', 'subtitle_align'], selectedEpisodeId.value) : null)
const subtitleArtifact = computed(() => completedSubtitleTask.value?.artifacts.find((artifact) => artifact.type === 'subtitle_srt') ?? null)
const bgmTask = computed(() => selectedEpisodeId.value ? latestTask('audio_bgm', selectedEpisodeId.value) : null)
const completedBgmTask = computed(() => selectedEpisodeId.value ? latestSucceededTask('audio_bgm', selectedEpisodeId.value) : null)
const bgmArtifact = computed(() => completedBgmTask.value?.artifacts.find((artifact) => artifact.type === 'audio_bgm') ?? null)
const videoClipTasks = computed(() => selectedEpisodeId.value
  ? tasks.value
    .filter((task) => task.kind === 'video_clip' && String(task.input_data.episode_id ?? '') === selectedEpisodeId.value)
    .sort((left, right) => right.updated_at.localeCompare(left.updated_at))
  : [])
const videoClipReadyCount = computed(() => selectedShotList.value?.shots.filter((shot) => isShotReady(shot)).length ?? 0)
const successfulVideoClipTasks = computed(() => {
  const taskByShot = new Map<number, GenerationTaskRecord>()
  for (const task of videoClipTasks.value) {
    const shotIndex = Number(task.input_data.shot_index)
    const hasArtifact = task.artifacts.some((artifact) => artifact.type === 'video_clip')
    if (task.status === 'succeeded' && Number.isInteger(shotIndex) && hasArtifact && !taskByShot.has(shotIndex)) {
      taskByShot.set(shotIndex, task)
    }
  }
  return [...taskByShot.values()].sort((left, right) => Number(left.input_data.shot_index) - Number(right.input_data.shot_index))
})
const videoClipSucceededCount = computed(() => successfulVideoClipTasks.value.length)
const shotTotalCount = computed(() => selectedShotList.value?.shots.length ?? 0)
const shotFailedCount = computed(() => selectedShotList.value?.shots.filter((shot) => {
  const task = latestVideoClipTask(shot.shot_index)
  return task?.status === 'failed' && !videoClipArtifact(shot.shot_index)
}).length ?? 0)
const shotSucceededCount = computed(() => selectedShotList.value?.shots.filter((shot) => {
  const task = latestVideoClipTask(shot.shot_index)
  return task?.status === 'succeeded' && Boolean(task.artifacts.some((artifact) => artifact.type === 'video_clip'))
}).length ?? 0)
const shotBlockedCount = computed(() => selectedShotList.value?.shots.filter((shot) => !isShotReady(shot)).length ?? 0)
const shotFilterOptions = computed(() => [
  { key: 'all' as ShotFilter, label: '全部', count: shotTotalCount.value },
  { key: 'ready' as ShotFilter, label: '待生成', count: Math.max(0, videoClipReadyCount.value - shotSucceededCount.value) },
  { key: 'failed' as ShotFilter, label: '生成失败', count: shotFailedCount.value },
  { key: 'succeeded' as ShotFilter, label: '已完成', count: shotSucceededCount.value },
  { key: 'blocked' as ShotFilter, label: '需补资产', count: shotBlockedCount.value },
])
const filteredShots = computed(() => {
  const query = shotSearch.value.trim().toLowerCase()
  return (selectedShotList.value?.shots ?? []).filter((shot) => {
    const task = latestVideoClipTask(shot.shot_index)
    const hasArtifact = Boolean(videoClipArtifact(shot.shot_index))
    const matchesFilter = shotFilter.value === 'all'
      || (shotFilter.value === 'ready' && isShotReady(shot) && !hasArtifact && task?.status !== 'failed')
      || (shotFilter.value === 'failed' && task?.status === 'failed' && !hasArtifact)
      || (shotFilter.value === 'succeeded' && task?.status === 'succeeded' && hasArtifact)
      || (shotFilter.value === 'blocked' && !isShotReady(shot))
    if (!matchesFilter) return false
    if (!query) return true
    return [shot.shot_index, shot.scene_index, shot.shot_size, shot.camera_movement, shot.visual_prompt]
      .join(' ')
      .toLowerCase()
      .includes(query)
  })
})
const assemblyLipSyncCandidates = computed(() => {
  const lipSyncByVideoArtifact = new Map<string, { task: GenerationTaskRecord; artifact: ArtifactSummary }>()
  for (const task of tasks.value) {
    if (task.kind !== 'lip_sync' || task.status !== 'succeeded' || String(task.input_data.episode_id ?? '') !== selectedEpisodeId.value) continue
    const artifact = task.artifacts.find((item) => item.type === 'lip_synced_video')
    const sourceArtifactId = String(task.input_data.video_artifact_id ?? artifact?.metadata.video_artifact_id ?? '')
    if (!artifact || !sourceArtifactId) continue
    const existing = lipSyncByVideoArtifact.get(sourceArtifactId)
    if (!existing || existing.task.updated_at.localeCompare(task.updated_at) < 0) {
      lipSyncByVideoArtifact.set(sourceArtifactId, { task, artifact })
    }
  }
  return lipSyncByVideoArtifact
})

const assemblyClipSelections = computed<AssemblyClipSelection[]>(() => {

  return successfulVideoClipTasks.value.map((task) => {
    const videoArtifact = task.artifacts.find((artifact) => artifact.type === 'video_clip') as ArtifactSummary
    const lipSync = assemblyLipSyncCandidates.value.get(videoArtifact.id)
    const shotIndex = Number(task.input_data.shot_index)
    const useLipSync = Boolean(lipSync && (assemblyUseLipSyncByShot.value[shotIndex] ?? true))
    const selectedLipSync = useLipSync && lipSync ? lipSync : null
    return {
      shotIndex,
      task: selectedLipSync?.task ?? task,
      artifact: selectedLipSync?.artifact ?? videoArtifact,
      source: selectedLipSync ? 'lip_sync' : 'video_clip',
    }
  })
})
const videoAssemblyTask = computed(() => selectedEpisodeId.value ? latestTask('video_assembly', selectedEpisodeId.value) : null)
const completedVideoAssemblyTask = computed(() => selectedEpisodeId.value ? latestSucceededTask('video_assembly', selectedEpisodeId.value) : null)
const renderedVideoArtifact = computed(() => completedVideoAssemblyTask.value?.artifacts.find((artifact) => artifact.type === 'rendered_video') ?? null)
const assemblyCanSubmit = computed(() => Boolean(
  selectedEpisode.value
  && assemblyClipSelections.value.length >= 2
  && !action.value
  && !(videoAssemblyTask.value && isActive(videoAssemblyTask.value.status)),
))
const audioTracksForAssemblyCount = computed(() => (
  Number(Boolean(assemblyUseNarration.value && audioArtifact.value))
  + Number(Boolean(assemblyUseBgm.value && bgmArtifact.value))
))
const audioDurationSeconds = computed(() => positiveNumber(audioArtifact.value?.metadata.duration_seconds))
const scriptReady = computed(() => Boolean(selectedScript.value) || selectedScriptTask.value?.status === 'succeeded')
const selectedScriptTask = computed(() => scriptTask.value)
const activeTaskCount = computed(() => tasks.value.filter((task) => isActive(task.status)).length)
const recentTaskGroups = computed<RecentTaskGroup[]>(() => {
  const groups = new Map<string, RecentTaskGroup>()
  const orderedTasks = [...tasks.value].sort((left, right) => right.updated_at.localeCompare(left.updated_at))
  for (const task of orderedTasks) {
    const errorCode = task.error?.code ?? ''
    const key = `${task.kind}:${task.status}:${errorCode}`
    const existing = groups.get(key)
    if (existing) existing.count += 1
    else groups.set(key, { task, count: 1 })
  }
  return [...groups.values()].slice(0, 6)
})
const selectedEpisodeTasks = computed(() => selectedEpisodeId.value
  ? tasks.value.filter((task) => String(task.input_data.episode_id ?? '') === selectedEpisodeId.value)
  : [])
const selectedEpisodeActiveTasks = computed(() => selectedEpisodeTasks.value.filter((task) => isActive(task.status)))
const shotListReady = computed(() => Boolean(selectedShotList.value) || shotTask.value?.status === 'succeeded')
const workflowFailureTask = computed(() => {
  const stageTasks: Array<{ task: GenerationTaskRecord | null; blocks: boolean }> = [
    { task: storyBibleTask.value, blocks: !storyBibleReady.value },
    { task: episodePlanTask.value, blocks: !episodesReady.value },
    { task: scriptTask.value, blocks: !scriptReady.value },
    { task: shotTask.value, blocks: !shotListReady.value },
    { task: audioTask.value, blocks: !audioArtifact.value },
    { task: subtitleTask.value, blocks: !subtitleArtifact.value },
    ...videoClipTasks.value.map((task) => ({
      task,
      blocks: !videoClipArtifact(Number(task.input_data.shot_index)),
    })),
    { task: videoAssemblyTask.value, blocks: assemblyClipSelections.value.length >= 2 && !renderedVideoArtifact.value },
  ]
  return stageTasks.find(({ task, blocks }) => blocks && task?.status === 'failed')?.task ?? null
})
const workflowStageKey = computed(() => {
  if (!sourceReady.value || !storyBibleReady.value || !episodesReady.value) return 'content'
  if (!selectedEpisode.value || !scriptReady.value || !shotListReady.value) return 'script'
  if (!audioArtifact.value || !subtitleArtifact.value || !selectedShotList.value || videoClipSucceededCount.value < videoClipReadyCount.value) return 'media'
  return 'final'
})
const workflowStageLabels: Record<string, string> = {
  content: '内容理解',
  script: '剧本与分镜',
  media: '媒体生成',
  final: '审核与成片',
}
const workflowStageLabel = computed(() => workflowStageLabels[workflowStageKey.value] ?? '内容理解')
const workflowStageDetail = computed(() => {
  if (workflowFailureTask.value) {
    const task = workflowFailureTask.value
    return `${formatTaskKind(task.kind)}失败：${friendlyErrorMessage(task.error)} 当前页面保留了失败记录，可以在对应步骤重试。`
  }
  if (workflowStageKey.value === 'content') return '先完成原文、故事设定和分集大纲，后续任务才有稳定输入。'
  if (workflowStageKey.value === 'script') return '当前集需要完成剧本和分镜，并在进入媒体生成前检查资产绑定。'
  if (workflowStageKey.value === 'media') return selectedEpisodeActiveTasks.value.length
    ? `正在准备旁白、字幕和逐镜头画面；当前有 ${selectedEpisodeActiveTasks.value.length} 个任务处理中。`
    : '正在准备旁白、字幕和逐镜头画面；每个耗时任务都可单独重试。'
  return videoAssemblyTask.value?.status === 'succeeded' ? '成片已生成，建议先人工复核音画、字幕和角色身份。' : '等待所有媒体通过校验后进入最终成片合成。'
})
const mediaPhaseDetail = computed(() => {
  if (!selectedShotList.value) return '等待分镜审核'
  const total = selectedShotList.value.shots.length
  const pieces = `${videoClipSucceededCount.value}/${total} 个片段`
  if (shotFailedCount.value > 0) return `${pieces} · ${shotFailedCount.value} 个失败`
  if (videoClipReadyCount.value < total) return `${pieces} · ${total - videoClipReadyCount.value} 个镜头待补资产`
  return `${pieces} · 逐镜头画面`
})
const workflowCoreSteps = computed(() => [
  sourceReady.value,
  storyBibleReady.value,
  episodesReady.value,
  scriptReady.value,
  shotListReady.value,
  Boolean(audioArtifact.value),
  Boolean(subtitleArtifact.value),
  Boolean(selectedShotList.value) && videoClipReadyCount.value > 0 && videoClipSucceededCount.value >= videoClipReadyCount.value,
  videoAssemblyTask.value?.status === 'succeeded',
])
const workflowCoreCompletedCount = computed(() => workflowCoreSteps.value.filter(Boolean).length)
const workflowPhaseCards = computed(() => [
  {
    key: 'content',
    label: '内容理解',
    detail: sourceReady.value ? `${chapters.value.length} 个章节 · ${episodes.value.length ? `${episodes.value.length} 集大纲` : '等待分集大纲'}` : '上传原文、分析设定、拆分分集',
    complete: sourceReady.value && storyBibleReady.value && episodesReady.value,
    active: workflowStageKey.value === 'content',
    target: 'creator-step-source',
  },
  {
    key: 'script',
    label: '剧本与分镜',
    detail: selectedEpisode.value ? `${scriptReady.value ? '剧本已就绪' : '等待剧本'} · ${shotListReady.value ? '分镜已提交' : '等待分镜'}` : '选择分集并生成可审核的剧本',
    complete: scriptReady.value && shotListReady.value,
    active: workflowStageKey.value === 'script',
    target: 'creator-step-script',
  },
  {
    key: 'media',
    label: '媒体生成',
    detail: `${audioArtifact.value ? '旁白' : '旁白待生成'} · ${subtitleArtifact.value ? '字幕' : '字幕待生成'} · ${mediaPhaseDetail.value}`,
    complete: Boolean(audioArtifact.value) && Boolean(subtitleArtifact.value) && videoClipReadyCount.value > 0 && videoClipSucceededCount.value >= videoClipReadyCount.value,
    active: workflowStageKey.value === 'media',
    target: 'creator-step-audio',
  },
  {
    key: 'final',
    label: '审核与成片',
    detail: videoAssemblyTask.value?.status === 'succeeded' ? '成片已生成 · 可预览下载' : '人工复核后合成最终成片',
    complete: videoAssemblyTask.value?.status === 'succeeded',
    active: workflowStageKey.value === 'final',
    target: 'creator-step-assembly',
  },
])
const workflowBlocker = computed(() => {
  if (productionRun.value?.status === 'blocked') return productionRun.value.message
  if (productionRun.value?.status === 'failed') return `自动生产 Run 失败：${productionRun.value.message} 请从失败步骤重试。`
  if (workflowFailureTask.value) {
    const task = workflowFailureTask.value
    if (task.kind === 'video_clip') return `视频片段生成失败：${friendlyErrorMessage(task.error)} 当前有 ${shotFailedCount.value} 个失败镜头，可只重试失败镜头。`
    if (task.kind === 'asset_reference_image') return `参考图生成失败：${friendlyErrorMessage(task.error)} 请先恢复参考图任务，再继续生成视频。`
    return `${formatTaskKind(task.kind)}失败：${friendlyErrorMessage(task.error)} 请在对应步骤重新提交。`
  }
  if (!sourceReady.value) return '尚未上传小说原文。'
  if (!storyBibleReady.value) return storyBibleTask.value && isActive(storyBibleTask.value.status) ? '故事设定任务正在处理中。' : '等待启动故事设定分析。'
  if (!episodesReady.value) return episodePlanTask.value && isActive(episodePlanTask.value.status) ? '分集大纲任务正在处理中。' : '等待生成分集大纲。'
  if (!selectedEpisode.value) return '请选择要制作的分集。'
  if (!scriptReady.value) return scriptTask.value && isActive(scriptTask.value.status) ? '分场剧本任务正在处理中。' : '等待生成分场剧本。'
  if (!shotListReady.value) return shotTask.value && isActive(shotTask.value.status) ? '分镜任务正在处理中。' : '需要先生成分镜任务。'
  if (!audioArtifact.value) return audioTask.value && isActive(audioTask.value.status) ? '旁白任务正在处理中。' : '等待生成单集旁白。'
  if (!subtitleArtifact.value) return subtitleTask.value && isActive(subtitleTask.value.status) ? '字幕任务正在处理中。' : '等待生成并审核字幕。'
  if (!selectedShotList.value) return '分镜数据尚未载入，请刷新工作区。'
  if (videoClipReadyCount.value < selectedShotList.value.shots.length) return `还有 ${selectedShotList.value.shots.length - videoClipReadyCount.value} 个镜头需要资产审核或绑定。`
  if (videoClipSucceededCount.value < videoClipReadyCount.value) return `还有 ${videoClipReadyCount.value - videoClipSucceededCount.value} 个视频片段未完成。`
  if (videoAssemblyTask.value?.status !== 'succeeded') return assemblyClipSelections.value.length >= 2 ? '媒体已就绪，可以提交成片合成。' : '等待至少两个成功的视频片段。'
  return '当前核心流程已完成，可预览并下载成片。'
})
const failedTaskCount = computed(() => {
  const task = workflowFailureTask.value
  if (!task) return 0
  return task.kind === 'video_clip' ? Math.max(1, shotFailedCount.value) : 1
})
const workspaceStatus = computed(() => {
  if (failedTaskCount.value > 0) return { label: `${selectedEpisode.value ? '本集 ' : ''}${failedTaskCount.value} 个步骤需处理`, className: 'attention' }
  if (renderedVideoArtifact.value) return { label: '成片已就绪', className: 'ready' }
  if (activeTaskCount.value > 0) return { label: '正在生产', className: 'analyzing' }
  if (workflowStageKey.value === 'content') return { label: '等待内容', className: 'pending' }
  return { label: '制作进行中', className: 'pending' }
})
const nextWorkflowStep = computed(() => {
  if (!sourceReady.value) return { label: '上传小说', target: 'creator-step-source' }
  if (storyBibleTask.value?.status === 'failed' && !storyBibleReady.value) return { label: '重试故事设定', target: 'creator-step-story' }
  if (!storyBibleReady.value) return { label: '分析故事设定', target: 'creator-step-story' }
  if (episodePlanTask.value?.status === 'failed' && !episodesReady.value) return { label: '重试分集大纲', target: 'creator-step-episode-plan' }
  if (!episodesReady.value) return { label: '生成分集大纲', target: 'creator-step-episodes' }
  if (!selectedEpisode.value) return { label: '选择分集', target: 'creator-step-episodes' }
  if (scriptTask.value?.status === 'failed' && !scriptReady.value) return { label: '重试分场剧本', target: 'creator-step-script' }
  if (!scriptReady.value) return { label: '生成分场剧本', target: 'creator-step-script' }
  if (shotTask.value?.status === 'failed' && !shotListReady.value) return { label: '重试分镜任务', target: 'creator-step-shots' }
  if (!shotListReady.value) return { label: '生成分镜任务', target: 'creator-step-shots' }
  if (audioTask.value?.status === 'failed' && !audioArtifact.value) return { label: '重试旁白', target: 'creator-step-audio' }
  if (!audioArtifact.value) return { label: '生成旁白', target: 'creator-step-audio' }
  if (subtitleTask.value?.status === 'failed' && !subtitleArtifact.value) return { label: '重试字幕', target: 'creator-step-subtitle' }
  if (!subtitleArtifact.value) return { label: '创建字幕', target: 'creator-step-subtitle' }
  if (shotFailedCount.value > 0) return { label: '处理失败镜头', target: 'creator-step-video' }
  if (!selectedShotList.value || videoClipReadyCount.value < selectedShotList.value.shots.length || videoClipSucceededCount.value < videoClipReadyCount.value) return { label: '处理并生成画面片段', target: 'creator-step-video' }
  if (videoAssemblyTask.value?.status === 'failed' && !renderedVideoArtifact.value) return { label: '重试成片合成', target: 'creator-step-assembly' }
  if (videoAssemblyTask.value?.status !== 'succeeded') return { label: '生成成片', target: 'creator-step-assembly' }
  return { label: '查看成片', target: 'creator-step-assembly' }
})
const subtitleCanSubmit = computed(() => {
  if (!selectedEpisode.value || !audioArtifact.value || !subtitleText.value.trim() || action.value) return false
  if (subtitleTask.value && isActive(subtitleTask.value.status)) return false
  return subtitleMode.value === 'asr'
    ? Boolean(selectedProfileId.value)
    : audioDurationSeconds.value > 0
})
const bgmCanSubmit = computed(() => Boolean(
  selectedEpisode.value
  && bgmLabel.value.trim()
  && bgmRightsStatus.value !== 'denied'
  && !action.value
  && !(bgmTask.value && isActive(bgmTask.value.status)),
))

function isActive(status: TaskStatus) {
  return ['created', 'queued', 'running'].includes(status)
}

function latestTask(kind: string, episodeId?: string): GenerationTaskRecord | null {
  const matching = tasks.value
    .filter((task) => task.kind === kind)
    .filter((task) => !episodeId || String(task.input_data.episode_id ?? '') === episodeId)
    .sort((left, right) => right.updated_at.localeCompare(left.updated_at))
  return matching[0] ?? null
}

function latestTaskByKinds(kinds: string[], episodeId?: string): GenerationTaskRecord | null {
  const matching = tasks.value
    .filter((task) => kinds.includes(task.kind))
    .filter((task) => !episodeId || String(task.input_data.episode_id ?? '') === episodeId)
    .sort((left, right) => right.updated_at.localeCompare(left.updated_at))
  return matching[0] ?? null
}

function latestSucceededTask(kind: string, episodeId?: string): GenerationTaskRecord | null {
  return latestSucceededTaskByKinds([kind], episodeId)
}

function latestSucceededTaskByKinds(kinds: string[], episodeId?: string): GenerationTaskRecord | null {
  return tasks.value
    .filter((task) => kinds.includes(task.kind) && task.status === 'succeeded')
    .filter((task) => !episodeId || String(task.input_data.episode_id ?? '') === episodeId)
    .sort((left, right) => right.updated_at.localeCompare(left.updated_at))[0] ?? null
}

function formatTaskKind(kind: string) {
  return formatTaskKindLabel(kind)
}

function formatStatus(status: TaskStatus) {
  return formatTaskStatus(status)
}

function formatBytes(size: number) {
  if (size < 1024) return `${size} B`
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`
  return `${(size / (1024 * 1024)).toFixed(1)} MB`
}

function formatTime(value: string) {
  const date = new Date(value)
  return Number.isNaN(date.getTime())
    ? value
    : date.toLocaleString('zh-CN', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
}

function formatAudioDuration(value: unknown) {
  const seconds = typeof value === 'number' ? value : Number(value)
  return Number.isFinite(seconds) && seconds > 0 ? `${seconds.toFixed(1)} 秒` : '音频已校验'
}

function positiveNumber(value: unknown) {
  const numeric = typeof value === 'number' ? value : Number(value)
  return Number.isFinite(numeric) && numeric > 0 ? numeric : 0
}

function scrollToWorkflowStep(target: string) {
  document.getElementById(target)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
}

function focusFailedShots() {
  shotFilter.value = 'failed'
  shotSearch.value = ''
  scrollToWorkflowStep('creator-step-video')
}

function subtitleTextFromAudioTask() {
  const taskText = completedAudioTask.value?.input_data.text
  return typeof taskText === 'string' && taskText.trim() ? taskText.trim() : narrationText.value.trim()
}

function syncSubtitleTextFromAudio() {
  if (!subtitleTextDirty.value && completedAudioTask.value) {
    subtitleText.value = subtitleTextFromAudioTask()
  }
}

function formatSubtitleCueCount(value: unknown) {
  const count = positiveNumber(value)
  return count > 0 ? `${Math.floor(count)} 条字幕` : '字幕已生成'
}

function formatBgmSourceType(value: unknown) {
  return typeof value === 'string' && value.trim() ? value : '已登记来源'
}

function latestVideoClipTask(shotIndex: number): GenerationTaskRecord | null {
  return videoClipTasks.value.find((task) => Number(task.input_data.shot_index) === shotIndex) ?? null
}

function lipSyncCandidateForShot(shotIndex: number) {
  const task = successfulVideoClipTask(shotIndex)
  const artifact = task?.artifacts.find((item) => item.type === 'video_clip')
  return artifact ? assemblyLipSyncCandidates.value.get(artifact.id) ?? null : null
}

function setAssemblySource(shotIndex: number, event: Event) {
  const value = (event.target as HTMLSelectElement).value
  assemblyUseLipSyncByShot.value = {
    ...assemblyUseLipSyncByShot.value,
    [shotIndex]: value === 'lip_sync',
  }
}

function videoClipArtifact(shotIndex: number) {
  return successfulVideoClipTask(shotIndex)?.artifacts.find((artifact) => artifact.type === 'video_clip')
    ?? latestVideoClipTask(shotIndex)?.artifacts.find((artifact) => artifact.type === 'video_clip')
    ?? null
}

function successfulVideoClipTask(shotIndex: number) {
  return successfulVideoClipTasks.value.find((task) => Number(task.input_data.shot_index) === shotIndex) ?? null
}

function isShotReady(shot: ShotContent) {
  return shot.unresolved_asset_requirements.length === 0
    && shot.asset_binding_warnings.length === 0
    && shot.asset_refs.every((reference) => reference.status === 'ready')
}

function formatShotGate(shot: ShotContent) {
  if (isShotReady(shot)) return '已审核，可生成'
  if (shot.unresolved_asset_requirements.length) return `待补资产 ${shot.unresolved_asset_requirements.length}`
  if (shot.asset_binding_warnings.length) return `绑定警告 ${shot.asset_binding_warnings.length}`
  return '待审核资产'
}

function normalizeNarrationText(text: string): string {
  return text
    .replace(/[\u200B\u200C\u200D\u2060\uFEFF]/g, '')
    .replace(/\s+/g, ' ')
    .trim()
}

function ensureNarrationUnitBoundary(text: string): string {
  const cleaned = normalizeNarrationText(text)
  if (!cleaned) return ''
  if (/[。！？!?；;…]$/.test(cleaned)) return cleaned
  return `${cleaned.replace(/[，,、：:]$/, '')}。`
}

function buildNarrationText(script: EpisodeScriptRecord): string {
  const units = script.content.scenes.flatMap((scene) => [
    scene.narration,
    ...scene.dialogues.map((dialogue) => dialogue.text),
  ])
  return units.map(ensureNarrationUnitBoundary).filter(Boolean).join('').slice(0, 5000)
}

function displayError(error: unknown) {
  if (error instanceof ApiClientError) return taskErrorDetail({ code: error.code, message: error.message })
  return '工作区暂时无法读取，请稍后重试。'
}

function newIdempotencyKey(actionName: string) {
  const randomId = globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(16).slice(2)}`
  return `creator:${projectData.value.id}:${actionName}:${randomId}`
}

async function loadSelectedScript(episodeId: string | null) {
  const previousScriptId = selectedScript.value?.id
  selectedScript.value = null
  if (!episodeId) return
  try {
    const script = await getEpisodeScript(episodeId)
    selectedScript.value = script
    if (!narrationTextDirty.value || previousScriptId !== script.id) {
      narrationText.value = buildNarrationText(script)
      narrationTextDirty.value = false
    }
  } catch (error) {
    if (error instanceof ApiClientError && error.code === 'EPISODE_SCRIPT_NOT_FOUND') return
    throw error
  }
}

async function loadSelectedShots(episodeId: string | null) {
  selectedShotList.value = null
  if (!episodeId) return
  try {
    selectedShotList.value = await getEpisodeShots(episodeId)
  } catch (error) {
    if (error instanceof ApiClientError && error.code === 'SHOT_LIST_NOT_FOUND') return
    throw error
  }
}

async function startNarration() {
  if (!selectedEpisode.value || !narrationText.value.trim()) return
  await runAction('audio-narration', '旁白任务已提交，完成后可以继续生成字幕。', () =>
    createAudioNarrationTask(
      selectedEpisode.value?.id ?? '',
      { text: narrationText.value.trim() },
      newIdempotencyKey(`audio-narration-${selectedEpisode.value?.id}`),
    ),
  )
}

async function startSubtitle() {
  if (!selectedEpisode.value || !audioArtifact.value || !subtitleCanSubmit.value) return
  const text = subtitleText.value.trim()
  const actionName = subtitleMode.value === 'asr' ? 'subtitle-asr' : 'subtitle-align'
  const successMessage = subtitleMode.value === 'asr'
    ? 'ASR 字幕任务已提交，完成后请审核时间轴与转写结果。'
    : '字幕对齐任务已提交，生成的是句子级估算时间轴，请人工复核。'
  await runAction(actionName, successMessage, () => subtitleMode.value === 'asr'
    ? createSubtitleASRTask(
      selectedEpisode.value?.id ?? '',
      {
        audio_artifact_id: audioArtifact.value?.id ?? '',
        language: projectData.value.language,
        reference_text: text,
        provider_profile_id: selectedProfileId.value ?? undefined,
      },
      newIdempotencyKey(`subtitle-asr-${selectedEpisode.value?.id}`),
    )
    : createSubtitleAlignmentTask(
      selectedEpisode.value?.id ?? '',
      {
        text,
        language: projectData.value.language,
        audio_duration_seconds: audioDurationSeconds.value,
      },
      newIdempotencyKey(`subtitle-align-${selectedEpisode.value?.id}`),
    ),
  )
}

async function startBgm() {
  if (!selectedEpisode.value || !bgmCanSubmit.value) return
  await runAction('audio-bgm', 'BGM 任务已提交，完成后可与旁白、字幕一起进入成片编排。', () =>
    createAudioBGMTask(
      selectedEpisode.value?.id ?? '',
      {
        source_path: bgmSourcePath.value.trim() || undefined,
        label: bgmLabel.value.trim(),
        rights_status: bgmRightsStatus.value,
        rights_holder: bgmRightsHolder.value.trim() || undefined,
        rights_reference: bgmRightsReference.value.trim() || undefined,
      },
      newIdempotencyKey(`audio-bgm-${selectedEpisode.value?.id}`),
    ),
  )
}

async function startVideoClip(shot: ShotContent) {
  const task = latestVideoClipTask(shot.shot_index)
  if (!selectedEpisode.value || !isShotReady(shot) || (task && isActive(task.status))) return
  await runAction(
    `video-clip-${shot.shot_index}`,
    `第 ${shot.shot_index} 个镜头的视频片段任务已提交，完成后请审核画面。`,
    () => createVideoClipTask(
      selectedEpisode.value?.id ?? '',
      shot.shot_index,
      {},
      newIdempotencyKey(`video-clip-${selectedEpisode.value?.id}-${shot.shot_index}`),
    ),
  )
}

async function startVideoAssembly() {
  if (!selectedEpisode.value || !assemblyCanSubmit.value) return
  const audioTracks: NonNullable<VideoAssemblyCreateRequest['audio_tracks']> = []
  if (assemblyUseNarration.value && audioArtifact.value) {
    audioTracks.push({
      artifact_id: audioArtifact.value.id,
      track_type: 'narration',
      start_seconds: 0,
      volume: 1,
    })
  }
  if (assemblyUseBgm.value && bgmArtifact.value) {
    audioTracks.push({
      artifact_id: bgmArtifact.value.id,
      track_type: 'bgm',
      start_seconds: 0,
      volume: Math.min(2, Math.max(0, Number(assemblyBgmVolume.value) || 0.18)),
      loop: true,
      fade_in_seconds: 1,
      fade_out_seconds: 2,
    })
  }
  const payload: VideoAssemblyCreateRequest = {
    clip_task_ids: assemblyClipSelections.value.map((selection) => selection.task.id),
    audio_tracks: audioTracks,
    subtitle_artifact_id: assemblyUseSubtitles.value && subtitleArtifact.value ? subtitleArtifact.value.id : undefined,
    output_format: 'mp4',
  }
  await runAction('video-assembly', '成片合成任务已提交，完成后可在这里预览，也可在媒体资产中下载。', () =>
    createVideoAssemblyTask(
      selectedEpisode.value?.id ?? '',
      payload,
      newIdempotencyKey(`video-assembly-${selectedEpisode.value?.id}`),
    ),
  )
}

async function loadRenderedVideoArtifact() {
  const summary = renderedVideoArtifact.value
  if (!summary) {
    renderedVideoArtifactRecord.value = null
    loadedRenderedVideoArtifactId = null
    return
  }
  if (loadedRenderedVideoArtifactId === summary.id && renderedVideoArtifactRecord.value) return
  renderedVideoArtifactLoading.value = true
  try {
    renderedVideoArtifactRecord.value = await getArtifact(summary.id, 3600)
    loadedRenderedVideoArtifactId = summary.id
  } catch (error) {
    renderedVideoArtifactRecord.value = null
    loadedRenderedVideoArtifactId = null
    errorMessage.value = displayError(error)
  } finally {
    renderedVideoArtifactLoading.value = false
  }
}

function resetBgmForm(episode: EpisodeRecord | null) {
  bgmLabel.value = episode ? `第 ${episode.episode_number} 集 BGM` : ''
  bgmSourcePath.value = ''
  bgmRightsStatus.value = 'unknown'
  bgmRightsHolder.value = ''
  bgmRightsReference.value = ''
}

async function refreshWorkspace() {
  if (refreshing.value) return
  refreshing.value = true
  errorMessage.value = null
  try {
    const currentProject = await getNovelProject(props.project.id)
    const [episodeItems, taskResponse, chapterItems] = await Promise.all([
      getEpisodes(currentProject.id),
      getTasks({ projectId: currentProject.id, limit: 80 }),
      currentProject.source_id ? getNovelChapters(currentProject.id) : Promise.resolve([]),
    ])
    projectData.value = currentProject
    episodes.value = episodeItems
    const availablePlanIds = new Set(episodeItems.map((episode) => episode.id))
    const retainedPlanIds = planEpisodeIds.value.filter((episodeId) => availablePlanIds.has(episodeId))
    planEpisodeIds.value = retainedPlanIds.length ? retainedPlanIds : episodeItems.map((episode) => episode.id)
    tasks.value = taskResponse.items
    chapters.value = chapterItems

    const selectedStillExists = selectedEpisodeId.value && episodeItems.some((episode) => episode.id === selectedEpisodeId.value)
    const nextSelectedEpisodeId = selectedStillExists ? selectedEpisodeId.value : episodeItems[0]?.id ?? null
    if (nextSelectedEpisodeId !== selectedEpisodeId.value) {
      resetBgmForm(episodeItems.find((episode) => episode.id === nextSelectedEpisodeId) ?? null)
    }
    selectedEpisodeId.value = nextSelectedEpisodeId
    syncSubtitleTextFromAudio()
    await loadSelectedScript(selectedEpisodeId.value)
    await loadSelectedShots(selectedEpisodeId.value)
    await loadRenderedVideoArtifact()
  } catch (error) {
    errorMessage.value = displayError(error)
  } finally {
    loading.value = false
    refreshing.value = false
  }
}

async function runAction(actionName: string, successMessage: string, work: () => Promise<unknown>) {
  if (action.value) return
  action.value = actionName
  errorMessage.value = null
  noticeMessage.value = null
  try {
    await work()
    noticeMessage.value = successMessage
    await refreshWorkspace()
    emit('changed')
  } catch (error) {
    errorMessage.value = displayError(error)
  } finally {
    action.value = null
  }
}

function onFileChange(event: Event) {
  const input = event.target as HTMLInputElement
  selectedFile.value = input.files?.[0] ?? null
}

async function uploadSource() {
  if (!selectedFile.value) return
  await runAction('upload', '小说已上传并完成章节切分。下一步可以开始分析故事设定。', async () => {
    await uploadNovelSource(projectData.value.id, selectedFile.value as File, projectData.value.rights_status)
    selectedFile.value = null
    if (fileInput.value) fileInput.value.value = ''
  })
}

async function startStoryBible() {
  await runAction('story-bible', '故事设定分析任务已提交，完成后会自动进入分集大纲阶段。', () =>
    createStoryBibleTask(projectData.value.id, newIdempotencyKey('story-bible')),
  )
}

async function startEpisodePlan() {
  await runAction('episode-plan', '分集大纲任务已提交，完成后可以选择具体分集。', () =>
    createEpisodePlanTask(projectData.value.id, projectData.value.target_episode_count, newIdempotencyKey('episode-plan')),
  )
}

async function startEpisodeTaskPlan() {
  if (!planCanSubmit.value) return
  await runAction('episode-task-plan', '分集任务计划已创建，任务已进入生产任务中心。', async () => {
    planResponse.value = await createEpisodeTaskPlan(
      projectData.value.id,
      {
        episode_ids: planEpisodeIds.value,
        label: planLabel.value.trim() || '分集生产计划',
        production_mode: true,
        include_reference_images: true,
        include_narration: true,
        include_subtitles: true,
        include_bgm: false,
        include_video: true,
        include_assembly: true,
        subtitle_mode: 'align',
      },
      newIdempotencyKey('episode-task-plan'),
    )
  })
}

async function startFullProduction() {
  if (!productionRunCanSubmit.value) return
  await runAction('production-run', '完整生产 Run 已启动，后续会由 Windows Worker 自动推进。', async () => {
    productionRun.value = await startProductionRun(
      projectData.value.id,
      {
        target_episode_count: projectData.value.target_episode_count,
        label: 'Windows 4060 Ti 完整生产',
        production_mode: true,
        include_reference_images: true,
        include_narration: true,
        include_subtitles: true,
        include_bgm: false,
        include_video: true,
        include_assembly: true,
        subtitle_mode: 'align',
        auto_advance: true,
      },
      `creator:${projectData.value.id}:production-run`,
    )
  })
}

async function startScript() {
  if (!selectedEpisode.value) return
  await runAction('episode-script', '分场剧本任务已提交，完成后可以在这里预览剧本。', () =>
    createEpisodeScriptTask(selectedEpisode.value?.id ?? '', newIdempotencyKey(`episode-script-${selectedEpisode.value?.id}`)),
  )
}

async function startShots() {
  if (!selectedEpisode.value) return
  await runAction('shot-list', '分镜任务已提交。完成后请进入制作后台审核资产绑定，再继续生成画面。', () =>
    createEpisodeShotTask(selectedEpisode.value?.id ?? '', newIdempotencyKey(`shot-list-${selectedEpisode.value?.id}`)),
  )
}

function selectEpisode(episodeId: string) {
  selectedEpisodeId.value = episodeId
  narrationText.value = ''
  narrationTextDirty.value = false
  subtitleText.value = ''
  subtitleTextDirty.value = false
  assemblyUseNarration.value = true
  assemblyUseBgm.value = true
  assemblyUseSubtitles.value = true
  assemblyBgmVolume.value = 0.18
  assemblyUseLipSyncByShot.value = {}
  shotFilter.value = 'all'
  shotSearch.value = ''
  resetBgmForm(episodes.value.find((episode) => episode.id === episodeId) ?? null)
  syncSubtitleTextFromAudio()
  void loadSelectedScript(episodeId).catch((error) => {
    errorMessage.value = displayError(error)
  })
  void loadSelectedShots(episodeId).catch((error) => {
    errorMessage.value = displayError(error)
  })
}

function openFilePicker() {
  fileInput.value?.click()
}

watch(() => props.project, (project) => {
  projectData.value = project
  void refreshWorkspace()
})

onMounted(() => {
  void refreshWorkspace()
  pollTimer = window.setInterval(() => {
    if (activeTaskCount.value > 0) void refreshWorkspace()
  }, 2500)
})

onUnmounted(() => {
  if (pollTimer !== null) window.clearInterval(pollTimer)
})
</script>

<template>
  <section class="creator-workspace-section" aria-labelledby="creator-workspace-title">
    <div class="creator-workspace-heading">
      <div>
        <button class="creator-back-link" type="button" @click="emit('back')">← 返回项目总览</button>
        <p class="creator-eyebrow">CREATOR WORKSPACE</p>
        <h2 id="creator-workspace-title">{{ projectData.title }}</h2>
        <p>从原文到剧本，再到分镜任务。每一步都可以查看状态，失败后可重试。</p>
      </div>
      <div class="creator-workspace-heading-actions">
        <span class="creator-workspace-status" :class="workspaceStatus.className">{{ workspaceStatus.label }}</span>
        <button class="creator-ghost-button" type="button" @click="emit('openOperator')">进入制作后台 <span>↗</span></button>
      </div>
    </div>

    <div class="creator-phase-strip" aria-label="生产阶段">
      <button v-for="(phase, index) in workflowPhaseCards" :key="phase.key" type="button" :class="{ active: phase.active, complete: phase.complete }" @click="scrollToWorkflowStep(phase.target)">
        <span>PHASE {{ String(index + 1).padStart(2, '0') }}</span><strong>{{ phase.label }}</strong><small>{{ phase.detail }}</small><em>{{ phase.complete ? '已完成' : phase.active ? '进行中' : '未开始' }}</em>
      </button>
    </div>
    <section class="creator-workflow-summary" aria-live="polite"><div class="creator-workflow-summary-main"><p class="creator-eyebrow">CURRENT STAGE</p><h3>{{ workflowStageLabel }}</h3><p>{{ workflowStageDetail }}</p></div><div class="creator-workflow-summary-metric"><small>关键节点完成度</small><strong>{{ workflowCoreCompletedCount }}/9</strong><span>{{ workflowBlocker }}</span><em>{{ selectedShotList ? `${videoClipSucceededCount}/${selectedShotList.shots.length} 个视频片段已完成` : '等待分镜清单' }}</em></div><button class="creator-primary-button" type="button" @click="scrollToWorkflowStep(nextWorkflowStep.target)">{{ nextWorkflowStep.label }} <span>→</span></button></section>
    <section class="creator-route-guide"><div><span class="creator-route-guide-label">推荐生产路径</span><strong>原文 → 剧本 → 资产审核 → 媒体生成 → 成片复核</strong></div><p>如果只是正常制作，使用上方的“下一步”或第一步里的“一键启动完整生产”；高级计划仅用于批量调试。</p></section>

    <details class="creator-detail-workflow">
      <summary><span>查看 10 个执行步骤</span><small>需要定位或复盘时展开</small></summary>
      <div class="creator-pipeline-steps" aria-label="小说生产详细步骤">
        <div class="creator-pipeline-step" :class="{ complete: sourceReady, active: !sourceReady }"><span>01</span><div><strong>导入小说</strong><small>{{ sourceReady ? '原文已就绪' : '上传 TXT / Markdown' }}</small></div></div>
        <div class="creator-pipeline-step" :class="{ complete: storyBibleReady, active: sourceReady && !storyBibleReady }"><span>02</span><div><strong>故事设定</strong><small>{{ storyBibleReady ? 'StoryBible 已生成' : '分析人物与世界观' }}</small></div></div>
        <div class="creator-pipeline-step" :class="{ complete: episodesReady, active: storyBibleReady && !episodesReady }"><span>03</span><div><strong>分集大纲</strong><small>{{ episodesReady ? `${episodes.length} 集可选择` : '拆分故事节奏' }}</small></div></div>
        <div class="creator-pipeline-step" :class="{ complete: scriptReady, active: Boolean(selectedEpisode) && !scriptReady }"><span>04</span><div><strong>分场剧本</strong><small>{{ scriptReady ? '剧本可预览' : '选择分集后生成' }}</small></div></div>
        <div class="creator-pipeline-step" :class="{ active: scriptReady, complete: shotListReady }"><span>05</span><div><strong>分镜任务</strong><small>{{ shotListReady ? '已提交完成' : '准备生成画面' }}</small></div></div>
        <div class="creator-pipeline-step" :class="{ active: scriptReady, complete: audioTask?.status === 'succeeded' }"><span>06</span><div><strong>单集旁白</strong><small>{{ audioTask?.status === 'succeeded' ? '音频已生成' : '准备合成声音' }}</small></div></div>
        <div class="creator-pipeline-step" :class="{ active: Boolean(audioArtifact), complete: subtitleTask?.status === 'succeeded' }"><span>07</span><div><strong>字幕任务</strong><small>{{ subtitleTask?.status === 'succeeded' ? 'SRT 已生成' : audioArtifact ? '准备创建字幕' : '等待旁白完成' }}</small></div></div>
        <div class="creator-pipeline-step" :class="{ active: Boolean(audioArtifact), complete: bgmTask?.status === 'succeeded' }"><span>08</span><div><strong>BGM</strong><small>{{ bgmTask?.status === 'succeeded' ? '音轨已生成' : audioArtifact ? '准备添加配乐' : '等待旁白完成' }}</small></div></div>
        <div class="creator-pipeline-step" :class="{ active: videoClipReadyCount > 0, complete: selectedShotList && videoClipReadyCount > 0 && videoClipSucceededCount >= videoClipReadyCount }"><span>09</span><div><strong>画面片段</strong><small>{{ selectedShotList ? `${videoClipSucceededCount}/${videoClipReadyCount} 个可生成镜头已完成` : '等待分镜审核' }}</small></div></div>
        <div class="creator-pipeline-step" :class="{ active: assemblyClipSelections.length >= 2, complete: videoAssemblyTask?.status === 'succeeded' }"><span>10</span><div><strong>成片合成</strong><small>{{ videoAssemblyTask?.status === 'succeeded' ? '成片已生成' : assemblyClipSelections.length >= 2 ? '准备编排成片' : '等待至少两个片段' }}</small></div></div>
      </div>
    </details>

    <div v-if="errorMessage" class="creator-alert creator-workspace-alert" role="alert"><strong>工作区提示</strong><span>{{ errorMessage }}</span><button type="button" @click="refreshWorkspace">重试</button></div>
    <div v-if="noticeMessage" class="creator-workspace-notice" role="status"><span>✓</span>{{ noticeMessage }}</div>

    <div class="creator-workspace-grid">
      <main class="creator-workspace-main">
        <section id="creator-step-source" class="creator-workspace-card">
          <div class="creator-workspace-card-heading"><div><p class="creator-eyebrow">STEP 01</p><h3>导入小说原文</h3><p>支持 UTF-8 编码的 `.txt` 或 `.md` 文件，单文件不超过 5 MB。</p></div><span class="creator-card-state" :class="{ ready: sourceReady }">{{ sourceReady ? '已上传' : '待上传' }}</span></div>
          <div v-if="!sourceReady" class="creator-upload-zone" :class="{ selected: selectedFile }" @click="openFilePicker">
            <input ref="fileInput" class="creator-hidden-input" type="file" accept=".txt,.md,text/plain,text/markdown" @change="onFileChange" />
            <span class="creator-upload-icon">↑</span>
            <strong>{{ selectedFile ? selectedFile.name : '选择小说文件' }}</strong>
            <small>{{ selectedFile ? `${formatBytes(selectedFile.size)} · 点击重新选择` : '拖入文件，或点击从电脑选择' }}</small>
          </div>
          <div v-else class="creator-source-summary"><span class="creator-source-icon">▤</span><div><strong>小说原文已接入</strong><small>{{ projectData.source_id }} · 已切分 {{ chapters.length }} 个章节</small></div><span class="creator-source-meta">{{ chapters.length ? formatBytes(chapters.reduce((total, chapter) => total + chapter.content.length, 0)) : '已保存' }}</span></div>
          <div v-if="!sourceReady" class="creator-workspace-actions"><button class="creator-primary-button" type="button" :disabled="!selectedFile || Boolean(action)" @click="uploadSource">{{ action === 'upload' ? '上传中…' : '上传并切分章节' }} <span>→</span></button></div>
          <div v-else class="creator-chapter-preview"><div class="creator-subheading"><strong>章节预览</strong><small>{{ chapters.length }} 个章节</small></div><div v-if="chapters.length" class="creator-chapter-list"><div v-for="chapter in chapters.slice(0, 3)" :key="chapter.id"><span>第 {{ chapter.chapter_number }} 章</span><strong>{{ chapter.title }}</strong></div></div><small v-if="chapters.length > 3" class="creator-more-note">还有 {{ chapters.length - 3 }} 个章节，完整内容将在制作后台中查看。</small></div>
          <div v-if="sourceReady" class="creator-auto-run-panel">
            <div><span class="creator-auto-run-icon">▶</span><div><strong>Windows 4060 Ti 自动生产</strong><small>一键创建从故事设定到最终成片的完整 DAG。分镜资产审核仍然是门禁，不会绕过人工审核。</small></div></div>
            <button class="creator-primary-button" type="button" :disabled="!productionRunCanSubmit" @click="startFullProduction">{{ action === 'production-run' ? '启动中…' : productionRun?.status === 'active' ? 'Run 已启动' : '一键启动完整生产' }} <span>→</span></button>
          </div>
          <div v-if="productionRun" class="creator-auto-run-status" :class="productionRun.status"><span>{{ productionRun.status === 'completed' ? '✓' : productionRun.status === 'failed' ? '!' : productionRun.status === 'blocked' ? '!' : '↻' }}</span><div><strong>Run {{ productionRun.status === 'active' ? '运行中' : productionRun.status === 'blocked' ? '等待人工处理' : productionRun.status === 'completed' ? '已完成' : '失败' }}</strong><small>{{ productionRun.run_id }} · 当前阶段 {{ productionRun.stage }} · {{ productionRun.message }}</small></div></div>
        </section>

        <section id="creator-step-story" class="creator-workspace-card" :class="{ muted: !sourceReady }">
          <div class="creator-workspace-card-heading"><div><p class="creator-eyebrow">STEP 02</p><h3>分析故事设定</h3><p>提取角色、场景、道具、时间线和核心冲突，作为后续剧本的统一依据。</p></div><span class="creator-card-state" :class="{ ready: storyBibleReady }">{{ storyBibleReady ? '已完成' : '待处理' }}</span></div>
          <div class="creator-step-callout"><span class="creator-step-callout-icon">✦</span><div><strong>{{ storyBibleTask && isActive(storyBibleTask.status) ? '故事设定正在生成' : storyBibleReady ? '故事设定已准备好' : '准备好后开始分析' }}</strong><small>{{ storyBibleTask && isActive(storyBibleTask.status) ? `任务${formatStatus(storyBibleTask.status)}，页面会自动刷新` : '这一步会调用当前配置的长文本 Provider，并保留可追踪任务记录。' }}</small></div></div>
          <div class="creator-workspace-actions"><button class="creator-primary-button" type="button" :disabled="!sourceReady || storyBibleReady || Boolean(action) || Boolean(storyBibleTask && isActive(storyBibleTask.status))" @click="startStoryBible">{{ action === 'story-bible' ? '提交中…' : storyBibleReady ? '故事设定已完成' : '开始分析故事设定' }} <span>→</span></button></div>
        </section>

        <section id="creator-step-episode-plan" class="creator-workspace-card" :class="{ muted: !storyBibleReady }">
          <div class="creator-workspace-card-heading"><div><p class="creator-eyebrow">STEP 03</p><h3>生成分集大纲</h3><p>根据项目设定拆出每集目标、冲突、转折和结尾钩子。</p></div><span class="creator-card-state" :class="{ ready: episodesReady }">{{ episodesReady ? `${episodes.length} 集` : '待处理' }}</span></div>
          <div v-if="!episodesReady" class="creator-step-callout"><span class="creator-step-callout-icon blue">01</span><div><strong>{{ episodePlanTask && isActive(episodePlanTask.status) ? '分集大纲正在生成' : '还没有可选择的分集' }}</strong><small>{{ episodePlanTask && isActive(episodePlanTask.status) ? '任务完成后会自动出现分集列表。' : '默认按项目创建时设置的集数生成，可在内部后台继续调整。' }}</small></div></div>
          <div v-else class="creator-plan-summary"><span class="creator-plan-number">{{ episodes.length }}</span><div><strong>分集大纲已生成</strong><small>选择一集，进入分场剧本阶段。</small></div></div>
          <div class="creator-workspace-actions"><button class="creator-primary-button" type="button" :disabled="!storyBibleReady || episodesReady || Boolean(action) || Boolean(episodePlanTask && isActive(episodePlanTask.status))" @click="startEpisodePlan">{{ action === 'episode-plan' ? '提交中…' : episodesReady ? '分集大纲已完成' : '生成分集大纲' }} <span>→</span></button></div>
        </section>

        <section v-if="episodesReady" id="creator-step-episodes" class="creator-workspace-card">
          <div class="creator-workspace-card-heading"><div><p class="creator-eyebrow">CONTENT HANDOFF</p><h3>选择要制作的分集</h3><p>先从一集开始验证脚本质量，后续再扩展批量生产。</p></div><span class="creator-card-state ready">{{ selectedEpisode ? `已选第 ${selectedEpisode.episode_number} 集` : '请选择' }}</span></div>
          <div class="creator-episode-picker"><button v-for="episode in episodes" :key="episode.id" type="button" :class="{ selected: episode.id === selectedEpisodeId }" @click="selectEpisode(episode.id)"><span>第 {{ episode.episode_number }} 集</span><strong>{{ episode.outline.title }}</strong><small>{{ episode.outline.target_duration_seconds }} 秒 · {{ episode.status === 'scripted' ? '已有剧本' : '待生成剧本' }}</small></button></div>
        </section>

        <details v-if="episodesReady" class="creator-advanced-section">
          <summary><span>高级：分集生产计划</span><small>批量推进与断点调试</small></summary>
          <section class="creator-workspace-card creator-plan-card">
          <div class="creator-workspace-card-heading"><div><p class="creator-eyebrow">BATCH PRODUCTION PLAN</p><h3>推进分集生产计划</h3><p>这是面向批量和断点调试的手动入口；系统按依赖推进任务，不会跳过剧本和资产审核。</p></div><span class="creator-card-state" :class="{ ready: planResponse?.batch || planResponse?.skipped_count }">{{ planResponse ? `${planResponse.created_count + planResponse.reused_count} 个任务` : '可推进' }}</span></div>
          <div class="creator-plan-note"><span>i</span><p>计划不会跳过剧本和资产审核，也不会自动发布。上游任务完成后再次点击即可断点续跑；已完成、运行中和失败任务会复用，失败任务可在任务中心恢复。</p></div>
          <label class="creator-plan-label"><span>计划名称</span><input v-model="planLabel" maxlength="120" :disabled="Boolean(action)" placeholder="例如：第一季分集剧本计划" /></label>
          <div class="creator-plan-selection-heading"><strong>选择分集</strong><span>{{ planEpisodeIds.length }}/{{ episodes.length }} 已选择</span><button type="button" @click="planEpisodeIds = episodes.map((episode) => episode.id)">全选</button><button type="button" @click="planEpisodeIds = []">清空</button></div>
          <div class="creator-plan-episode-grid"><label v-for="episode in episodes" :key="episode.id" class="creator-plan-episode-option"><input v-model="planEpisodeIds" type="checkbox" :value="episode.id" :disabled="Boolean(action)" /><span><strong>第 {{ episode.episode_number }} 集</strong><small>{{ episode.outline.title }}</small></span></label></div>
          <div v-if="planResponse" class="creator-plan-result"><span>最近一次计划：{{ planResponse.created_count }} 新建 · {{ planResponse.reused_count }} 复用 · {{ planResponse.skipped_count }} 跳过 · {{ planResponse.blocked_count }} 阻塞</span><small v-for="item in planResponse.items" :key="item.episode_id">第 {{ item.episode_number }} 集：{{ item.stage ?? '已完成' }} · {{ item.reason }}</small></div>
          <div class="creator-workspace-actions"><button class="creator-primary-button" type="button" :disabled="!planCanSubmit" @click="startEpisodeTaskPlan">{{ action === 'episode-task-plan' ? '推进中…' : '推进自动生产计划' }} <span>→</span></button><button class="creator-ghost-button" type="button" @click="emit('openOperator', 'tasks')">打开任务中心 <span>↗</span></button></div>
          </section>
        </details>

        <section v-if="selectedEpisode" id="creator-step-script" class="creator-workspace-card">
          <div class="creator-workspace-card-heading"><div><p class="creator-eyebrow">STEP 04 · SCRIPT</p><h3>生成并查看分场剧本</h3><p>剧本生成后可以在内部制作后台继续编辑、审核和保存版本。</p></div><span class="creator-card-state" :class="{ ready: scriptReady }">{{ scriptReady ? `版本 ${selectedScript?.version ?? '最新'}` : '待生成' }}</span></div>
          <div v-if="!scriptReady" class="creator-script-empty"><span class="creator-script-icon">✎</span><div><strong>{{ scriptTask && isActive(scriptTask.status) ? '剧本正在生成' : '这集还没有剧本' }}</strong><small>{{ scriptTask && isActive(scriptTask.status) ? '任务完成后页面会自动载入剧本摘要。' : '生成后会得到场景、旁白、对白和时长结构。' }}</small></div></div>
          <div v-else-if="selectedScript" class="creator-script-preview"><div class="creator-script-stats"><span><strong>{{ selectedScript.content.scenes.length }}</strong> 个场景</span><span><strong>{{ selectedScript.content.scenes.reduce((count, scene) => count + scene.dialogues.length, 0) }}</strong> 条对白</span><span><strong>{{ selectedScript.content.total_duration_seconds }}</strong> 秒</span></div><div class="creator-script-hook"><strong>{{ selectedScript.content.opening_hook }}</strong><span>{{ selectedScript.content.ending_hook }}</span></div><div class="creator-scene-preview"><div v-for="scene in selectedScript.content.scenes.slice(0, 2)" :key="scene.scene_index"><span>场景 {{ scene.scene_index }}</span><strong>{{ scene.title }}</strong><small>{{ scene.location }} · {{ scene.duration_seconds }} 秒</small></div></div></div>
          <div class="creator-workspace-actions"><button class="creator-primary-button" type="button" :disabled="scriptReady || Boolean(action) || Boolean(scriptTask && isActive(scriptTask.status))" @click="startScript">{{ action === 'episode-script' ? '提交中…' : scriptReady ? '剧本已生成' : '生成这一集的剧本' }} <span>→</span></button><button v-if="scriptReady" class="creator-ghost-button" type="button" @click="emit('openOperator')">打开编辑工作台 <span>↗</span></button></div>
        </section>

        <section v-if="scriptReady" id="creator-step-shots" class="creator-workspace-card creator-next-production-card">
          <div class="creator-workspace-card-heading"><div><p class="creator-eyebrow">STEP 05 · STORYBOARD</p><h3>提交分镜任务</h3><p>分镜任务会根据剧本生成镜头列表，并尝试绑定角色、场景和道具资产。</p></div><span class="creator-card-state" :class="{ ready: shotTask?.status === 'succeeded' }">{{ shotTask?.status === 'succeeded' ? '已完成' : '下一步' }}</span></div>
          <div class="creator-production-note"><span>i</span><p>分镜完成后仍需要人工检查资产绑定和画面提示词，再进入视频片段生成。当前系统不会跳过审核直接批量发布。</p></div>
          <div class="creator-workspace-actions"><button class="creator-primary-button" type="button" :disabled="Boolean(action) || Boolean(shotTask && isActive(shotTask.status)) || shotTask?.status === 'succeeded'" @click="startShots">{{ action === 'shot-list' ? '提交中…' : shotTask?.status === 'succeeded' ? '分镜任务已完成' : '生成分镜任务' }} <span>→</span></button><button class="creator-ghost-button" type="button" @click="emit('openOperator', 'workbench')">进入制作后台审核 <span>↗</span></button></div>
        </section>

        <section v-if="scriptReady" id="creator-step-audio" class="creator-workspace-card creator-audio-card">
          <div class="creator-workspace-card-heading"><div><p class="creator-eyebrow">STEP 06</p><h3>生成单集旁白</h3><p>从当前剧本整理旁白和对白文本，调用配置好的 TTS Provider 生成可复用的音频 Artifact。</p></div><span class="creator-card-state" :class="{ ready: audioTask?.status === 'succeeded' }">{{ audioTask?.status === 'succeeded' ? '已完成' : audioTask ? formatStatus(audioTask.status) : '待生成' }}</span></div>
          <div v-if="audioTask?.status === 'failed'" class="creator-audio-error"><strong>旁白生成失败</strong><span>{{ taskErrorDetail(audioTask.error) }}</span></div>
          <label class="creator-audio-field"><span>旁白与对白文本 <em>{{ narrationText.length }}/5000</em></span><textarea v-model="narrationText" rows="8" maxlength="5000" :disabled="Boolean(action) || Boolean(audioTask && isActive(audioTask.status))" placeholder="输入本集需要合成的旁白或对白文本" @input="narrationTextDirty = true" /></label>
          <div v-if="audioArtifact" class="creator-audio-artifact"><span class="creator-audio-icon">♫</span><div><strong>音频 Artifact 已生成</strong><small>{{ audioArtifact.provider }} · {{ formatAudioDuration(audioArtifact.metadata.duration_seconds) }} · 可供字幕和成片编排使用</small></div><span class="creator-artifact-state">已校验</span></div>
          <div class="creator-workspace-actions"><button class="creator-primary-button" type="button" :disabled="!narrationText.trim() || Boolean(action) || Boolean(audioTask && isActive(audioTask.status))" @click="startNarration">{{ action === 'audio-narration' ? '提交中…' : audioTask?.status === 'succeeded' ? '重新生成旁白' : audioTask ? '再次提交旁白' : '生成本集旁白' }} <span>→</span></button><button class="creator-ghost-button" type="button" @click="emit('openOperator')">进入任务中心 <span>↗</span></button></div>
        </section>

        <section v-if="audioArtifact" id="creator-step-subtitle" class="creator-workspace-card creator-subtitle-card">
          <div class="creator-workspace-card-heading"><div><p class="creator-eyebrow">STEP 07</p><h3>生成并审核字幕</h3><p>使用已校验的单集旁白创建字幕任务。ASR 依赖选定 Provider，句子级对齐只提供待审核的时间估算。</p></div><span class="creator-card-state" :class="{ ready: subtitleTask?.status === 'succeeded' }">{{ subtitleTask?.status === 'succeeded' ? '已完成' : subtitleTask ? formatStatus(subtitleTask.status) : '待生成' }}</span></div>
          <div class="creator-subtitle-source"><span class="creator-audio-icon">♫</span><div><strong>已选择本集旁白 Artifact</strong><small>{{ audioArtifact.provider }} · {{ formatAudioDuration(audioArtifact.metadata.duration_seconds) }} · 已通过音频校验</small></div><span class="creator-artifact-state">可用</span></div>
          <div class="creator-subtitle-mode-switch" role="tablist" aria-label="字幕生成方式"><button type="button" :class="{ active: subtitleMode === 'asr' }" @click="subtitleMode = 'asr'">真实 ASR 转写</button><button type="button" :class="{ active: subtitleMode === 'align' }" @click="subtitleMode = 'align'">句子级对齐</button></div>
          <div v-if="subtitleMode === 'asr'" class="creator-subtitle-note"><strong>ASR Provider</strong><select v-model="selectedProfileId" :disabled="profilesLoading || Boolean(action)"><option value="" disabled>请选择已配置的识别服务</option><option v-for="profile in availableProfiles" :key="profile.profile_id" :value="profile.profile_id">{{ profile.label }} · {{ profile.model }}{{ profile.default ? ' · 默认' : '' }}</option></select><small v-if="profilesLoading">正在读取可用配置…</small><small v-else-if="availableProfiles.length === 0">当前没有可用 ASR Provider；可以切换到句子级对齐，或先在制作后台配置服务。</small><small v-else>文本会作为质量复核参考；Provider 的密钥只保存在服务端。</small></div>
          <div v-else class="creator-subtitle-note align"><strong>句子级对齐</strong><p>系统会按句子与音频总时长估算时间轴，不读取声学特征，结果必须人工审核后才能进入成片。</p></div>
          <label class="creator-audio-field"><span>{{ subtitleMode === 'asr' ? '参考文本（用于质量复核）' : '字幕文本' }} <em>{{ subtitleText.length }}/5000</em></span><textarea v-model="subtitleText" rows="7" maxlength="5000" :disabled="Boolean(action) || Boolean(subtitleTask && isActive(subtitleTask.status))" placeholder="默认使用生成旁白时提交的文本；可在这里调整后再创建字幕任务。" @input="subtitleTextDirty = true" /></label>
          <div v-if="subtitleTask?.status === 'failed'" class="creator-audio-error"><strong>字幕任务失败</strong><span>{{ taskErrorDetail(subtitleTask.error) }}</span></div>
          <div v-if="subtitleArtifact" class="creator-subtitle-artifact"><span class="creator-subtitle-artifact-icon">▤</span><div><strong>SRT Artifact 已生成</strong><small>{{ subtitleArtifact.provider }} · {{ formatSubtitleCueCount(subtitleArtifact.metadata.cue_count) }} · 请继续审核文本与时间轴</small></div><span class="creator-artifact-state">待审核</span></div>
          <div class="creator-workspace-actions"><button class="creator-primary-button" type="button" :disabled="!subtitleCanSubmit" @click="startSubtitle">{{ action === 'subtitle-asr' || action === 'subtitle-align' ? '提交中…' : subtitleTask?.status === 'succeeded' ? '重新生成字幕' : '创建字幕任务' }} <span>→</span></button><button class="creator-ghost-button" type="button" @click="emit('openOperator', 'subtitle')">进入字幕审核 <span>↗</span></button></div>
        </section>

        <details v-if="audioArtifact" class="creator-optional-section">
          <summary><span>可选：添加背景音乐</span><small>{{ bgmTask?.status === 'succeeded' ? '已完成' : '不影响主流程' }}</small></summary>
          <section class="creator-workspace-card creator-bgm-card">
          <div class="creator-workspace-card-heading"><div><p class="creator-eyebrow">STEP 08</p><h3>添加背景音乐</h3><p>使用服务端配置的 Mock 或授权本地文件 Provider。授权状态只作为线索记录，系统不会自动确认版权。</p></div><span class="creator-card-state" :class="{ ready: bgmTask?.status === 'succeeded' }">{{ bgmTask?.status === 'succeeded' ? '已完成' : bgmTask ? formatStatus(bgmTask.status) : '待生成' }}</span></div>
          <div class="creator-bgm-guide"><span>i</span><p>本地文件模式只允许读取 Worker 授权目录内的相对路径；Mock 模式不需要文件。生成后音频会经过服务端格式和播放性校验；标记为“不可使用”的素材不能提交。</p></div>
          <div class="creator-bgm-form"><label><span>曲目标签</span><input v-model="bgmLabel" maxlength="120" :disabled="Boolean(action) || Boolean(bgmTask && isActive(bgmTask.status))" placeholder="例如：悬疑氛围铺底" /></label><label><span>授权状态</span><select v-model="bgmRightsStatus" :disabled="Boolean(action) || Boolean(bgmTask && isActive(bgmTask.status))"><option value="unknown">尚未确认</option><option value="pending">待核验</option><option value="confirmed">已确认</option><option value="denied">不可使用</option></select></label><label class="creator-bgm-wide"><span>授权目录内相对路径（可选）</span><input v-model="bgmSourcePath" maxlength="500" :disabled="Boolean(action) || Boolean(bgmTask && isActive(bgmTask.status))" placeholder="例如 licensed/ambient.wav；Mock 模式可留空" /></label><label><span>权利人（可选）</span><input v-model="bgmRightsHolder" maxlength="200" :disabled="Boolean(action) || Boolean(bgmTask && isActive(bgmTask.status))" placeholder="公司、作者或素材库" /></label><label><span>授权凭据引用（可选）</span><input v-model="bgmRightsReference" maxlength="500" :disabled="Boolean(action) || Boolean(bgmTask && isActive(bgmTask.status))" placeholder="合同号、订单号或内部记录" /></label></div>
          <div v-if="bgmTask?.status === 'failed'" class="creator-audio-error"><strong>BGM 任务失败</strong><span>{{ taskErrorDetail(bgmTask.error) }}</span></div>
          <div v-if="bgmArtifact" class="creator-bgm-artifact"><span class="creator-bgm-icon">♪</span><div><strong>BGM Artifact 已生成</strong><small>{{ bgmArtifact.provider }} · {{ formatAudioDuration(bgmArtifact.metadata.duration_seconds) }} · {{ formatBgmSourceType(bgmArtifact.metadata.source_type) }}</small></div><span class="creator-artifact-state">已校验</span></div>
          <div class="creator-workspace-actions"><button class="creator-primary-button" type="button" :disabled="!bgmCanSubmit" @click="startBgm">{{ action === 'audio-bgm' ? '提交中…' : bgmTask?.status === 'succeeded' ? '重新生成 BGM' : '创建 BGM 任务' }} <span>→</span></button><button class="creator-ghost-button" type="button" @click="emit('openOperator', 'artifacts')">查看音频 Artifact <span>↗</span></button></div>
          </section>
        </details>

        <section v-if="selectedShotList" id="creator-step-video" class="creator-workspace-card creator-video-clips-card">
          <div class="creator-workspace-card-heading"><div><p class="creator-eyebrow">STEP 09 · VIDEO CLIPS</p><h3>生成单镜头视频片段</h3><p>只对已审核、资产绑定完整的镜头提交任务。每个镜头独立生成，失败后可以单独重试，不会重新生成整集。</p></div><span class="creator-card-state" :class="{ ready: videoClipReadyCount === selectedShotList.shots.length }">{{ videoClipReadyCount }}/{{ selectedShotList.shots.length }} 可生成</span></div>
          <div class="creator-production-note"><span>i</span><p>视频生成门禁由服务端再次校验。若镜头存在未解决资产需求、绑定警告或非 ready 资产，请进入制作后台处理后再回来提交。</p><button v-if="shotFailedCount" class="creator-inline-action" type="button" @click="focusFailedShots">只看失败镜头</button></div>
          <div class="creator-shot-toolbar">
            <div><strong>镜头清单</strong><small>显示 {{ filteredShots.length }}/{{ shotTotalCount }} · 已完成 {{ shotSucceededCount }} · 失败 {{ shotFailedCount }} · 需补资产 {{ shotBlockedCount }}</small></div>
            <div class="creator-shot-controls"><div class="creator-shot-filters"><button v-for="filter in shotFilterOptions" :key="filter.key" type="button" :class="{ active: shotFilter === filter.key }" @click="shotFilter = filter.key">{{ filter.label }} <b>{{ filter.count }}</b></button></div><input v-model="shotSearch" type="search" placeholder="搜索镜头或提示词" aria-label="搜索镜头或提示词" /></div>
          </div>
          <div v-if="!filteredShots.length" class="creator-shot-empty"><strong>没有匹配的镜头</strong><span>尝试切换筛选条件或清空搜索。</span><button class="creator-small-button" type="button" @click="shotFilter = 'all'; shotSearch = ''">显示全部镜头</button></div>
          <div v-else class="creator-shot-list">
            <article v-for="shot in filteredShots" :key="shot.shot_index" class="creator-shot-card" :class="{ ready: isShotReady(shot), failed: latestVideoClipTask(shot.shot_index)?.status === 'failed', completed: latestVideoClipTask(shot.shot_index)?.status === 'succeeded' && Boolean(videoClipArtifact(shot.shot_index)), blocked: !isShotReady(shot) }">
              <div class="creator-shot-card-heading"><div><span>SHOT {{ String(shot.shot_index).padStart(2, '0') }} · SCENE {{ shot.scene_index }}</span><strong>{{ shot.shot_size }} · {{ shot.camera_movement }}</strong></div><em :class="{ ready: isShotReady(shot) }">{{ formatShotGate(shot) }}</em></div>
              <details class="creator-shot-prompt-details"><summary>查看画面提示词</summary><p>{{ shot.visual_prompt }}</p></details>
              <div class="creator-shot-meta"><span>{{ shot.duration_seconds }} 秒</span><span>{{ shot.asset_refs.length }} 个资产</span><span v-if="shot.continuity_notes">有连续性备注</span></div>
              <div v-if="shot.unresolved_asset_requirements.length || shot.asset_binding_warnings.length" class="creator-shot-warning"><strong>暂不可生成</strong><span>{{ [...shot.unresolved_asset_requirements, ...shot.asset_binding_warnings].join('；') }}</span></div>
              <div v-if="latestVideoClipTask(shot.shot_index)?.status === 'failed'" class="creator-audio-error"><strong>片段任务失败</strong><span>{{ taskErrorDetail(latestVideoClipTask(shot.shot_index)?.error) }}</span></div>
              <div v-if="videoClipArtifact(shot.shot_index)" class="creator-video-artifact"><span class="creator-video-icon">▶</span><div><strong>视频片段 Artifact 已生成</strong><small>{{ videoClipArtifact(shot.shot_index)?.provider }} · {{ formatAudioDuration(videoClipArtifact(shot.shot_index)?.metadata.duration_seconds) }} · 已通过播放性校验</small></div><span class="creator-artifact-state">已校验</span></div>
              <div class="creator-shot-actions"><button class="creator-primary-button" type="button" :disabled="!isShotReady(shot) || Boolean(action) || Boolean(latestVideoClipTask(shot.shot_index) && isActive(latestVideoClipTask(shot.shot_index)?.status ?? 'created'))" @click="startVideoClip(shot)">{{ action === `video-clip-${shot.shot_index}` ? '提交中…' : videoClipArtifact(shot.shot_index) ? '重新生成片段' : latestVideoClipTask(shot.shot_index) ? '再次提交片段' : '生成视频片段' }} <span>→</span></button><button class="creator-ghost-button" type="button" @click="emit('openOperator', 'artifacts')">查看审核与 Artifact <span>↗</span></button></div>
            </article>
          </div>
        </section>

        <section v-if="assemblyClipSelections.length >= 2" id="creator-step-assembly" class="creator-workspace-card creator-assembly-card">
          <div class="creator-workspace-card-heading"><div><p class="creator-eyebrow">STEP 10 · FINAL ASSEMBLY</p><h3>编排并生成成片</h3><p>按镜头编号拼接已成功片段，可选混入旁白、BGM 和字幕。任务完成后仅生成 Artifact，不会自动发布。</p></div><span class="creator-card-state" :class="{ ready: videoAssemblyTask?.status === 'succeeded' }">{{ videoAssemblyTask?.status === 'succeeded' ? '已完成' : videoAssemblyTask ? formatStatus(videoAssemblyTask.status) : '可编排' }}</span></div>
          <div class="creator-assembly-summary"><div><strong>{{ assemblyClipSelections.length }} 个镜头</strong><small>按 SHOT 编号自动排序</small></div><div><strong>{{ assemblyClipSelections.reduce((total, selection) => total + positiveNumber(selection.artifact.metadata.duration_seconds), 0).toFixed(1) }} 秒</strong><small>当前选用片段合计</small></div><div><strong>{{ audioTracksForAssemblyCount }} 条音轨</strong><small>旁白 / BGM 可选</small></div></div>
          <div v-if="selectedShotList && successfulVideoClipTasks.length < selectedShotList.shots.length" class="creator-production-note"><span>i</span><p>当前只有部分镜头已成功，成片将按现有成功片段生成（{{ successfulVideoClipTasks.length }}/{{ selectedShotList.shots.length }}）。剩余镜头完成后可重新编排。</p></div>
          <div class="creator-assembly-clip-list"><div v-for="selection in assemblyClipSelections" :key="selection.task.id"><span class="creator-assembly-index">{{ String(selection.shotIndex).padStart(2, '0') }}</span><div><strong>SHOT {{ selection.shotIndex }}{{ selection.source === 'lip_sync' ? ' · MuseTalk' : '' }}</strong><small>{{ selection.artifact.provider }} · {{ selection.source === 'lip_sync' ? '已替换为唇形同步片段' : '原始视频片段回退' }} · 已通过播放性校验</small></div><select v-if="lipSyncCandidateForShot(selection.shotIndex)" class="creator-assembly-source" :value="selection.source" :aria-label="`选择镜头 ${selection.shotIndex} 的成片来源`" @change="setAssemblySource(selection.shotIndex, $event)"><option value="video_clip">原始片段</option><option value="lip_sync">MuseTalk 片段</option></select><span class="creator-artifact-state">已就绪</span></div></div>
          <div class="creator-assembly-options">
            <label><input v-model="assemblyUseNarration" type="checkbox" :disabled="!audioArtifact || Boolean(action) || Boolean(videoAssemblyTask && isActive(videoAssemblyTask.status))" /><span><strong>混入旁白</strong><small>{{ audioArtifact ? formatAudioDuration(audioArtifact.metadata.duration_seconds) : '暂无可用旁白' }}</small></span></label>
            <label><input v-model="assemblyUseBgm" type="checkbox" :disabled="!bgmArtifact || Boolean(action) || Boolean(videoAssemblyTask && isActive(videoAssemblyTask.status))" /><span><strong>混入 BGM</strong><small>{{ bgmArtifact ? `默认音量 ${assemblyBgmVolume.toFixed(2)}` : '暂无可用 BGM' }}</small></span></label>
            <label><input v-model="assemblyUseSubtitles" type="checkbox" :disabled="!subtitleArtifact || Boolean(action) || Boolean(videoAssemblyTask && isActive(videoAssemblyTask.status))" /><span><strong>烧录字幕</strong><small>{{ subtitleArtifact ? formatSubtitleCueCount(subtitleArtifact.metadata.cue_count) : '暂无可用字幕' }}</small></span></label>
            <label v-if="bgmArtifact && assemblyUseBgm" class="creator-assembly-volume"><span><strong>BGM 音量</strong><small>建议保持在 0.18 左右</small></span><input v-model.number="assemblyBgmVolume" type="number" min="0" max="2" step="0.01" :disabled="Boolean(action) || Boolean(videoAssemblyTask && isActive(videoAssemblyTask.status))" /></label>
          </div>
          <div v-if="videoAssemblyTask?.status === 'failed'" class="creator-audio-error"><strong>成片合成失败</strong><span>{{ taskErrorDetail(videoAssemblyTask.error) }}</span></div>
          <div v-if="renderedVideoArtifact" class="creator-assembly-artifact"><span class="creator-video-icon">▶</span><div><strong>成片 Artifact 已生成</strong><small>{{ renderedVideoArtifact.provider }} · {{ formatAudioDuration(renderedVideoArtifact.metadata.duration_seconds) }} · 已通过 FFprobe 播放性校验</small></div><span class="creator-artifact-state">可预览</span></div>
          <div v-if="renderedVideoArtifactLoading" class="creator-assembly-preview-loading"><span class="spinner" />正在获取成片预览…</div>
          <div v-else-if="renderedVideoArtifactRecord" class="creator-assembly-preview"><MediaPreview :artifact="renderedVideoArtifactRecord" variant="panel" alt="最终成片预览" show-download /><div class="creator-assembly-preview-actions"><span>预览优先使用服务端安全通道，临时链接仅作为回退。</span></div></div>
          <div v-else-if="renderedVideoArtifact" class="creator-production-note"><span>i</span><p>成片 Artifact 已生成，但当前没有可读取的二进制内容；请检查 Worker 存储状态。</p></div>
          <div class="creator-workspace-actions"><button class="creator-primary-button" type="button" :disabled="!assemblyCanSubmit" @click="startVideoAssembly">{{ action === 'video-assembly' ? '提交中…' : videoAssemblyTask?.status === 'succeeded' ? '重新生成成片' : '生成成片' }} <span>→</span></button><button class="creator-ghost-button" type="button" @click="emit('openOperator', 'artifacts')">查看任务与 Artifact <span>↗</span></button></div>
        </section>

        <details v-if="selectedEpisode" class="creator-quality-section">
          <summary><span>质量审核与声音资产</span><small>身份初审、失败重试、多角色配音与 MuseTalk</small></summary>
          <ProductionQualityPanel
            :project-id="projectData.id"
            :episode="selectedEpisode"
            :script="selectedScript"
            :audio-artifact="audioArtifact"
            :tasks="tasks"
            @changed="refreshWorkspace"
          />
        </details>
      </main>

      <aside class="creator-workspace-sidebar">
        <section id="creator-workflow-status" class="creator-workspace-card creator-progress-card">
          <div class="creator-workspace-card-heading"><div><p class="creator-eyebrow">WORKFLOW STATUS</p><h3>阶段导航</h3><p>点击阶段可快速定位；绿色代表该阶段已完成。</p></div><button class="creator-refresh-button" type="button" :disabled="refreshing" aria-label="刷新工作区" @click="refreshWorkspace">↻</button></div>
          <div class="creator-stage-nav">
            <button v-for="phase in workflowPhaseCards" :key="phase.key" type="button" class="creator-stage-nav-row" :class="{ active: phase.active, complete: phase.complete }" @click="scrollToWorkflowStep(phase.target)">
              <span>{{ phase.complete ? '✓' : phase.active ? '•' : '○' }}</span><div><strong>{{ phase.label }}</strong><small>{{ phase.detail }}</small></div><b>›</b>
            </button>
          </div>
          <div class="creator-next-step-card"><span>下一步</span><strong>{{ nextWorkflowStep.label }}</strong><p>{{ workflowBlocker }}</p><button class="creator-small-button" type="button" @click="scrollToWorkflowStep(nextWorkflowStep.target)">定位到操作区 <span>↓</span></button></div>
        </section>
        <section class="creator-workspace-card creator-activity-card"><div class="creator-workspace-card-heading"><div><p class="creator-eyebrow">RECENT TASKS</p><h3>任务记录</h3></div><span class="creator-activity-count">{{ activeTaskCount ? `${activeTaskCount} 个处理中` : failedTaskCount ? `${failedTaskCount} 个需处理` : '已同步' }}</span></div><div v-if="loading" class="creator-activity-empty"><span class="spinner" />读取任务…</div><div v-else-if="!tasks.length" class="creator-activity-empty">完成上一步后，任务会显示在这里。</div><div v-else class="creator-activity-list"><div v-for="group in recentTaskGroups" :key="`${group.task.id}-${group.task.status}-${group.task.error?.code ?? ''}`"><span class="creator-activity-mark" :class="group.task.status">{{ group.task.status === 'succeeded' ? '✓' : group.task.status === 'failed' ? '!' : '↻' }}</span><div><strong>{{ formatTaskKind(group.task.kind) }}<em v-if="group.count > 1">×{{ group.count }}</em></strong><small>{{ formatStatus(group.task.status) }}<span v-if="group.task.error"> · {{ friendlyErrorMessage(group.task.error) }}</span> · {{ formatTime(group.task.updated_at) }}</small></div></div></div></section>
        <section class="creator-workspace-card creator-help-card"><span class="creator-help-icon">?</span><h3>需要人工审核</h3><p>AI 负责拆解和生成，创作者仍可以在每个阶段修改剧本、审核资产，并决定是否进入下一步。</p><button class="creator-small-link" type="button" @click="emit('openOperator', 'workbench')">了解制作后台 <span>→</span></button></section>
      </aside>
    </div>
  </section>
</template>

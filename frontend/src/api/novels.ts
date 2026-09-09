import { apiRequest } from './client'
import type {
  ChapterRecord,
  EpisodeRecord,
  GenerationTaskRecord,
  NovelProjectRecord,
  NovelSourceSummary,
  EpisodeTaskPlanResponse,
  ProductionRunResponse,
} from '../types/task'

export interface NovelProjectCreatePayload {
  title: string
  language?: string
  target_episode_count?: number
  target_episode_duration_seconds?: number
  rights_status?: 'unknown' | 'pending' | 'confirmed' | 'denied'
}

export function createNovelProject(payload: NovelProjectCreatePayload): Promise<NovelProjectRecord> {
  return apiRequest<NovelProjectRecord>('/api/v1/novel-projects', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function getNovelProjects(): Promise<NovelProjectRecord[]> {
  return apiRequest<NovelProjectRecord[]>('/api/v1/novel-projects')
}

export function getNovelProject(projectId: string): Promise<NovelProjectRecord> {
  return apiRequest<NovelProjectRecord>(`/api/v1/novel-projects/${projectId}`)
}

export function uploadNovelSource(
  projectId: string,
  file: File,
  rightsStatus?: string,
): Promise<NovelSourceSummary> {
  const formData = new FormData()
  formData.append('file', file, file.name)
  if (rightsStatus) formData.append('rights_status', rightsStatus)
  return apiRequest<NovelSourceSummary>(`/api/v1/novel-projects/${projectId}/sources`, {
    method: 'POST',
    body: formData,
  })
}

export function getNovelChapters(projectId: string): Promise<ChapterRecord[]> {
  return apiRequest<ChapterRecord[]>(`/api/v1/novel-projects/${projectId}/chapters`)
}

function idempotencyHeaders(idempotencyKey?: string): HeadersInit | undefined {
  return idempotencyKey ? { 'Idempotency-Key': idempotencyKey } : undefined
}

export function createStoryBibleTask(projectId: string, idempotencyKey?: string): Promise<GenerationTaskRecord> {
  return apiRequest<GenerationTaskRecord>(`/api/v1/novel-projects/${projectId}/story-bible/tasks`, {
    method: 'POST',
    headers: idempotencyHeaders(idempotencyKey),
  })
}

export function createEpisodePlanTask(
  projectId: string,
  targetEpisodeCount?: number,
  idempotencyKey?: string,
): Promise<GenerationTaskRecord> {
  return apiRequest<GenerationTaskRecord>(`/api/v1/novel-projects/${projectId}/episodes/tasks`, {
    method: 'POST',
    headers: idempotencyHeaders(idempotencyKey),
    body: JSON.stringify(targetEpisodeCount ? { target_episode_count: targetEpisodeCount } : {}),
  })
}

export function createEpisodeScriptTask(episodeId: string, idempotencyKey?: string): Promise<GenerationTaskRecord> {
  return apiRequest<GenerationTaskRecord>(`/api/v1/episodes/${episodeId}/script/tasks`, {
    method: 'POST',
    headers: idempotencyHeaders(idempotencyKey),
  })
}

export function createEpisodeShotTask(episodeId: string, idempotencyKey?: string): Promise<GenerationTaskRecord> {
  return apiRequest<GenerationTaskRecord>(`/api/v1/episodes/${episodeId}/shots/tasks`, {
    method: 'POST',
    headers: idempotencyHeaders(idempotencyKey),
  })
}

export function getEpisodes(projectId: string): Promise<EpisodeRecord[]> {
  return apiRequest<EpisodeRecord[]>(`/api/v1/novel-projects/${projectId}/episodes`)
}

export function createEpisodeTaskPlan(
  projectId: string,
  payload: {
    episode_ids?: string[]
    label?: string
    production_mode?: boolean
    include_reference_images?: boolean
    include_narration?: boolean
    include_subtitles?: boolean
    include_bgm?: boolean
    include_video?: boolean
    include_assembly?: boolean
    subtitle_mode?: 'align' | 'asr'
    provider_profile_id?: string
    image_provider_profile_id?: string
    video_provider_profile_id?: string
    visual_quality_profile_id?: string
    shot_keyframe_mode?: 'auto' | 'always' | 'off'
    bgm_source_path?: string
    bgm_label?: string
    bgm_rights_status?: 'unknown' | 'pending' | 'confirmed' | 'denied'
    bgm_rights_holder?: string
    bgm_rights_reference?: string
  },
  idempotencyKey?: string,
): Promise<EpisodeTaskPlanResponse> {
  return apiRequest<EpisodeTaskPlanResponse>(`/api/v1/novel-projects/${projectId}/episode-task-plans`, {
    method: 'POST',
    headers: idempotencyHeaders(idempotencyKey),
    body: JSON.stringify(payload),
  })
}

export function startProductionRun(
  projectId: string,
  payload: {
    target_episode_count?: number
    episode_ids?: string[]
    label?: string
    production_mode?: boolean
    include_reference_images?: boolean
    include_narration?: boolean
    include_subtitles?: boolean
    include_bgm?: boolean
    include_video?: boolean
    include_assembly?: boolean
    subtitle_mode?: 'align' | 'asr'
    provider_profile_id?: string
    image_provider_profile_id?: string
    video_provider_profile_id?: string
    visual_quality_profile_id?: string
    shot_keyframe_mode?: 'auto' | 'always' | 'off'
    bgm_source_path?: string
    bgm_label?: string
    bgm_rights_status?: 'unknown' | 'pending' | 'confirmed' | 'denied'
    bgm_rights_holder?: string
    bgm_rights_reference?: string
    auto_advance?: boolean
  },
  idempotencyKey?: string,
): Promise<ProductionRunResponse> {
  return apiRequest<ProductionRunResponse>(`/api/v1/novel-projects/${projectId}/production-runs`, {
    method: 'POST',
    headers: idempotencyHeaders(idempotencyKey),
    body: JSON.stringify(payload),
  })
}

export function getLatestProductionRun(projectId: string): Promise<ProductionRunResponse> {
  return apiRequest<ProductionRunResponse>(
    `/api/v1/novel-projects/${projectId}/production-runs/latest`,
  )
}

export function getProductionRun(projectId: string, runId: string): Promise<ProductionRunResponse> {
  return apiRequest<ProductionRunResponse>(
    `/api/v1/novel-projects/${projectId}/production-runs/${runId}`,
  )
}

function controlProductionRun(
  projectId: string,
  runId: string,
  action: 'pause' | 'resume' | 'cancel',
): Promise<ProductionRunResponse> {
  return apiRequest<ProductionRunResponse>(
    `/api/v1/novel-projects/${projectId}/production-runs/${runId}/${action}`,
    { method: 'POST' },
  )
}

export function pauseProductionRun(projectId: string, runId: string): Promise<ProductionRunResponse> {
  return controlProductionRun(projectId, runId, 'pause')
}

export function resumeProductionRun(projectId: string, runId: string): Promise<ProductionRunResponse> {
  return controlProductionRun(projectId, runId, 'resume')
}

export function cancelProductionRun(projectId: string, runId: string): Promise<ProductionRunResponse> {
  return controlProductionRun(projectId, runId, 'cancel')
}

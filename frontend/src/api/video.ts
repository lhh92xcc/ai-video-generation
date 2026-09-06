import { apiRequest } from './client'
import type { GenerationTaskRecord } from '../types/task'

export interface VideoClipCreateRequest {
  prompt_override?: string
  negative_prompt?: string
  provider_profile_id?: string
  visual_quality_profile_id?: string
}

export function createVideoClipTask(
  episodeId: string,
  shotIndex: number,
  payload: VideoClipCreateRequest = {},
  idempotencyKey?: string,
): Promise<GenerationTaskRecord> {
  const headers = idempotencyKey ? { 'Idempotency-Key': idempotencyKey } : undefined
  return apiRequest<GenerationTaskRecord>(`/api/v1/episodes/${episodeId}/shots/${shotIndex}/video-clips`, {
    method: 'POST',
    headers,
    body: JSON.stringify(payload),
  })
}

export interface VideoAssemblyAudioTrackRequest {
  artifact_id: string
  track_type: 'narration' | 'bgm'
  start_seconds?: number
  volume?: number
  loop?: boolean
  fade_in_seconds?: number
  fade_out_seconds?: number
}

export interface VideoAssemblyCreateRequest {
  clip_task_ids: string[]
  audio_tracks?: VideoAssemblyAudioTrackRequest[]
  subtitle_artifact_id?: string
  output_format?: 'mp4'
}

export function createVideoAssemblyTask(
  episodeId: string,
  payload: VideoAssemblyCreateRequest,
  idempotencyKey?: string,
): Promise<GenerationTaskRecord> {
  const headers = idempotencyKey ? { 'Idempotency-Key': idempotencyKey } : undefined
  return apiRequest<GenerationTaskRecord>(`/api/v1/episodes/${episodeId}/video-renders`, {
    method: 'POST',
    headers,
    body: JSON.stringify(payload),
  })
}

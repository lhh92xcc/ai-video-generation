import { apiRequest } from './client'
import type { GenerationTaskRecord, SubtitleASRCreateRequest } from '../types/task'

export function createSubtitleASRTask(
  episodeId: string,
  payload: SubtitleASRCreateRequest,
  idempotencyKey?: string,
): Promise<GenerationTaskRecord> {
  const headers = idempotencyKey ? { 'Idempotency-Key': idempotencyKey } : undefined
  return apiRequest<GenerationTaskRecord>(`/api/v1/episodes/${episodeId}/subtitles/asr`, {
    method: 'POST',
    headers,
    body: JSON.stringify(payload),
  })
}

export function createSubtitleAlignmentTask(
  episodeId: string,
  payload: { text: string; language?: string; audio_duration_seconds: number },
  idempotencyKey?: string,
): Promise<GenerationTaskRecord> {
  const headers = idempotencyKey ? { 'Idempotency-Key': idempotencyKey } : undefined
  return apiRequest<GenerationTaskRecord>(`/api/v1/episodes/${episodeId}/subtitles/align`, {
    method: 'POST',
    headers,
    body: JSON.stringify(payload),
  })
}

import { apiRequest } from './client'
import type { AudioBGMCreateRequest, AudioNarrationCreateRequest, GenerationTaskRecord } from '../types/task'

export function createAudioNarrationTask(
  episodeId: string,
  payload: AudioNarrationCreateRequest,
  idempotencyKey?: string,
): Promise<GenerationTaskRecord> {
  const headers = idempotencyKey ? { 'Idempotency-Key': idempotencyKey } : undefined
  return apiRequest<GenerationTaskRecord>(`/api/v1/episodes/${episodeId}/audio`, {
    method: 'POST',
    headers,
    body: JSON.stringify(payload),
  })
}

export function createAudioBGMTask(
  episodeId: string,
  payload: AudioBGMCreateRequest,
  idempotencyKey?: string,
): Promise<GenerationTaskRecord> {
  const headers = idempotencyKey ? { 'Idempotency-Key': idempotencyKey } : undefined
  return apiRequest<GenerationTaskRecord>(`/api/v1/episodes/${episodeId}/bgm`, {
    method: 'POST',
    headers,
    body: JSON.stringify(payload),
  })
}

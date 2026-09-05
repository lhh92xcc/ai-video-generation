import { apiRequest } from './client'
import type {
  IdentityAuditStatus,
  IdentityCalibrationResponse,
  IdentityRetryResponse,
  VoiceAssetCreateRequest,
  VoiceAssetRecord,
} from '../types/novel'
import type { GenerationTaskRecord } from '../types/task'

function idempotencyHeaders(idempotencyKey?: string): HeadersInit | undefined {
  return idempotencyKey ? { 'Idempotency-Key': idempotencyKey } : undefined
}

export function getVoiceAssets(projectId: string): Promise<VoiceAssetRecord[]> {
  return apiRequest<VoiceAssetRecord[]>(`/api/v1/novel-projects/${projectId}/voice-assets`)
}

export function createVoiceAsset(
  projectId: string,
  payload: VoiceAssetCreateRequest,
): Promise<VoiceAssetRecord> {
  return apiRequest<VoiceAssetRecord>(`/api/v1/novel-projects/${projectId}/voice-assets`, {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function calibrateIdentityThresholds(
  projectId: string,
  payload: { episode_id?: string; thresholds: number[]; max_artifacts?: number },
): Promise<IdentityCalibrationResponse> {
  return apiRequest<IdentityCalibrationResponse>(
    `/api/v1/novel-projects/${projectId}/identity-audit/calibrate`,
    {
      method: 'POST',
      body: JSON.stringify(payload),
    },
  )
}

export function retryIdentityFailedVideoClips(
  projectId: string,
  payload: {
    episode_id?: string
    identity_statuses?: IdentityAuditStatus[]
    max_tasks?: number
    label?: string
  },
  idempotencyKey?: string,
): Promise<IdentityRetryResponse> {
  return apiRequest<IdentityRetryResponse>(
    `/api/v1/novel-projects/${projectId}/video-clips/retry-failed`,
    {
      method: 'POST',
      headers: idempotencyHeaders(idempotencyKey),
      body: JSON.stringify(payload),
    },
  )
}

export interface LipSyncCreateRequest {
  video_artifact_id: string
  audio_artifact_id: string
  face_region?: 'auto' | 'full_frame'
  face_padding?: number
}

export function createLipSyncTask(
  episodeId: string,
  payload: LipSyncCreateRequest,
  idempotencyKey?: string,
): Promise<GenerationTaskRecord> {
  return apiRequest<GenerationTaskRecord>(`/api/v1/episodes/${episodeId}/lip-sync`, {
    method: 'POST',
    headers: idempotencyHeaders(idempotencyKey),
    body: JSON.stringify(payload),
  })
}

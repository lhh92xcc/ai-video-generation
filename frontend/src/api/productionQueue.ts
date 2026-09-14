import { apiRequest } from './client'
import type {
  CleanupResponse,
  DeadLetterListResponse,
  GenerationTaskRecord,
  OperationalHealthResponse,
  ProductionQueueSnapshot,
} from '../types/task'

export function getOperationalHealth(params: {
  imageProviderProfileId?: string
  videoProviderProfileId?: string
} = {}): Promise<OperationalHealthResponse> {
  const query = new URLSearchParams()
  if (params.imageProviderProfileId) query.set('image_provider_profile_id', params.imageProviderProfileId)
  if (params.videoProviderProfileId) query.set('video_provider_profile_id', params.videoProviderProfileId)
  const suffix = query.toString() ? `?${query.toString()}` : ''
  return apiRequest<OperationalHealthResponse>(`/api/v1/system/health${suffix}`)
}

export function getProductionQueue(params: {
  projectId?: string
  limit?: number
} = {}): Promise<ProductionQueueSnapshot> {
  const query = new URLSearchParams({ limit: String(params.limit ?? 100) })
  if (params.projectId) query.set('project_id', params.projectId)
  return apiRequest<ProductionQueueSnapshot>(`/api/v1/system/queue?${query.toString()}`)
}

export function cleanupTemporaryFiles(): Promise<CleanupResponse> {
  return apiRequest<CleanupResponse>('/api/v1/system/cleanup', { method: 'POST' })
}

export function getDeadLetters(params: { projectId?: string; limit?: number } = {}): Promise<DeadLetterListResponse> {
  const query = new URLSearchParams({ limit: String(params.limit ?? 100) })
  if (params.projectId) query.set('project_id', params.projectId)
  return apiRequest<DeadLetterListResponse>(`/api/v1/system/dead-letters?${query.toString()}`)
}

export function requeueDeadLetterTask(taskId: string): Promise<GenerationTaskRecord> {
  return apiRequest<GenerationTaskRecord>(`/api/v1/system/dead-letters/${taskId}/requeue`, { method: 'POST' })
}

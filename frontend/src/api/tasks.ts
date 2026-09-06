import { apiRequest } from './client'
import type { ArtifactListResponse, ArtifactRecord, GenerationTaskListResponse, GenerationTaskRecord, TaskBatchListResponse, TaskBatchRecord, TaskBatchResumeResponse } from '../types/task'

export function getTasks(params: {
  projectId?: string
  status?: string
  limit?: number
} = {}): Promise<GenerationTaskListResponse> {
  const query = new URLSearchParams()
  if (params.projectId) query.set('project_id', params.projectId)
  if (params.status) query.set('status', params.status)
  query.set('limit', String(params.limit ?? 100))
  return apiRequest<GenerationTaskListResponse>(`/api/v1/tasks?${query.toString()}`)
}

export function getTask(taskId: string): Promise<GenerationTaskRecord> {
  return apiRequest<GenerationTaskRecord>(`/api/v1/tasks/${taskId}`)
}

export function retryTask(taskId: string): Promise<GenerationTaskRecord> {
  return apiRequest<GenerationTaskRecord>(`/api/v1/tasks/${taskId}/retry`, { method: 'POST' })
}

export function createTaskBatch(payload: {
  project_id: string
  task_ids: string[]
  label: string
}, idempotencyKey?: string): Promise<TaskBatchRecord> {
  const headers = idempotencyKey ? { 'Idempotency-Key': idempotencyKey } : undefined
  return apiRequest<TaskBatchRecord>('/api/v1/task-batches', {
    method: 'POST',
    headers,
    body: JSON.stringify(payload),
  })
}

export function getTaskBatches(projectId: string, limit = 50): Promise<TaskBatchListResponse> {
  const query = new URLSearchParams({ project_id: projectId, limit: String(limit) })
  return apiRequest<TaskBatchListResponse>(`/api/v1/task-batches?${query.toString()}`)
}

export function getTaskBatch(batchId: string): Promise<TaskBatchRecord> {
  return apiRequest<TaskBatchRecord>(`/api/v1/task-batches/${batchId}`)
}

export function resumeTaskBatch(batchId: string): Promise<TaskBatchResumeResponse> {
  return apiRequest<TaskBatchResumeResponse>(`/api/v1/task-batches/${batchId}/resume`, { method: 'POST' })
}

export function getArtifacts(params: {
  projectId?: string
  type?: string
  limit?: number
  expiresInSeconds?: number
}): Promise<ArtifactListResponse> {
  const query = new URLSearchParams({ limit: String(params.limit ?? 100) })
  if (params.projectId) query.set('project_id', params.projectId)
  if (params.type) query.set('type', params.type)
  if (params.expiresInSeconds) query.set('expires_in_seconds', String(params.expiresInSeconds))
  return apiRequest<ArtifactListResponse>(`/api/v1/artifacts?${query.toString()}`)
}

export function getArtifact(
  artifactId: string,
  expiresInSeconds = 3600,
): Promise<ArtifactRecord> {
  const query = new URLSearchParams({ expires_in_seconds: String(expiresInSeconds) })
  return apiRequest<ArtifactRecord>(`/api/v1/artifacts/${artifactId}?${query.toString()}`)
}

/**
 * Browser-safe Artifact content endpoint.  Local storage has no public URL,
 * so media previews and downloads use this authorized API fallback.
 */
export function getArtifactContentUrl(artifactId: string, download = false): string {
  return `/api/v1/artifacts/${artifactId}/content${download ? '?download=true' : ''}`
}

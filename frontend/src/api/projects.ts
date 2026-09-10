import { apiRequest } from './client'
import type { GenerationTaskRecord } from '../types/task'
import type {
  TopicProjectCreatePayload,
  TopicProjectListResponse,
  TopicProjectRecord,
  TopicProductionCreatePayload,
  TopicProductionResponse,
} from '../types/project'

export function createTopicProject(payload: TopicProjectCreatePayload): Promise<TopicProjectRecord> {
  return apiRequest<TopicProjectRecord>('/api/v1/projects', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function getTopicProjects(): Promise<TopicProjectListResponse> {
  return apiRequest<TopicProjectListResponse>('/api/v1/projects')
}

export function createTopicGeneration(projectId: string, idempotencyKey?: string): Promise<GenerationTaskRecord> {
  const headers = idempotencyKey ? { 'Idempotency-Key': idempotencyKey } : undefined
  return apiRequest<GenerationTaskRecord>(`/api/v1/projects/${projectId}/generations`, {
    method: 'POST',
    headers,
  })
}

export function startTopicProduction(
  projectId: string,
  payload: TopicProductionCreatePayload = {},
  idempotencyKey?: string,
): Promise<TopicProductionResponse> {
  const headers = idempotencyKey ? { 'Idempotency-Key': idempotencyKey } : undefined
  return apiRequest<TopicProductionResponse>(`/api/v1/projects/${projectId}/production-runs`, {
    method: 'POST',
    headers,
    body: JSON.stringify(payload),
  })
}

export function getTopicProductionRun(projectId: string, runId: string): Promise<TopicProductionResponse> {
  return apiRequest<TopicProductionResponse>(
    `/api/v1/projects/${projectId}/production-runs/${runId}`,
  )
}

export function getLatestTopicProductionRun(projectId: string): Promise<TopicProductionResponse> {
  return apiRequest<TopicProductionResponse>(
    `/api/v1/projects/${projectId}/production-runs/latest`,
  )
}

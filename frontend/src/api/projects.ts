import { apiRequest } from './client'
import type { GenerationTaskRecord } from '../types/task'
import type { TopicProjectCreatePayload, TopicProjectListResponse, TopicProjectRecord } from '../types/project'

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

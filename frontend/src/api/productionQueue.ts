import { apiRequest } from './client'
import type { CleanupResponse, OperationalHealthResponse, ProductionQueueSnapshot } from '../types/task'

export function getOperationalHealth(): Promise<OperationalHealthResponse> {
  return apiRequest<OperationalHealthResponse>('/api/v1/system/health')
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

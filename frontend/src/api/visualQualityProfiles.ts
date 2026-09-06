import { apiRequest } from './client'
import type { VisualQualityProfileListResponse } from '../types/provider'

export function getVisualQualityProfiles(): Promise<VisualQualityProfileListResponse> {
  return apiRequest<VisualQualityProfileListResponse>('/api/v1/visual-quality-profiles')
}

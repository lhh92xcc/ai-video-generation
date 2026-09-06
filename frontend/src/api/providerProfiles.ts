import { apiRequest } from './client'
import type { ProviderProfileListResponse } from '../types/provider'

export type ProviderCapability = 'asr' | 'image' | 'video'

export function getProviderProfiles(capability: ProviderCapability = 'asr'): Promise<ProviderProfileListResponse> {
  const query = new URLSearchParams({ capability })
  return apiRequest<ProviderProfileListResponse>(`/api/v1/provider-profiles?${query.toString()}`)
}

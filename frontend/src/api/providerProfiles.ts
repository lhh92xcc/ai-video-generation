import { apiRequest } from './client'
import type { ProviderProfileListResponse } from '../types/provider'

export function getProviderProfiles(capability = 'asr'): Promise<ProviderProfileListResponse> {
  const query = new URLSearchParams({ capability })
  return apiRequest<ProviderProfileListResponse>(`/api/v1/provider-profiles?${query.toString()}`)
}

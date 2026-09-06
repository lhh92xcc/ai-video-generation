export interface ProviderProfile {
  profile_id: string
  capability: 'asr' | 'image' | 'video'
  label: string
  provider: string
  model: string
  base_url: string
  api_key_env: string | null
  configured: boolean
  default: boolean
}

export interface ProviderProfileListResponse {
  items: ProviderProfile[]
  total: number
  default_profile_id: string
}

export interface ApiErrorBody {
  error?: {
    code?: string
    message?: string
    request_id?: string
    details?: Record<string, unknown>
  }
}

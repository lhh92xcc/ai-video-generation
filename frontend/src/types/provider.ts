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

export interface VisualQualityProfile {
  profile_id: string
  label: string
  description: string
  recommended_for: string
  image_width: number
  image_height: number
  image_steps: number
  image_guidance: number
  image_identity_weight: number
  video_width: number
  video_height: number
  video_fps: number
  video_steps: number
  video_cfg: number
  video_noise_aug_strength: number
  video_motion_zoom: number
  version: string
}

export interface VisualQualityProfileListResponse {
  items: VisualQualityProfile[]
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

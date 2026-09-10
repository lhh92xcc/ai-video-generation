export type TopicProjectAspectRatio = '9:16' | '16:9' | '1:1'

export interface TopicProjectCreatePayload {
  title: string
  topic: string
  language?: string
  target_duration_seconds?: number
  aspect_ratio?: TopicProjectAspectRatio
  tone?: string
}

export interface TopicProjectRecord {
  id: string
  title: string
  topic: string
  language: string
  target_duration_seconds: number
  aspect_ratio: TopicProjectAspectRatio
  tone: string
  status: string
  created_at: string
  updated_at: string
}

export interface TopicProjectListResponse {
  items: TopicProjectRecord[]
  total: number
}

export type TopicProductionStatus = 'active' | 'blocked' | 'paused' | 'completed' | 'failed' | 'canceled'

export interface TopicProductionCreatePayload {
  production_mode?: true
  include_narration?: boolean
  include_subtitles?: boolean
  include_video?: boolean
  include_assembly?: boolean
  subtitle_mode?: 'align'
  video_provider_profile_id?: string
  visual_quality_profile_id?: string
}

export interface TopicProductionResponse {
  project_id: string
  run_id: string
  status: TopicProductionStatus
  stage: string
  task_ids: string[]
  auto_advance: boolean
  message: string
  error_code?: string | null
  error_message?: string | null
}

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

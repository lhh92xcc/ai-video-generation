export interface SubtitleASRCreateRequest {
  audio_artifact_id: string
  language?: string
  reference_text?: string
  provider_profile_id?: string
}

export interface AudioNarrationLineRequest {
  line_index: number
  speaker: string
  text: string
  voice_asset_id?: string
  pause_after_seconds?: number
}

export interface AudioNarrationCreateRequest {
  text?: string
  voice?: string
  rate?: string
  volume?: string
  voice_asset_id?: string
  speaker?: string
  voice_lines?: AudioNarrationLineRequest[]
}

export type RightsStatus = 'unknown' | 'pending' | 'confirmed' | 'denied'

export interface AudioBGMCreateRequest {
  source_path?: string
  label: string
  rights_status: RightsStatus
  rights_holder?: string
  rights_reference?: string
}

export type TaskStatus = 'created' | 'queued' | 'running' | 'succeeded' | 'failed' | 'canceled'
export type TaskBatchStatus = 'created' | 'running' | 'succeeded' | 'partial' | 'failed'

export interface TaskError {
  code: string
  message: string
  retryable?: boolean
  details?: Record<string, unknown>
}

export interface ArtifactSummary {
  id: string
  type: string
  status: 'ready'
  provider: string
  created_at: string
  metadata: Record<string, unknown>
  preview: Record<string, unknown> | null
}

export interface GenerationTaskRecord {
  id: string
  project_id: string
  kind: string
  status: TaskStatus
  input_data: Record<string, unknown>
  current_stage: string | null
  progress: number
  error: TaskError | null
  stages: Array<Record<string, unknown>>
  artifacts: ArtifactSummary[]
  created_at: string
  updated_at: string
}

export interface GenerationTaskListResponse {
  items: GenerationTaskRecord[]
  total: number
}

export interface TaskBatchRecord {
  id: string
  project_id: string
  task_ids: string[]
  label: string
  status: TaskBatchStatus
  total_count: number
  succeeded_count: number
  failed_count: number
  active_count: number
  created_at: string
  updated_at: string
}

export interface TaskBatchListResponse {
  items: TaskBatchRecord[]
  total: number
}

export interface TaskBatchResumeResponse {
  batch: TaskBatchRecord
  retried_task_ids: string[]
  skipped_task_ids: string[]
}

export type EpisodeTaskPlanAction = 'created' | 'reused' | 'skipped' | 'blocked'

export interface EpisodeTaskPlanItem {
  episode_id: string
  episode_number: number
  stage:
    | 'novel_episode_script'
    | 'novel_shot_list'
    | 'asset_reference_image'
    | 'audio_narration'
    | 'subtitle_align'
    | 'subtitle_asr'
    | 'audio_bgm'
    | 'video_clip'
    | 'video_assembly'
    | null
  task_id: string | null
  task_ids: string[]
  action: EpisodeTaskPlanAction
  reason: string
  blocked_reasons: string[]
}

export interface EpisodeTaskPlanResponse {
  project_id: string
  label: string
  auto_run_id: string | null
  auto_advance: boolean
  batch: TaskBatchRecord | null
  batches: TaskBatchRecord[]
  items: EpisodeTaskPlanItem[]
  created_count: number
  reused_count: number
  skipped_count: number
  blocked_count: number
}

export type ProductionRunStatus = 'active' | 'blocked' | 'completed' | 'failed'

export interface ProductionRunResponse {
  project_id: string
  run_id: string
  status: ProductionRunStatus
  stage: string
  task_ids: string[]
  auto_advance: boolean
  message: string
  plan: EpisodeTaskPlanResponse | null
}

export type OperationalComponentStatus = 'ok' | 'degraded' | 'unavailable' | 'not_configured'

export interface OperationalComponentHealth {
  name: string
  status: OperationalComponentStatus
  message: string
  latency_ms: number | null
}

export interface OperationalHealthResponse {
  status: 'ok' | 'degraded'
  profile: string
  checked_at: string
  disk_free_gb: number
  gpu_lock_enabled: boolean
  gpu_lock_busy: boolean
  worker: Record<string, unknown>
  components: OperationalComponentHealth[]
}

export interface ProductionQueueRun {
  id: string
  status: string
  task_count: number
  active_count: number
  succeeded_count: number
  failed_count: number
  progress: number
  episode_ids: string[]
  updated_at: string
}

export interface ProductionQueueSnapshot {
  profile: string
  refreshed_at: string
  counts: Record<string, number>
  gpu_lock_enabled: boolean
  gpu_lock_busy: boolean
  worker: Record<string, unknown>
  auto_runs: ProductionQueueRun[]
  tasks: GenerationTaskRecord[]
}

export interface CleanupResponse {
  scanned_roots: string[]
  deleted_files: number
  deleted_bytes: number
  skipped_files: number
  errors: string[]
  warnings: string[]
  free_bytes_before: number
  free_bytes_after: number
  min_free_bytes: number
  low_disk: boolean
  free_gb_before: number
  free_gb_after: number
}

export interface ArtifactRecord extends ArtifactSummary {
  task_id: string
  project_id: string
  download_url: string | null
}

export interface ArtifactListResponse {
  items: ArtifactRecord[]
  total: number
}

export interface NovelProjectRecord {
  id: string
  title: string
  language: string
  target_episode_count: number
  target_episode_duration_seconds: number
  rights_status: string
  status: string
  source_id: string | null
  story_bible_id: string | null
  created_at: string
  updated_at: string
}

export interface NovelSourceSummary {
  id: string
  project_id: string
  filename: string
  content_type: 'text/plain' | 'text/markdown'
  size_bytes: number
  checksum: string
  rights_status: string
  chapter_count: number
  created_at: string
}

export interface ChapterRecord {
  id: string
  source_id: string
  chapter_number: number
  title: string
  content: string
  start_offset: number
  end_offset: number
  created_at: string
}

export interface EpisodeRecord {
  id: string
  project_id: string
  episode_number: number
  status: string
  outline: {
    title: string
    logline: string
    objective: string
    conflict: string
    turning_point: string
    ending_hook: string
    source_chapter_numbers: number[]
    target_duration_seconds: number
  }
}

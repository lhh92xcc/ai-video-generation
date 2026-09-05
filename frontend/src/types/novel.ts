import type { EpisodeRecord } from './task'

export type AssetType = 'character' | 'location' | 'prop'
export type AssetStatus = 'draft' | 'needs_review' | 'ready' | 'archived'

export type ProjectRole = 'viewer' | 'editor' | 'reviewer' | 'owner'
export type ProjectPermission =
  | 'project:read'
  | 'script:edit'
  | 'asset:edit'
  | 'asset:review'
  | 'project:manage_members'

export interface ProjectAccessRecord {
  project_id: string
  actor_id: string
  actor_name: string
  role: ProjectRole
  permissions: ProjectPermission[]
}

export interface ProjectMemberRecord {
  id: string
  project_id: string
  actor_id: string
  actor_name: string
  role: ProjectRole
  created_at: string
  updated_at: string
}

export type AuditEntityType = 'episode_script' | 'episode_script_draft' | 'asset' | 'asset_review' | 'project_member'
export type AuditAction =
  | 'script_draft_saved'
  | 'script_draft_deleted'
  | 'script_version_published'
  | 'asset_version_created'
  | 'asset_review_created'
  | 'project_member_added'
  | 'project_member_updated'
  | 'project_member_role_changed'
  | 'project_member_removed'

export interface AuditLogRecord {
  id: string
  project_id: string
  episode_id: string | null
  entity_type: AuditEntityType
  entity_id: string
  action: AuditAction
  actor_id: string
  actor_name: string
  metadata: Record<string, unknown>
  created_at: string
}

export interface ProjectMemberUpsertRequest {
  actor_name: string
  role: ProjectRole
}

export type ProjectInvitationStatus = 'pending' | 'accepted' | 'revoked' | 'expired'

export interface ProjectInvitationSummary {
  id: string
  project_id: string
  invitee_actor_id: string
  invitee_name: string
  role: ProjectRole
  status: ProjectInvitationStatus
  expires_at: string
  invited_by_actor_id: string
  invited_by_name: string
  accepted_at: string | null
  accepted_by_actor_id: string | null
  revoked_at: string | null
  created_at: string
  updated_at: string
}

export interface ProjectInvitationCreateRequest {
  invitee_actor_id: string
  invitee_name: string
  role: ProjectRole
  expires_in_seconds?: number
}

export interface ProjectInvitationCreateResponse {
  invitation: ProjectInvitationSummary
  accept_token: string
}

export interface AssetRecord {
  id: string
  asset_key: string
  project_id: string
  story_bible_id: string
  asset_type: AssetType
  name: string
  aliases: string[]
  version: number
  status: AssetStatus
  content: Record<string, unknown>
  source_chapter_numbers: number[]
  provider: string
  model: string
  duration_ms: number
  created_at: string
  updated_at: string
}

export interface AssetReviewRecord {
  id: string
  asset_key: string
  asset_id: string
  version: number
  from_status: AssetStatus
  to_status: AssetStatus
  reviewer: string
  comment: string
  created_at: string
}

export interface AssetReviewResult {
  asset: AssetRecord
  review: AssetReviewRecord
}

export interface AssetReviewRequest {
  status: AssetStatus
  reviewer: string
  comment?: string
}

export interface AssetBatchReviewRequest {
  asset_ids?: string[]
  status: 'ready'
  reviewer: string
  comment?: string
}

export interface AssetVersionCreateRequest {
  expected_version?: number
  status: AssetStatus
  content: Record<string, unknown>
  aliases?: string[]
  source_chapter_numbers: number[]
}

export interface DialogueLine {
  line_index: number
  speaker: string
  text: string
  emotion: string
  delivery_notes: string
}

export interface SceneScriptContent {
  scene_index: number
  title: string
  location: string
  time: string
  characters: string[]
  duration_seconds: number
  action: string
  narration: string
  dialogues: DialogueLine[]
  emotion: string
  source_chapter_numbers: number[]
}

export interface EpisodeScriptContent {
  episode_number: number
  title: string
  logline: string
  opening_hook: string
  ending_hook: string
  total_duration_seconds: number
  scenes: SceneScriptContent[]
  risk_notes: string[]
}

export interface EpisodeScriptRecord {
  id: string
  project_id: string
  episode_id: string
  version: number
  content: EpisodeScriptContent
  provider: string
  model: string
  duration_ms: number
  created_at: string
}

export interface EpisodeScriptDraftRecord {
  id: string
  project_id: string
  episode_id: string
  base_script_id: string
  base_script_version: number
  revision: number
  content: EpisodeScriptContent
  created_at: string
  updated_at: string
}

export type EpisodeScriptImpactShotGate = 'ready' | 'needs_review' | 'blocked'
export type EpisodeScriptImpactAction = 'generate_shot_list' | 'regenerate_shot_list' | 'review_asset_bindings'

export interface EpisodeScriptImpactShot {
  shot_index: number
  scene_index: number
  requires_regeneration: boolean
  asset_gate: EpisodeScriptImpactShotGate
  unresolved_asset_requirements: string[]
  asset_binding_warnings: string[]
}

export interface EpisodeScriptImpactAsset {
  asset_key: string
  asset_type: AssetType
  name: string
  version: number
  status: AssetStatus
  shot_indexes: number[]
  requires_review: boolean
}

export interface EpisodeScriptImpactReport {
  episode_id: string
  script_id: string
  script_version: number
  shot_list_id: string | null
  shot_list_version: number | null
  shot_list_script_id: string | null
  shot_list_state: 'missing' | 'current' | 'stale'
  requires_shot_regeneration: boolean
  video_generation_gate: 'ready' | 'blocked'
  requires_asset_review: boolean
  affected_shot_count: number
  unresolved_asset_requirement_count: number
  asset_binding_warning_count: number
  shots: EpisodeScriptImpactShot[]
  assets: EpisodeScriptImpactAsset[]
  recommended_actions: EpisodeScriptImpactAction[]
}

export interface EpisodeScriptVersionCreateRequest {
  expected_version?: number
  content: EpisodeScriptContent
}

export interface EpisodeScriptDraftUpsertRequest {
  expected_revision: number
  content: EpisodeScriptContent
}

export interface EpisodeScriptDraftMergePreviewRequest {
  base_script_id: string
  base_script_version: number
  content: EpisodeScriptContent
}

export interface EpisodeScriptDraftMergePreviewResponse {
  episode_id: string
  base_script_id: string
  base_script_version: number
  current_revision: number
  safe_to_apply: boolean
  mergeable_paths: string[]
  conflict_paths: string[]
  merged_content: EpisodeScriptContent | null
}

export interface ShotAssetReference {
  asset_key: string
  asset_type: AssetType
  name: string
  version: number
  status: AssetStatus
  match_kind: 'name' | 'alias'
  matched_text: string
}

export interface ShotContent {
  shot_index: number
  scene_index: number
  duration_seconds: number
  shot_size: string
  camera_movement: string
  characters: string[]
  location: string
  visual_prompt: string
  dialogue_refs: number[]
  audio_requirements: string[]
  asset_requirements: string[]
  asset_refs: ShotAssetReference[]
  unresolved_asset_requirements: string[]
  asset_binding_warnings: string[]
  continuity_notes: string
}

export interface ShotListRecord {
  id: string
  project_id: string
  episode_id: string
  script_id: string
  version: number
  shots: ShotContent[]
  provider: string
  model: string
  duration_ms: number
  created_at: string
}

export type WorkbenchEpisodeRecord = EpisodeRecord

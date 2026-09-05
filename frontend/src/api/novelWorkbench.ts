import { apiRequest } from './client'
import type {
  AssetBatchReviewRequest,
  AssetRecord,
  AssetReviewRecord,
  AssetReviewRequest,
  AssetReviewResult,
  AssetVersionCreateRequest,
  AssetType,
  AuditEntityType,
  AuditLogRecord,
  EpisodeScriptDraftMergePreviewRequest,
  EpisodeScriptDraftMergePreviewResponse,
  EpisodeScriptDraftRecord,
  EpisodeScriptDraftUpsertRequest,
  EpisodeScriptVersionCreateRequest,
  EpisodeScriptImpactReport,
  EpisodeScriptRecord,
  ProjectAccessRecord,
  ProjectMemberRecord,
  ProjectMemberUpsertRequest,
  ProjectInvitationCreateRequest,
  ProjectInvitationCreateResponse,
  ProjectInvitationSummary,
  ShotListRecord,
} from '../types/novel'
import type { EpisodeRecord } from '../types/task'

export function getEpisode(episodeId: string): Promise<EpisodeRecord> {
  return apiRequest<EpisodeRecord>(`/api/v1/episodes/${episodeId}`)
}

export function getProjectAccess(projectId: string): Promise<ProjectAccessRecord> {
  return apiRequest<ProjectAccessRecord>(`/api/v1/novel-projects/${projectId}/access`)
}

export function getProjectMembers(projectId: string): Promise<{ items: ProjectMemberRecord[]; total: number }> {
  return apiRequest<{ items: ProjectMemberRecord[]; total: number }>(`/api/v1/novel-projects/${projectId}/members`)
}

export function upsertProjectMember(
  projectId: string,
  actorId: string,
  payload: ProjectMemberUpsertRequest,
): Promise<ProjectMemberRecord> {
  return apiRequest<ProjectMemberRecord>(`/api/v1/novel-projects/${projectId}/members/${encodeURIComponent(actorId)}`, {
    method: 'PUT',
    body: JSON.stringify(payload),
  })
}

export function removeProjectMember(projectId: string, actorId: string): Promise<void> {
  return apiRequest<void>(`/api/v1/novel-projects/${projectId}/members/${encodeURIComponent(actorId)}`, {
    method: 'DELETE',
  })
}

export function getProjectInvitations(projectId: string): Promise<{ items: ProjectInvitationSummary[]; total: number }> {
  return apiRequest<{ items: ProjectInvitationSummary[]; total: number }>(`/api/v1/novel-projects/${projectId}/invitations`)
}

export function createProjectInvitation(
  projectId: string,
  payload: ProjectInvitationCreateRequest,
): Promise<ProjectInvitationCreateResponse> {
  return apiRequest<ProjectInvitationCreateResponse>(`/api/v1/novel-projects/${projectId}/invitations`, {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function revokeProjectInvitation(projectId: string, invitationId: string): Promise<void> {
  return apiRequest<void>(`/api/v1/novel-projects/${projectId}/invitations/${invitationId}`, {
    method: 'DELETE',
  })
}

export function getEpisodeScript(episodeId: string): Promise<EpisodeScriptRecord> {
  return apiRequest<EpisodeScriptRecord>(`/api/v1/episodes/${episodeId}/script`)
}

export function getEpisodeScriptDraft(episodeId: string): Promise<EpisodeScriptDraftRecord> {
  return apiRequest<EpisodeScriptDraftRecord>(`/api/v1/episodes/${episodeId}/script/draft`)
}

export function saveEpisodeScriptDraft(
  episodeId: string,
  payload: EpisodeScriptDraftUpsertRequest,
): Promise<EpisodeScriptDraftRecord> {
  return apiRequest<EpisodeScriptDraftRecord>(`/api/v1/episodes/${episodeId}/script/draft`, {
    method: 'PUT',
    body: JSON.stringify(payload),
  })
}

export function previewEpisodeScriptDraftMerge(
  episodeId: string,
  payload: EpisodeScriptDraftMergePreviewRequest,
): Promise<EpisodeScriptDraftMergePreviewResponse> {
  return apiRequest<EpisodeScriptDraftMergePreviewResponse>(
    `/api/v1/episodes/${episodeId}/script/draft/merge-preview`,
    {
      method: 'POST',
      body: JSON.stringify(payload),
    },
  )
}

export function deleteEpisodeScriptDraft(episodeId: string): Promise<void> {
  return apiRequest<void>(`/api/v1/episodes/${episodeId}/script/draft`, {
    method: 'DELETE',
  })
}

export function getEpisodeScriptImpact(episodeId: string): Promise<EpisodeScriptImpactReport> {
  return apiRequest<EpisodeScriptImpactReport>(`/api/v1/episodes/${episodeId}/script/impact`)
}

export function getAuditLogs(
  projectId: string,
  entityType?: AuditEntityType,
  entityId?: string,
): Promise<{ items: AuditLogRecord[]; total: number }> {
  const params = new URLSearchParams()
  if (entityType) params.set('entity_type', entityType)
  if (entityId) params.set('entity_id', entityId)
  const query = params.toString()
  return apiRequest<{ items: AuditLogRecord[]; total: number }>(
    `/api/v1/novel-projects/${projectId}/audit-logs${query ? `?${query}` : ''}`,
  )
}

export function getEpisodeShots(episodeId: string): Promise<ShotListRecord> {
  return apiRequest<ShotListRecord>(`/api/v1/episodes/${episodeId}/shots`)
}

export function getAssets(projectId: string, assetType?: AssetType): Promise<AssetRecord[]> {
  const query = assetType ? `?asset_type=${encodeURIComponent(assetType)}` : ''
  return apiRequest<AssetRecord[]>(`/api/v1/novel-projects/${projectId}/assets${query}`)
}

export function getAssetReviews(assetId: string): Promise<AssetReviewRecord[]> {
  return apiRequest<AssetReviewRecord[]>(`/api/v1/assets/${assetId}/reviews`)
}

export function reviewAsset(assetId: string, payload: AssetReviewRequest): Promise<AssetReviewResult> {
  return apiRequest<AssetReviewResult>(`/api/v1/assets/${assetId}/reviews`, {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function reviewProjectAssets(projectId: string, payload: AssetBatchReviewRequest): Promise<AssetReviewResult[]> {
  return apiRequest<AssetReviewResult[]>(`/api/v1/novel-projects/${projectId}/assets/review-batch`, {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function saveAssetVersion(assetId: string, payload: AssetVersionCreateRequest): Promise<AssetRecord> {
  return apiRequest<AssetRecord>(`/api/v1/assets/${assetId}/versions`, {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function saveEpisodeScriptVersion(
  episodeId: string,
  payload: EpisodeScriptVersionCreateRequest,
): Promise<EpisodeScriptRecord> {
  return apiRequest<EpisodeScriptRecord>(`/api/v1/episodes/${episodeId}/script/versions`, {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

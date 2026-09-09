<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { ApiClientError } from '../api/client'
import { getEpisodes, getNovelProjects } from '../api/novels'
import {
  getAssetReviews,
  getAssets,
  getAuditLogs,
  getProjectInvitations,
  getEpisodeScriptDraft,
  getProjectAccess,
  getProjectMembers,
  getEpisodeScript,
  getEpisodeScriptImpact,
  getEpisodeShots,
  previewEpisodeScriptDraftMerge,
  saveEpisodeScriptDraft,
  saveAssetVersion,
  saveEpisodeScriptVersion,
  reviewAsset,
  reviewProjectAssets,
  createProjectInvitation,
  removeProjectMember,
  revokeProjectInvitation,
  upsertProjectMember,
} from '../api/novelWorkbench'
import type {
  AssetRecord,
  AssetReviewRecord,
  AssetStatus,
  AssetType,
  AuditAction,
  AuditLogRecord,
  DialogueLine,
  EpisodeScriptContent,
  EpisodeScriptDraftMergePreviewResponse,
  EpisodeScriptDraftRecord,
  EpisodeScriptImpactAction,
  EpisodeScriptImpactReport,
  EpisodeScriptRecord,
  ProjectAccessRecord,
  ProjectMemberRecord,
  ProjectInvitationStatus,
  ProjectInvitationSummary,
  ProjectPermission,
  ProjectRole,
  SceneScriptContent,
  ShotListRecord,
} from '../types/novel'
import type { EpisodeRecord, NovelProjectRecord } from '../types/task'

type ContentTab = 'script' | 'shots'

const projects = ref<NovelProjectRecord[]>([])
const episodes = ref<EpisodeRecord[]>([])
const assets = ref<AssetRecord[]>([])
const script = ref<EpisodeScriptRecord | null>(null)
const shots = ref<ShotListRecord | null>(null)
const scriptImpact = ref<EpisodeScriptImpactReport | null>(null)
const recoverableDraft = ref<EpisodeScriptDraftRecord | null>(null)
const reviews = ref<AssetReviewRecord[]>([])
const auditLogs = ref<AuditLogRecord[]>([])
const projectAccess = ref<ProjectAccessRecord | null>(null)
const projectMembers = ref<ProjectMemberRecord[]>([])
const projectInvitations = ref<ProjectInvitationSummary[]>([])

const projectId = ref('')
const episodeId = ref('')
const selectedAssetId = ref('')
const contentTab = ref<ContentTab>('script')
const assetTypeFilter = ref<'all' | AssetType>('all')
const assetStatusFilter = ref<'all' | AssetStatus>('all')
const reviewStatus = ref<AssetStatus>('ready')
const reviewer = ref('本地管理员')
const reviewComment = ref('')
const editingScript = ref(false)
const scriptDraft = ref<EpisodeScriptContent | null>(null)
const savingScript = ref(false)
const savingDraft = ref(false)
const draftRevision = ref(0)
const draftDirty = ref(false)
const draftConflict = ref(false)
const draftConflictDetails = ref<Record<string, unknown> | null>(null)
const mergePreview = ref<EpisodeScriptDraftMergePreviewResponse | null>(null)
const loadingMergePreview = ref(false)
let draftSaveTimer: ReturnType<typeof setTimeout> | null = null
const editingAsset = ref(false)
const assetDraftContent = ref<Record<string, unknown>>({})
const assetDraftAliases = ref('')
const assetDraftSourceChapters = ref('')
const assetDraftStatus = ref<AssetStatus>('needs_review')
const savingAsset = ref(false)
const memberActorId = ref('')
const memberActorName = ref('')
const memberRole = ref<ProjectRole>('viewer')
const editingMemberActorId = ref<string | null>(null)
const savingMember = ref(false)
const removingMemberId = ref<string | null>(null)
const invitationActorId = ref('')
const invitationActorName = ref('')
const invitationRole = ref<ProjectRole>('viewer')
const invitationExpiresInDays = ref(7)
const lastInvitationToken = ref('')
const creatingInvitation = ref(false)
const revokingInvitationId = ref<string | null>(null)

const loadingProjects = ref(false)
const loadingResources = ref(false)
const loadingEpisode = ref(false)
const loadingReviews = ref(false)
const loadingAuditLogs = ref(false)
const loadingMembers = ref(false)
const loadingInvitations = ref(false)
const submittingReview = ref(false)
const submittingBatchReview = ref(false)
const errorMessage = ref<string | null>(null)
const successMessage = ref<string | null>(null)

const assetTypeLabels: Record<AssetType | 'all', string> = {
  all: '全部资产',
  character: '角色',
  location: '场景',
  prop: '道具',
}
const assetStatusLabels: Record<AssetStatus | 'all', string> = {
  all: '全部状态',
  draft: '草稿',
  needs_review: '待审核',
  ready: '已就绪',
  archived: '已归档',
}
const contentLabels: Record<string, string> = {
  age_range: '年龄段',
  role: '角色定位',
  traits: '性格特征',
  appearance: '外观描述',
  relationships: '人物关系',
  voice_notes: '声音备注',
  description: '描述',
  time_period: '时代/时间',
  atmosphere: '氛围',
  visual_keywords: '视觉关键词',
  purpose: '用途',
  continuity_notes: '连续性备注',
}
const reviewTransitions: Record<AssetStatus, AssetStatus[]> = {
  draft: ['needs_review', 'archived'],
  needs_review: ['ready', 'draft', 'archived'],
  ready: ['needs_review', 'archived'],
  archived: ['draft', 'needs_review'],
}

const selectedProject = computed(() => projects.value.find((item) => item.id === projectId.value) ?? null)
const hasPermission = (permission: ProjectPermission) => projectAccess.value?.permissions.includes(permission) ?? false
const canEditScript = computed(() => hasPermission('script:edit'))
const canEditAsset = computed(() => hasPermission('asset:edit'))
const canReviewAsset = computed(() => hasPermission('asset:review'))
const canManageMembers = computed(() => hasPermission('project:manage_members'))
const roleLabels: Record<ProjectRole, string> = {
  viewer: '只读成员',
  editor: '编辑成员',
  reviewer: '审核成员',
  owner: '项目所有者',
}
const roleDescriptions: Record<ProjectRole, string> = {
  viewer: '查看项目内容和成员',
  editor: '编辑剧本和资产',
  reviewer: '审核资产版本',
  owner: '管理成员并拥有全部权限',
}
const projectRoles: ProjectRole[] = ['viewer', 'editor', 'reviewer', 'owner']
const invitationStatusLabels: Record<ProjectInvitationStatus, string> = {
  pending: '待接受',
  accepted: '已接受',
  revoked: '已撤销',
  expired: '已过期',
}
const selectedEpisode = computed(() => episodes.value.find((item) => item.id === episodeId.value) ?? null)
const filteredAssets = computed(() => assets.value.filter((asset) => {
  const matchesType = assetTypeFilter.value === 'all' || asset.asset_type === assetTypeFilter.value
  const matchesStatus = assetStatusFilter.value === 'all' || asset.status === assetStatusFilter.value
  return matchesType && matchesStatus
}))
const selectedAsset = computed(() => assets.value.find((asset) => asset.id === selectedAssetId.value) ?? null)
const selectedAssetContent = computed(() => selectedAsset.value ? Object.entries(selectedAsset.value.content) : [])
const reviewOptions = computed(() => selectedAsset.value ? reviewTransitions[selectedAsset.value.status] : [])
const readyAssetCount = computed(() => assets.value.filter((asset) => asset.status === 'ready').length)
const unresolvedShotCount = computed(() => shots.value?.shots.reduce((count, shot) => count + shot.unresolved_asset_requirements.length, 0) ?? 0)
const warningShotCount = computed(() => shots.value?.shots.reduce((count, shot) => count + shot.asset_binding_warnings.length, 0) ?? 0)
const canSubmitReview = computed(() => Boolean(selectedAsset.value && reviewer.value.trim() && reviewStatus.value !== selectedAsset.value.status && !submittingReview.value))
const reviewableAssetCount = computed(() => assets.value.filter((asset) => asset.status === 'needs_review').length)
const canSubmitBatchReview = computed(() => Boolean(projectId.value && reviewer.value.trim() && reviewableAssetCount.value > 0 && !submittingBatchReview.value))
const shotsStale = computed(() => Boolean(scriptImpact.value?.requires_shot_regeneration || (script.value && shots.value && script.value.id !== shots.value.script_id)))
const assetDraftEntries = computed(() => Object.entries(assetDraftContent.value))
const impactActionLabels: Record<EpisodeScriptImpactAction, string> = {
  generate_shot_list: '生成分镜',
  regenerate_shot_list: '重新生成分镜',
  review_asset_bindings: '复核资产绑定',
}
const impactShotIndexes = computed(() => scriptImpact.value?.shots.filter((shot) => shot.requires_regeneration).map((shot) => shot.shot_index) ?? [])
const draftConflictPaths = computed(() => {
  const paths = draftConflictDetails.value?.conflict_paths
  return Array.isArray(paths) ? paths.filter((path): path is string => typeof path === 'string') : []
})
const draftMergeablePaths = computed(() => mergePreview.value?.mergeable_paths ?? [])
const draftMergeConflictPaths = computed(() => mergePreview.value?.conflict_paths ?? [])
const auditActionLabels: Record<AuditAction, string> = {
  script_draft_saved: '保存剧本草稿',
  script_draft_deleted: '删除剧本草稿',
  script_version_published: '发布剧本版本',
  asset_version_created: '保存资产版本',
  asset_review_created: '提交资产审核',
  project_member_added: '新增项目成员',
  project_member_updated: '更新成员信息',
  project_member_role_changed: '变更成员角色',
  project_member_removed: '移除项目成员',
}
const auditEntityLabels: Record<AuditLogRecord['entity_type'], string> = {
  episode_script: '剧本',
  episode_script_draft: '剧本草稿',
  asset: '资产',
  asset_review: '资产审核',
  project_member: '项目成员',
}

function displayError(error: unknown): string {
  if (error instanceof ApiClientError) return `${error.message} · ${error.code}`
  return '脚本与资产暂时无法读取，请检查 API 和当前项目数据。'
}

function isMissing(error: unknown, code: string): boolean {
  return error instanceof ApiClientError && error.code === code
}

function formatTime(value: string): string {
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
}

function formatValue(value: unknown): string {
  if (Array.isArray(value)) return value.length ? value.join('、') : '—'
  if (value === null || value === undefined || value === '') return '—'
  return String(value)
}

function formatAuditMetadata(metadata: Record<string, unknown>): string {
  const labels: Record<string, string> = {
    version: '版本',
    revision: '修订',
    base_script_version: '基线剧本',
    status: '状态',
    from_status: '原状态',
    to_status: '新状态',
    reviewer: '审核人',
    expected_version: '期望版本',
  }
  return Object.entries(metadata)
    .filter(([, value]) => value !== null && value !== undefined && value !== '')
    .map(([key, value]) => `${labels[key] || key}：${formatValue(value)}`)
    .join(' · ')
}

function inputValue(event: Event): string {
  return (event.target as HTMLInputElement | HTMLTextAreaElement).value
}

function statusLabel(status: AssetStatus): string {
  return assetStatusLabels[status]
}

function resetAssetSelection() {
  selectedAssetId.value = filteredAssets.value[0]?.id ?? ''
}

function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T
}

function createDialogue(lineIndex: number): DialogueLine {
  return {
    line_index: lineIndex,
    speaker: '待设定角色',
    text: '待补充对白',
    emotion: '平静',
    delivery_notes: '',
  }
}

function createScene(sceneIndex: number, sourceChapterNumbers: number[]): SceneScriptContent {
  return {
    scene_index: sceneIndex,
    title: `新增场景 ${sceneIndex}`,
    location: '待设定地点',
    time: '待设定时间',
    characters: ['待设定角色'],
    duration_seconds: 10,
    action: '待补充可表演动作。',
    narration: '',
    dialogues: [createDialogue(1)],
    emotion: '待设定情绪',
    source_chapter_numbers: sourceChapterNumbers.length ? clone(sourceChapterNumbers) : [1],
  }
}

function normalizeScriptStructure() {
  if (!scriptDraft.value) return
  scriptDraft.value.scenes.forEach((scene, scenePosition) => {
    scene.scene_index = scenePosition + 1
    scene.dialogues.forEach((dialogue, dialoguePosition) => {
      dialogue.line_index = dialoguePosition + 1
    })
  })
  scriptDraft.value.total_duration_seconds = scriptDraft.value.scenes.reduce(
    (total, scene) => total + Number(scene.duration_seconds || 0),
    0,
  )
}

function syncScriptDuration() {
  normalizeScriptStructure()
}

function addScene() {
  if (!scriptDraft.value) return
  const currentDuration = scriptDraft.value.scenes.reduce((total, scene) => total + scene.duration_seconds, 0)
  if (scriptDraft.value.scenes.length >= 100 || currentDuration >= 600) {
    errorMessage.value = '场景数量最多 100 个，且总时长不能超过 600 秒。'
    return
  }
  const sourceChapters = scriptDraft.value.scenes.at(-1)?.source_chapter_numbers ?? [1]
  const sceneDuration = Math.min(10, 600 - currentDuration)
  scriptDraft.value.scenes.push(createScene(scriptDraft.value.scenes.length + 1, sourceChapters))
  scriptDraft.value.scenes.at(-1)!.duration_seconds = sceneDuration
  normalizeScriptStructure()
  errorMessage.value = null
}

function removeScene(scenePosition: number) {
  if (!scriptDraft.value) return
  if (scriptDraft.value.scenes.length <= 1) {
    errorMessage.value = '剧本至少需要保留一个场景。'
    return
  }
  const remainingDuration = scriptDraft.value.scenes.reduce(
    (total, scene, index) => total + (index === scenePosition ? 0 : scene.duration_seconds),
    0,
  )
  if (remainingDuration < 30) {
    errorMessage.value = '删除后总时长会低于 30 秒，请先调整剩余场景时长。'
    return
  }
  scriptDraft.value.scenes.splice(scenePosition, 1)
  normalizeScriptStructure()
  errorMessage.value = null
}

function addDialogue(scene: SceneScriptContent) {
  if (scene.dialogues.length >= 30) {
    errorMessage.value = '单个场景最多 30 条对白。'
    return
  }
  scene.dialogues.push(createDialogue(scene.dialogues.length + 1))
  normalizeScriptStructure()
  errorMessage.value = null
}

function removeDialogue(scene: SceneScriptContent, dialoguePosition: number) {
  scene.dialogues.splice(dialoguePosition, 1)
  normalizeScriptStructure()
  errorMessage.value = null
}

function draftFieldLabel(path: string): string {
  return path
    .replace(/^/, '内容.')
    .replace(/scenes\[(\d+)\]/g, '场景 $1')
    .replace(/dialogues\[(\d+)\]/g, '对白 $1')
    .replace(/\.(\w+)/g, ' · $1')
    .replace(/scenes$/, '场景列表')
    .replace(/title$/, '标题')
    .replace(/logline$/, '梗概')
    .replace(/opening_hook$/, '开场钩子')
    .replace(/ending_hook$/, '结尾钩子')
    .replace(/action$/, '动作')
    .replace(/text$/, '对白文本')
}

function beginScriptEdit() {
  if (!script.value) return
  scriptDraft.value = clone(recoverableDraft.value?.content ?? script.value.content)
  draftRevision.value = recoverableDraft.value?.revision ?? 0
  draftDirty.value = false
  draftConflict.value = false
  draftConflictDetails.value = null
  mergePreview.value = null
  editingScript.value = true
  successMessage.value = null
}

function cancelScriptEdit() {
  if (draftSaveTimer) clearTimeout(draftSaveTimer)
  editingScript.value = false
  scriptDraft.value = null
  draftDirty.value = false
  draftConflictDetails.value = null
  mergePreview.value = null
}

function scheduleDraftSave() {
  if (draftSaveTimer) clearTimeout(draftSaveTimer)
  draftSaveTimer = setTimeout(() => { void autoSaveScriptDraft() }, 800)
}

async function autoSaveScriptDraft() {
  if (!episodeId.value || !scriptDraft.value || !editingScript.value || savingDraft.value || draftConflict.value) return
  savingDraft.value = true
  try {
    const saved = await saveEpisodeScriptDraft(episodeId.value, {
      expected_revision: draftRevision.value,
      content: clone(scriptDraft.value),
    })
    recoverableDraft.value = saved
    draftRevision.value = saved.revision
    draftDirty.value = false
    mergePreview.value = null
    void loadAuditLogs()
  } catch (error) {
    draftConflict.value = true
    draftConflictDetails.value = error instanceof ApiClientError ? error.details : null
    errorMessage.value = displayError(error)
  } finally {
    savingDraft.value = false
  }
}

async function reloadLatestDraft() {
  if (!episodeId.value) return
  try {
    const latest = await getEpisodeScriptDraft(episodeId.value)
    if (script.value && latest.base_script_id !== script.value.id) {
      errorMessage.value = '服务端草稿基于旧正式剧本，请取消编辑并重新读取当前分集。'
      return
    }
    recoverableDraft.value = latest
    scriptDraft.value = clone(latest.content)
    draftRevision.value = latest.revision
    draftDirty.value = false
    draftConflict.value = false
    draftConflictDetails.value = null
    mergePreview.value = null
    errorMessage.value = null
    successMessage.value = `已载入服务端最新草稿 r${latest.revision}。`
  } catch (error) {
    errorMessage.value = displayError(error)
  }
}

async function previewDraftMerge() {
  if (!episodeId.value || !script.value || !scriptDraft.value || loadingMergePreview.value) return
  loadingMergePreview.value = true
  errorMessage.value = null
  try {
    mergePreview.value = await previewEpisodeScriptDraftMerge(episodeId.value, {
      base_script_id: script.value.id,
      base_script_version: script.value.version,
      content: clone(scriptDraft.value),
    })
  } catch (error) {
    errorMessage.value = displayError(error)
  } finally {
    loadingMergePreview.value = false
  }
}

function applyDraftMergePreview() {
  const preview = mergePreview.value
  if (!preview?.safe_to_apply || !preview.merged_content || !scriptDraft.value) return
  scriptDraft.value = clone(preview.merged_content)
  draftRevision.value = preview.current_revision
  draftConflict.value = false
  draftConflictDetails.value = null
  draftDirty.value = true
  mergePreview.value = null
  errorMessage.value = null
  successMessage.value = '安全合并结果已应用到编辑器，将按服务端最新草稿版本继续自动保存。'
  scheduleDraftSave()
}

async function saveScript() {
  if (!script.value || !scriptDraft.value || !episodeId.value || savingScript.value) return
  savingScript.value = true
  errorMessage.value = null
  successMessage.value = null
  try {
    const saved = await saveEpisodeScriptVersion(episodeId.value, {
      expected_version: script.value.version,
      content: scriptDraft.value,
    })
    script.value = saved
    editingScript.value = false
    scriptDraft.value = null
    recoverableDraft.value = null
    draftRevision.value = 0
    draftDirty.value = false
    draftConflict.value = false
    draftConflictDetails.value = null
    mergePreview.value = null
    await loadScriptImpact()
    await loadAuditLogs()
    successMessage.value = `剧本已保存为 v${saved.version}。已有分镜若基于旧剧本版本，需要重新生成。`
  } catch (error) {
    errorMessage.value = displayError(error)
  } finally {
    savingScript.value = false
  }
}

function beginAssetEdit() {
  if (!selectedAsset.value) return
  assetDraftContent.value = clone(selectedAsset.value.content)
  assetDraftAliases.value = selectedAsset.value.aliases.join('、')
  assetDraftSourceChapters.value = selectedAsset.value.source_chapter_numbers.join('、')
  assetDraftStatus.value = selectedAsset.value.status === 'ready' ? 'needs_review' : selectedAsset.value.status
  editingAsset.value = true
  successMessage.value = null
}

function cancelAssetEdit() {
  editingAsset.value = false
  assetDraftContent.value = {}
}

function assetDraftValue(key: string): string {
  const value = assetDraftContent.value[key]
  return Array.isArray(value) ? value.join('、') : value === undefined || value === null ? '' : String(value)
}

function updateAssetDraftValue(key: string, value: string) {
  const arrayKeys = new Set(['traits', 'relationships', 'visual_keywords'])
  assetDraftContent.value[key] = arrayKeys.has(key)
    ? value.split(/[、,，]/).map((item) => item.trim()).filter(Boolean)
    : value
}

async function saveAsset() {
  if (!selectedAsset.value || savingAsset.value) return
  savingAsset.value = true
  errorMessage.value = null
  successMessage.value = null
  try {
    const sourceChapters = assetDraftSourceChapters.value
      .split(/[、,，\s]+/)
      .map((item) => Number(item))
      .filter((item) => Number.isInteger(item) && item > 0)
    const saved = await saveAssetVersion(selectedAsset.value.id, {
      expected_version: selectedAsset.value.version,
      status: assetDraftStatus.value,
      content: clone(assetDraftContent.value),
      aliases: assetDraftAliases.value.split(/[、,，]/).map((item) => item.trim()).filter(Boolean),
      source_chapter_numbers: sourceChapters,
    })
    assets.value = assets.value.map((asset) => asset.asset_key === saved.asset_key ? saved : asset)
    selectedAssetId.value = saved.id
    editingAsset.value = false
    assetDraftContent.value = {}
    successMessage.value = `资产“${saved.name}”已保存为 v${saved.version}，当前状态为${statusLabel(saved.status)}。`
    await loadReviews()
    await loadAuditLogs()
  } catch (error) {
    errorMessage.value = displayError(error)
  } finally {
    savingAsset.value = false
  }
}

async function loadProjects() {
  loadingProjects.value = true
  errorMessage.value = null
  try {
    projects.value = await getNovelProjects()
    if (!projectId.value && projects.value.length) projectId.value = projects.value[0].id
  } catch (error) {
    errorMessage.value = displayError(error)
  } finally {
    loadingProjects.value = false
  }
}

function resetMemberForm() {
  memberActorId.value = ''
  memberActorName.value = ''
  memberRole.value = 'viewer'
  editingMemberActorId.value = null
}

function resetInvitationForm() {
  invitationActorId.value = ''
  invitationActorName.value = ''
  invitationRole.value = 'viewer'
  invitationExpiresInDays.value = 7
}

function editMember(member: ProjectMemberRecord) {
  editingMemberActorId.value = member.actor_id
  memberActorId.value = member.actor_id
  memberActorName.value = member.actor_name
  memberRole.value = member.role
}

async function loadProjectMembers() {
  projectMembers.value = []
  if (!projectId.value) return
  loadingMembers.value = true
  try {
    projectMembers.value = (await getProjectMembers(projectId.value)).items
  } catch (error) {
    errorMessage.value = displayError(error)
  } finally {
    loadingMembers.value = false
  }
}

async function loadProjectInvitations() {
  projectInvitations.value = []
  if (!projectId.value || !canManageMembers.value) return
  loadingInvitations.value = true
  try {
    projectInvitations.value = (await getProjectInvitations(projectId.value)).items
  } catch (error) {
    errorMessage.value = displayError(error)
  } finally {
    loadingInvitations.value = false
  }
}

async function saveMember() {
  if (!projectId.value || !memberActorId.value.trim() || !memberActorName.value.trim()) return
  savingMember.value = true
  errorMessage.value = null
  successMessage.value = null
  try {
    const saved = await upsertProjectMember(projectId.value, memberActorId.value.trim(), {
      actor_name: memberActorName.value.trim(),
      role: memberRole.value,
    })
    projectMembers.value = [...projectMembers.value.filter((item) => item.actor_id !== saved.actor_id), saved]
      .sort((left, right) => left.actor_id.localeCompare(right.actor_id))
    successMessage.value = `成员“${saved.actor_name}”已保存为${roleLabels[saved.role]}。`
    resetMemberForm()
    await loadAuditLogs()
  } catch (error) {
    errorMessage.value = displayError(error)
  } finally {
    savingMember.value = false
  }
}

async function removeMember(member: ProjectMemberRecord) {
  if (!projectId.value || !canManageMembers.value || !window.confirm(`确定移除成员“${member.actor_name}”吗？`)) return
  removingMemberId.value = member.id
  errorMessage.value = null
  successMessage.value = null
  try {
    await removeProjectMember(projectId.value, member.actor_id)
    projectMembers.value = projectMembers.value.filter((item) => item.actor_id !== member.actor_id)
    if (editingMemberActorId.value === member.actor_id) resetMemberForm()
    successMessage.value = `成员“${member.actor_name}”已移除。`
    await loadAuditLogs()
  } catch (error) {
    errorMessage.value = displayError(error)
  } finally {
    removingMemberId.value = null
  }
}

async function createInvitation() {
  if (!projectId.value || !canManageMembers.value || !invitationActorId.value.trim() || !invitationActorName.value.trim()) return
  creatingInvitation.value = true
  errorMessage.value = null
  successMessage.value = null
  try {
    const result = await createProjectInvitation(projectId.value, {
      invitee_actor_id: invitationActorId.value.trim(),
      invitee_name: invitationActorName.value.trim(),
      role: invitationRole.value,
      expires_in_seconds: invitationExpiresInDays.value * 24 * 60 * 60,
    })
    projectInvitations.value = [result.invitation, ...projectInvitations.value.filter((item) => item.id !== result.invitation.id)]
    lastInvitationToken.value = result.accept_token
    successMessage.value = `已为“${result.invitation.invitee_name}”创建邀请。请在本页面复制一次性 token 交给对方。`
    resetInvitationForm()
    await loadAuditLogs()
  } catch (error) {
    errorMessage.value = displayError(error)
  } finally {
    creatingInvitation.value = false
  }
}

async function revokeInvitation(invitation: ProjectInvitationSummary) {
  if (!projectId.value || !canManageMembers.value || invitation.status !== 'pending' || !window.confirm(`确定撤销发给“${invitation.invitee_name}”的邀请吗？`)) return
  revokingInvitationId.value = invitation.id
  errorMessage.value = null
  successMessage.value = null
  try {
    await revokeProjectInvitation(projectId.value, invitation.id)
    projectInvitations.value = projectInvitations.value.map((item) => item.id === invitation.id ? { ...item, status: 'revoked', revoked_at: new Date().toISOString(), updated_at: new Date().toISOString() } : item)
    successMessage.value = `发给“${invitation.invitee_name}”的邀请已撤销。`
    await loadAuditLogs()
  } catch (error) {
    errorMessage.value = displayError(error)
  } finally {
    revokingInvitationId.value = null
  }
}

async function copyInvitationToken() {
  if (!lastInvitationToken.value) return
  await navigator.clipboard.writeText(lastInvitationToken.value)
  successMessage.value = '邀请 token 已复制。它只在创建响应中返回，接受后立即失效。'
}

async function loadProjectResources() {
  episodes.value = []
  assets.value = []
  projectAccess.value = null
  script.value = null
  shots.value = null
  scriptImpact.value = null
  episodeId.value = ''
  selectedAssetId.value = ''
  auditLogs.value = []
  projectMembers.value = []
  projectInvitations.value = []
  lastInvitationToken.value = ''
  resetMemberForm()
  resetInvitationForm()
  if (!projectId.value) return

  loadingResources.value = true
  errorMessage.value = null
  try {
    const [accessResult, episodeItems, assetItems, auditResult, memberResult] = await Promise.all([
      getProjectAccess(projectId.value),
      getEpisodes(projectId.value),
      getAssets(projectId.value),
      getAuditLogs(projectId.value),
      getProjectMembers(projectId.value),
    ])
    projectAccess.value = accessResult
    episodes.value = episodeItems
    assets.value = assetItems
    auditLogs.value = auditResult.items
    projectMembers.value = memberResult.items
    if (accessResult.permissions.includes('project:manage_members')) {
      projectInvitations.value = (await getProjectInvitations(projectId.value)).items
    }
    episodeId.value = episodeItems[0]?.id ?? ''
    selectedAssetId.value = assetItems[0]?.id ?? ''
    if (!episodeItems.length && !assetItems.length) errorMessage.value = '当前项目还没有分集或资产，请先完成 StoryBible、分集和资产同步。'
  } catch (error) {
    errorMessage.value = displayError(error)
  } finally {
    loadingResources.value = false
  }
}

async function loadAuditLogs() {
  if (!projectId.value) {
    auditLogs.value = []
    return
  }
  loadingAuditLogs.value = true
  try {
    auditLogs.value = (await getAuditLogs(projectId.value)).items
  } catch (error) {
    errorMessage.value = displayError(error)
  } finally {
    loadingAuditLogs.value = false
  }
}

async function loadEpisodeDocuments() {
  script.value = null
  shots.value = null
  scriptImpact.value = null
  recoverableDraft.value = null
  draftRevision.value = 0
  draftDirty.value = false
  draftConflict.value = false
  draftConflictDetails.value = null
  mergePreview.value = null
  if (!episodeId.value) return

  loadingEpisode.value = true
  errorMessage.value = null
  const [scriptResult, shotsResult, impactResult, draftResult] = await Promise.allSettled([
    getEpisodeScript(episodeId.value),
    getEpisodeShots(episodeId.value),
    getEpisodeScriptImpact(episodeId.value),
    getEpisodeScriptDraft(episodeId.value),
  ])
  if (scriptResult.status === 'fulfilled') script.value = scriptResult.value
  else if (!isMissing(scriptResult.reason, 'EPISODE_SCRIPT_NOT_FOUND')) errorMessage.value = displayError(scriptResult.reason)
  if (shotsResult.status === 'fulfilled') shots.value = shotsResult.value
  else if (!isMissing(shotsResult.reason, 'SHOT_LIST_NOT_FOUND')) errorMessage.value = displayError(shotsResult.reason)
  if (impactResult.status === 'fulfilled') scriptImpact.value = impactResult.value
  else if (!isMissing(impactResult.reason, 'EPISODE_SCRIPT_NOT_FOUND')) errorMessage.value = displayError(impactResult.reason)
  if (draftResult.status === 'fulfilled' && scriptResult.status === 'fulfilled') {
    recoverableDraft.value = draftResult.value.base_script_id === scriptResult.value.id ? draftResult.value : null
  } else if (draftResult.status === 'rejected' && !isMissing(draftResult.reason, 'EPISODE_SCRIPT_DRAFT_NOT_FOUND')) {
    errorMessage.value = displayError(draftResult.reason)
  }
  loadingEpisode.value = false
}

async function loadScriptImpact() {
  if (!episodeId.value) return
  try {
    scriptImpact.value = await getEpisodeScriptImpact(episodeId.value)
  } catch (error) {
    if (!isMissing(error, 'EPISODE_SCRIPT_NOT_FOUND')) errorMessage.value = displayError(error)
  }
}

async function loadReviews() {
  reviews.value = []
  if (!selectedAssetId.value) return
  loadingReviews.value = true
  try {
    reviews.value = await getAssetReviews(selectedAssetId.value)
  } catch (error) {
    errorMessage.value = displayError(error)
  } finally {
    loadingReviews.value = false
  }
}

async function submitReview() {
  if (!selectedAsset.value || !canSubmitReview.value) return
  submittingReview.value = true
  errorMessage.value = null
  successMessage.value = null
  try {
    const result = await reviewAsset(selectedAsset.value.id, {
      status: reviewStatus.value,
      reviewer: reviewer.value.trim(),
      comment: reviewComment.value.trim(),
    })
    assets.value = assets.value.map((asset) => asset.asset_key === result.asset.asset_key ? result.asset : asset)
    selectedAssetId.value = result.asset.id
    reviewComment.value = ''
    successMessage.value = `资产“${result.asset.name}”已创建 v${result.asset.version} 审核版本，状态为${statusLabel(result.asset.status)}。`
    await loadReviews()
    await loadAuditLogs()
  } catch (error) {
    errorMessage.value = displayError(error)
  } finally {
    submittingReview.value = false
  }
}

async function submitBatchReview() {
  if (!canSubmitBatchReview.value) return
  submittingBatchReview.value = true
  errorMessage.value = null
  successMessage.value = null
  try {
    const results = await reviewProjectAssets(projectId.value, {
      status: 'ready',
      reviewer: reviewer.value.trim(),
      comment: reviewComment.value.trim() || '批量审核通过',
    })
    assets.value = assets.value.map((asset) => {
      const result = results.find((item) => item.asset.asset_key === asset.asset_key)
      return result?.asset ?? asset
    })
    reviewComment.value = ''
    successMessage.value = results.length ? `已批量审核 ${results.length} 个资产为已就绪。` : '没有可批量审核的待审核资产。'
    await loadReviews()
    await loadAuditLogs()
    await loadEpisodeDocuments()
  } catch (error) {
    errorMessage.value = displayError(error)
  } finally {
    submittingBatchReview.value = false
  }
}

watch(projectId, loadProjectResources)
watch(episodeId, loadEpisodeDocuments)
watch([assetTypeFilter, assetStatusFilter], resetAssetSelection)
watch(projectId, () => { editingScript.value = false; editingAsset.value = false })
watch(episodeId, () => { editingScript.value = false; scriptDraft.value = null; draftRevision.value = 0; recoverableDraft.value = null; draftConflictDetails.value = null; mergePreview.value = null })
watch(scriptDraft, () => {
  if (!editingScript.value || !scriptDraft.value || savingDraft.value) return
  const baseline = recoverableDraft.value?.content ?? script.value?.content
  if (baseline && JSON.stringify(baseline) === JSON.stringify(scriptDraft.value)) {
    draftDirty.value = false
    return
  }
  draftDirty.value = true
  scheduleDraftSave()
}, { deep: true })
watch(selectedAsset, (asset) => {
  const nextStatus = asset ? reviewTransitions[asset.status][0] : 'ready'
  reviewStatus.value = nextStatus ?? 'ready'
  if (!asset) editingAsset.value = false
})
watch(selectedAssetId, () => { editingAsset.value = false; loadReviews() })

onMounted(loadProjects)
</script>

<template>
  <section class="page-header">
    <div>
      <p class="page-kicker">NOVEL PRODUCTION / WORKBENCH</p>
      <h1>脚本与资产</h1>
      <p>在生成视频前查看分集剧本、分镜资产绑定和角色 / 场景 / 道具审核状态。</p>
    </div>
    <span class="workspace-badge">人工审核节点</span>
  </section>

  <section class="card workbench-toolbar">
    <label class="form-field"><span>小说项目</span><select v-model="projectId" :disabled="loadingProjects || loadingResources"><option value="">请选择项目</option><option v-for="project in projects" :key="project.id" :value="project.id">{{ project.title }}</option></select></label>
    <label class="form-field"><span>目标分集</span><select v-model="episodeId" :disabled="loadingResources || episodes.length === 0"><option value="">请选择分集</option><option v-for="episode in episodes" :key="episode.id" :value="episode.id">第 {{ episode.episode_number }} 集 · {{ episode.outline.title }}</option></select></label>
    <div class="workbench-toolbar-summary"><strong>{{ selectedProject?.title || '未选择项目' }}</strong><span>{{ assets.length }} 个资产 · {{ readyAssetCount }} 个已就绪</span><span v-if="projectAccess" class="access-badge">{{ roleLabels[projectAccess.role] }} · {{ projectAccess.actor_name || projectAccess.actor_id }}</span></div>
  </section>

  <div v-if="errorMessage" class="alert-card error-card"><div><strong>工作台读取失败</strong><p>{{ errorMessage }}</p></div><button class="secondary-button" type="button" @click="loadProjectResources">重试</button></div>
  <div v-if="successMessage" class="alert-card success-card"><div><strong>审核已保存</strong><p>{{ successMessage }}</p></div><button class="secondary-button" type="button" @click="successMessage = null">知道了</button></div>

  <section class="workbench-summary-grid" aria-label="脚本与资产摘要">
    <article class="summary-card"><div class="summary-icon blue"><span>剧</span></div><div><span>当前剧本</span><strong>{{ script ? `v${script.version}` : '未生成' }}</strong><small>{{ script ? `${script.content.scenes.length} 个场景 · ${script.content.total_duration_seconds} 秒` : '先生成分场剧本' }}</small></div><span class="summary-state" :class="{ good: script }">{{ script ? '可查看' : '待处理' }}</span></article>
    <article class="summary-card"><div class="summary-icon purple"><span>镜</span></div><div><span>当前分镜</span><strong>{{ shots ? `v${shots.version}` : '未生成' }}</strong><small>{{ shots ? `${shots.shots.length} 个镜头` : '先生成分镜 JSON' }}</small></div><span class="summary-state" :class="{ good: shots }">{{ shots ? '可检查' : '待处理' }}</span></article>
    <article class="summary-card"><div class="summary-icon green"><span>审</span></div><div><span>资产门禁</span><strong>{{ readyAssetCount }} / {{ assets.length || 0 }}</strong><small>只有 ready 资产可绑定到视频镜头</small></div><span class="summary-state" :class="{ good: assets.length > 0 && readyAssetCount === assets.length }">{{ assets.length > 0 && readyAssetCount === assets.length ? '已通过' : '需审核' }}</span></article>
  </section>

  <details class="card audit-card">
    <summary><div><span class="page-kicker">AUDIT TRAIL</span><strong>操作记录</strong><small>记录脚本草稿、剧本版本、资产版本和审核状态变更</small></div><span class="table-count">{{ loadingAuditLogs ? '读取中…' : `${auditLogs.length} 条` }}</span></summary>
    <div class="audit-disclaimer">当前操作人来自后端身份适配器；生产模式要求可信网关签名，编辑与审核按钮会根据项目角色自动收敛。</div>
    <div v-if="auditLogs.length === 0" class="history-empty">当前项目暂无编辑操作记录。</div>
    <div v-for="log in auditLogs" :key="log.id" class="audit-row"><span class="audit-dot" /><div><div class="audit-row-heading"><strong>{{ auditActionLabels[log.action] }}</strong><span>{{ auditEntityLabels[log.entity_type] }}</span></div><p>{{ formatAuditMetadata(log.metadata) || '无附加摘要' }}</p><small>{{ log.actor_name }} · {{ log.actor_id }} · {{ formatTime(log.created_at) }}</small></div></div>
  </details>

  <section v-if="projectId" class="card members-card">
    <div class="members-header"><div><span class="page-kicker">PROJECT ACCESS</span><h2>项目成员</h2><p>成员角色控制脚本、资产和审核操作；所有新增、变更和移除都会写入项目审计记录。</p></div><span class="table-count">{{ loadingMembers ? '读取中…' : `${projectMembers.length} 人` }}</span></div>
    <div v-if="projectMembers.length === 0 && !loadingMembers" class="history-empty">当前项目暂无成员记录。</div>
    <div v-else class="members-list"><div v-for="member in projectMembers" :key="member.id" class="member-row"><div class="member-avatar">{{ member.actor_name.slice(0, 1).toUpperCase() }}</div><div class="member-copy"><strong>{{ member.actor_name }}</strong><small>{{ member.actor_id }} · 更新于 {{ formatTime(member.updated_at) }}</small></div><span class="member-role-pill" :class="member.role">{{ roleLabels[member.role] }}</span><span class="member-role-description">{{ roleDescriptions[member.role] }}</span><div v-if="canManageMembers" class="member-actions"><button class="secondary-button compact-button" type="button" @click="editMember(member)">编辑</button><button class="danger-button compact-button" type="button" :disabled="removingMemberId === member.id" @click="removeMember(member)">{{ removingMemberId === member.id ? '移除中…' : '移除' }}</button></div></div></div>
    <div v-if="canManageMembers" class="member-form"><div class="member-form-heading"><div><strong>{{ editingMemberActorId ? '编辑成员' : '添加成员' }}</strong><span>owner 才能修改成员；不能移除或降级项目最后一个 owner。</span></div><button v-if="editingMemberActorId" class="secondary-button compact-button" type="button" @click="resetMemberForm">取消编辑</button></div><form class="member-form-grid" @submit.prevent="saveMember"><label class="form-field"><span>Actor ID</span><input v-model="memberActorId" :disabled="Boolean(editingMemberActorId)" maxlength="120" placeholder="例如 editor-01" required /></label><label class="form-field"><span>显示名称</span><input v-model="memberActorName" maxlength="120" placeholder="例如 内容编辑" required /></label><label class="form-field"><span>角色</span><select v-model="memberRole"><option v-for="role in projectRoles" :key="role" :value="role">{{ roleLabels[role] }} · {{ roleDescriptions[role] }}</option></select></label><button class="primary-button member-save-button" type="submit" :disabled="savingMember || !memberActorId.trim() || !memberActorName.trim()">{{ savingMember ? '保存中…' : editingMemberActorId ? '保存成员' : '添加成员' }}</button></form></div>
    <div v-else class="members-readonly-note">当前角色为{{ projectAccess ? roleLabels[projectAccess.role] : '只读成员' }}，仅 owner 可以管理成员。</div>
    <div v-if="canManageMembers" class="invitation-panel"><div class="member-form-heading"><div><strong>邀请新成员</strong><span>邀请不会自动发送邮件；token 只在创建响应中返回一次，接受后立即失效。</span></div><span class="table-count">{{ loadingInvitations ? '读取中…' : `${projectInvitations.length} 条邀请` }}</span></div><form class="invitation-form-grid" @submit.prevent="createInvitation"><label class="form-field"><span>受邀 Actor ID</span><input v-model="invitationActorId" maxlength="120" placeholder="例如 reviewer-01" required /></label><label class="form-field"><span>显示名称</span><input v-model="invitationActorName" maxlength="120" placeholder="例如 内容审核" required /></label><label class="form-field"><span>角色</span><select v-model="invitationRole"><option v-for="role in projectRoles" :key="role" :value="role">{{ roleLabels[role] }}</option></select></label><label class="form-field"><span>有效期</span><select v-model.number="invitationExpiresInDays"><option :value="1">1 天</option><option :value="7">7 天</option><option :value="30">30 天</option></select></label><button class="primary-button invitation-submit-button" type="submit" :disabled="creatingInvitation || !invitationActorId.trim() || !invitationActorName.trim()">{{ creatingInvitation ? '创建中…' : '创建邀请' }}</button></form><div v-if="lastInvitationToken" class="invitation-token-box"><div><strong>请立即复制一次性 token</strong><span>服务端只保存哈希，刷新页面后不会再次显示此 token。</span></div><code>{{ lastInvitationToken }}</code><button class="secondary-button compact-button" type="button" @click="copyInvitationToken">复制 token</button></div><div v-if="projectInvitations.length" class="invitation-list"><div v-for="invitation in projectInvitations" :key="invitation.id" class="invitation-row"><div class="member-copy"><strong>{{ invitation.invitee_name }} · {{ roleLabels[invitation.role] }}</strong><small>{{ invitation.invitee_actor_id }} · {{ formatTime(invitation.created_at) }} 创建 · {{ formatTime(invitation.expires_at) }} 到期</small></div><span class="invitation-status" :class="invitation.status">{{ invitationStatusLabels[invitation.status] }}</span><button v-if="invitation.status === 'pending'" class="danger-button compact-button" type="button" :disabled="revokingInvitationId === invitation.id" @click="revokeInvitation(invitation)">{{ revokingInvitationId === invitation.id ? '撤销中…' : '撤销' }}</button></div></div><div v-else-if="!loadingInvitations" class="history-empty">当前没有邀请记录。</div></div>
  </section>

  <section class="workbench-main-grid">
    <article class="card script-panel">
      <div class="card-header"><div><h2>内容检查</h2><p>{{ selectedEpisode ? `第 ${selectedEpisode.episode_number} 集 · ${selectedEpisode.outline.title}` : '选择一个项目和分集开始检查' }}</p></div><div class="card-header-actions"><span v-if="recoverableDraft && !editingScript" class="draft-status">有未发布草稿 · r{{ recoverableDraft.revision }}</span><button v-if="script && contentTab === 'script' && !editingScript && canEditScript" class="secondary-button compact-button" type="button" @click="beginScriptEdit">编辑剧本</button><button v-if="editingScript" class="secondary-button compact-button" type="button" @click="cancelScriptEdit">取消编辑</button><span class="table-count">{{ script?.content.scenes.length || 0 }} 场 / {{ shots?.shots.length || 0 }} 镜</span></div></div>
      <div class="mode-switch" role="tablist" aria-label="内容视图">
        <button type="button" :class="{ active: contentTab === 'script' }" @click="contentTab = 'script'">分场剧本</button>
        <button type="button" :class="{ active: contentTab === 'shots' }" @click="contentTab = 'shots'">分镜与绑定</button>
      </div>
      <div v-if="shotsStale" class="stale-warning"><strong>分镜版本已过期</strong><span>当前剧本 v{{ script?.version }} 已保存，分镜仍基于旧剧本版本；请重新生成分镜后再创建视频任务。</span></div>
      <div v-if="scriptImpact" class="impact-card" :class="{ blocked: scriptImpact.video_generation_gate === 'blocked' }"><div class="impact-card-heading"><div><span class="page-kicker">DOWNSTREAM IMPACT</span><strong>{{ scriptImpact.shot_list_state === 'missing' ? '尚未生成分镜' : scriptImpact.requires_shot_regeneration ? `需要处理 ${scriptImpact.affected_shot_count} 个镜头` : '当前分镜与剧本一致' }}</strong></div><span class="impact-state">视频门禁：{{ scriptImpact.video_generation_gate === 'blocked' ? '阻断' : '可继续' }}</span></div><div class="impact-metrics"><span>剧本 v{{ scriptImpact.script_version }}</span><span>分镜 {{ scriptImpact.shot_list_version ? `v${scriptImpact.shot_list_version}` : '—' }}</span><span>未解决需求 {{ scriptImpact.unresolved_asset_requirement_count }}</span><span>绑定警告 {{ scriptImpact.asset_binding_warning_count }}</span></div><div v-if="scriptImpact.recommended_actions.length" class="impact-actions"><strong>建议操作</strong><span v-for="action in scriptImpact.recommended_actions" :key="action" class="impact-action-tag">{{ impactActionLabels[action] }}</span></div><p v-if="impactShotIndexes.length" class="impact-footnote">受影响镜头：{{ impactShotIndexes.slice(0, 12).join('、') }}{{ impactShotIndexes.length > 12 ? ' 等' : '' }}</p></div>
      <div v-if="loadingEpisode" class="task-empty"><span class="spinner" />正在读取剧本和分镜…</div>
      <div v-else-if="contentTab === 'script' && script && editingScript && scriptDraft" class="script-editor">
        <div class="editor-note"><strong>版本化编辑</strong><span>编辑内容每 0.8 秒自动保存为草稿；点击“保存为新版本”才会发布，不覆盖当前剧本。</span><span class="draft-save-state">{{ savingDraft ? '正在自动保存…' : draftConflict ? '草稿冲突，需要重新载入' : draftDirty ? '有未保存修改' : '草稿已保存' }}</span></div>
        <div v-if="draftConflict" class="draft-conflict-panel"><strong>检测到字段级冲突</strong><span v-if="draftConflictPaths.length">重叠字段：{{ draftConflictPaths.map(draftFieldLabel).join('、') }}</span><span v-else>服务端草稿已更新，但没有足够信息判断重叠字段。</span><small>系统不会自动覆盖任一方内容。你可以生成只读合并预览、载入服务端最新草稿后继续编辑，或取消本次编辑。</small><div class="draft-conflict-actions"><button class="secondary-button compact-button" type="button" :disabled="loadingMergePreview" @click="previewDraftMerge">{{ loadingMergePreview ? '生成预览中…' : '生成安全合并预览' }}</button><button class="secondary-button compact-button" type="button" @click="reloadLatestDraft">载入最新草稿</button><button class="secondary-button compact-button" type="button" @click="cancelScriptEdit">取消编辑</button></div></div>
        <div v-if="mergePreview" class="draft-merge-preview" :class="{ conflict: !mergePreview.safe_to_apply }"><strong>{{ mergePreview.safe_to_apply ? '可以安全合并' : '仍存在不可自动合并的冲突' }}</strong><span v-if="draftMergeablePaths.length">可合并字段：{{ draftMergeablePaths.map(draftFieldLabel).join('、') }}</span><span v-if="draftMergeConflictPaths.length">冲突字段：{{ draftMergeConflictPaths.map(draftFieldLabel).join('、') }}</span><span v-if="!draftMergeablePaths.length && !draftMergeConflictPaths.length">没有检测到内容差异。</span><small>这是只读预览，不会修改服务端草稿。应用后会把合并内容放回编辑器，并继续按当前 revision 自动保存。</small><div class="draft-conflict-actions"><button v-if="mergePreview.safe_to_apply" class="primary-button compact-button" type="button" @click="applyDraftMergePreview">应用合并结果</button><button class="secondary-button compact-button" type="button" @click="mergePreview = null">关闭预览</button></div></div>
        <div class="editor-grid"><label class="form-field"><span>剧本标题</span><input v-model="scriptDraft.title" maxlength="120" /></label><label class="form-field"><span>总时长（秒）</span><input :value="scriptDraft.total_duration_seconds" type="number" min="30" max="600" readonly /><small class="field-hint">由场景时长自动汇总</small></label><label class="form-field form-field-wide"><span>一句话梗概</span><textarea v-model="scriptDraft.logline" rows="2" maxlength="500" /></label><label class="form-field"><span>开场钩子</span><textarea v-model="scriptDraft.opening_hook" rows="3" maxlength="500" /></label><label class="form-field"><span>结尾钩子</span><textarea v-model="scriptDraft.ending_hook" rows="3" maxlength="500" /></label></div>
        <div class="editor-section-heading"><div><strong>场景与对白</strong><span>{{ scriptDraft.scenes.length }} 个场景 · {{ scriptDraft.scenes.reduce((total, scene) => total + scene.dialogues.length, 0) }} 条对白</span></div><button class="structure-button" type="button" :disabled="scriptDraft.scenes.length >= 100" @click="addScene">＋ 添加场景</button></div>
        <article v-for="(scene, scenePosition) in scriptDraft.scenes" :key="scene.scene_index" class="scene-editor-card"><div class="scene-card-heading"><div><span class="scene-index">场景 {{ scene.scene_index }}</span><input v-model="scene.title" class="inline-editor-input scene-title-input" maxlength="120" /></div><div class="scene-editor-heading-actions"><label class="duration-editor"><span>秒</span><input v-model.number="scene.duration_seconds" type="number" min="1" max="300" @change="syncScriptDuration" /></label><button class="structure-button danger" type="button" :disabled="scriptDraft.scenes.length <= 1" @click="removeScene(scenePosition)">删除场景</button></div></div><div class="editor-grid scene-editor-grid"><label class="form-field"><span>地点</span><input v-model="scene.location" maxlength="120" /></label><label class="form-field"><span>时间</span><input v-model="scene.time" maxlength="80" /></label><label class="form-field form-field-wide"><span>角色（用、分隔）</span><input :value="scene.characters.join('、')" @input="scene.characters = inputValue($event).split(/[、,，]/).map((item) => item.trim()).filter(Boolean)" /></label><label class="form-field form-field-wide"><span>动作</span><textarea v-model="scene.action" rows="3" maxlength="1500" /></label><label class="form-field"><span>旁白</span><textarea v-model="scene.narration" rows="3" maxlength="1000" /></label><label class="form-field"><span>情绪</span><input v-model="scene.emotion" maxlength="120" /></label></div><div class="dialogue-editor-heading"><div><strong>对白</strong><span>{{ scene.dialogues.length }} 条</span></div><button class="structure-button" type="button" :disabled="scene.dialogues.length >= 30" @click="addDialogue(scene)">＋ 添加对白</button></div><div v-if="scene.dialogues.length === 0" class="dialogue-empty">当前场景为旁白 / 动作场景，没有对白。</div><div v-for="(dialogue, dialoguePosition) in scene.dialogues" :key="dialogue.line_index" class="dialogue-editor-row"><input v-model="dialogue.speaker" aria-label="对白角色" placeholder="角色" maxlength="80" /><textarea v-model="dialogue.text" aria-label="对白文本" rows="2" maxlength="1000" /><input v-model="dialogue.emotion" aria-label="对白情绪" placeholder="情绪" maxlength="120" /><button class="structure-button danger dialogue-delete-button" type="button" @click="removeDialogue(scene, dialoguePosition)">删除</button></div></article>
        <div class="editor-actions"><span class="field-hint">保存前会由后端重新校验字段、场景序号和总时长；草稿可在刷新后恢复。</span><button class="primary-button" type="button" :disabled="savingScript || savingDraft || draftConflict" @click="saveScript">{{ savingScript ? '保存中…' : '保存为新版本' }}</button></div>
      </div>
      <div v-else-if="contentTab === 'script' && script" class="script-document">
        <div class="script-title-row"><div><span class="page-kicker">EPISODE SCRIPT · VERSION {{ script.version }}</span><h3>{{ script.content.title }}</h3></div><span class="configured-tag">{{ script.provider }}</span></div>
        <p class="script-logline">{{ script.content.logline }}</p>
        <div class="hook-grid"><div><span>开场钩子</span><p>{{ script.content.opening_hook }}</p></div><div><span>结尾钩子</span><p>{{ script.content.ending_hook }}</p></div></div>
        <div class="scene-stack">
          <article v-for="scene in script.content.scenes" :key="scene.scene_index" class="scene-card"><div class="scene-card-heading"><div><span class="scene-index">场景 {{ scene.scene_index }}</span><h4>{{ scene.title }}</h4></div><span class="scene-duration">{{ scene.duration_seconds }}s</span></div><div class="scene-meta"><span>{{ scene.location }}</span><span>{{ scene.time }}</span><span>{{ scene.characters.join('、') }}</span></div><p class="scene-action">{{ scene.action }}</p><p v-if="scene.narration" class="scene-narration">旁白：{{ scene.narration }}</p><div class="dialogue-list"><div v-for="dialogue in scene.dialogues" :key="dialogue.line_index" class="dialogue-row"><strong>{{ dialogue.speaker }}</strong><span>{{ dialogue.text }}</span><small>{{ dialogue.emotion }}</small></div></div></article>
        </div>
      </div>
      <div v-else-if="contentTab === 'script'" class="task-empty"><strong>当前分集还没有剧本</strong><span>先通过剧本生成接口创建版本，工作台会自动读取最新版本。</span></div>
      <div v-else-if="shots" class="shot-stack">
        <div class="shot-health"><span :class="{ danger: unresolvedShotCount > 0 }">未解决需求 {{ unresolvedShotCount }}</span><span :class="{ warning: warningShotCount > 0 }">绑定警告 {{ warningShotCount }}</span><span>总时长 {{ shots.shots.reduce((sum, shot) => sum + shot.duration_seconds, 0) }} 秒</span></div>
        <article v-for="shot in shots.shots" :key="shot.shot_index" class="shot-card"><div class="shot-card-heading"><div><span class="scene-index">镜头 {{ shot.shot_index }} · 场景 {{ shot.scene_index }}</span><h4>{{ shot.shot_size }} · {{ shot.camera_movement }}</h4></div><span class="scene-duration">{{ shot.duration_seconds }}s</span></div><p class="shot-prompt">{{ shot.visual_prompt }}</p><div class="shot-tags"><span v-for="asset in shot.asset_refs" :key="`${asset.asset_key}-${asset.version}`" class="asset-ref-tag" :class="asset.status">{{ asset.name }} · v{{ asset.version }}</span><span v-for="name in shot.unresolved_asset_requirements" :key="name" class="asset-ref-tag unresolved">缺失：{{ name }}</span><span v-for="warning in shot.asset_binding_warnings" :key="warning" class="asset-ref-tag warning">警告：{{ warning }}</span></div><small class="shot-footnote">角色：{{ shot.characters.join('、') }} · 场景：{{ shot.location }} · 音频：{{ shot.audio_requirements.join('、') }}</small></article>
      </div>
      <div v-else class="task-empty"><strong>当前分集还没有分镜</strong><span>先生成分镜 JSON，再回来检查资产绑定和审核门禁。</span></div>
    </article>

    <article class="card asset-panel">
      <div class="card-header"><div><h2>资产审核</h2><p>版本化管理角色、场景和道具，审核不会覆盖历史版本。</p></div><div class="card-header-actions"><span class="table-count">{{ filteredAssets.length }} 个结果</span><button v-if="canReviewAsset" class="primary-button compact-button" type="button" :disabled="!canSubmitBatchReview" @click="submitBatchReview">{{ submittingBatchReview ? '批量审核中…' : `一键审核待审核资产（${reviewableAssetCount}）` }}</button></div></div>
      <div class="asset-filter-row"><select v-model="assetTypeFilter"><option v-for="(label, value) in assetTypeLabels" :key="value" :value="value">{{ label }}</option></select><select v-model="assetStatusFilter"><option v-for="(label, value) in assetStatusLabels" :key="value" :value="value">{{ label }}</option></select></div>
      <div v-if="loadingResources" class="task-empty"><span class="spinner" />正在读取资产库…</div>
      <div v-else-if="filteredAssets.length === 0" class="task-empty"><strong>暂无匹配资产</strong><span>请先同步 StoryBible 资产，或调整筛选条件。</span></div>
      <div v-else class="asset-workspace">
        <div class="asset-list"><button v-for="asset in filteredAssets" :key="asset.id" type="button" class="asset-list-row" :class="{ selected: selectedAssetId === asset.id }" @click="selectedAssetId = asset.id"><span class="asset-type-icon" :class="asset.asset_type">{{ asset.asset_type === 'character' ? '人' : asset.asset_type === 'location' ? '景' : '物' }}</span><span class="asset-list-copy"><strong>{{ asset.name }}</strong><small>{{ assetTypeLabels[asset.asset_type] }} · v{{ asset.version }}</small></span><span class="asset-status-dot" :class="asset.status" /> </button></div>
        <div v-if="selectedAsset" class="asset-detail">
          <div class="asset-detail-heading"><div><span class="page-kicker">{{ assetTypeLabels[selectedAsset.asset_type].toUpperCase() }} ASSET</span><h3>{{ selectedAsset.name }}</h3><p>{{ selectedAsset.asset_key }}</p></div><div class="asset-heading-actions"><button v-if="!editingAsset && canEditAsset" class="secondary-button compact-button" type="button" @click="beginAssetEdit">编辑资产</button><button v-if="editingAsset" class="secondary-button compact-button" type="button" @click="cancelAssetEdit">取消</button><span class="asset-status-pill" :class="selectedAsset.status">{{ statusLabel(selectedAsset.status) }}</span></div></div>
          <dl class="asset-detail-meta"><div><dt>当前版本</dt><dd>v{{ selectedAsset.version }}</dd></div><div><dt>Provider</dt><dd>{{ selectedAsset.provider }}</dd></div><div><dt>模型</dt><dd>{{ selectedAsset.model }}</dd></div><div><dt>来源章节</dt><dd>{{ selectedAsset.source_chapter_numbers.join('、') || '—' }}</dd></div></dl>
          <div v-if="!editingAsset" class="asset-aliases"><span>别名</span><div><span v-for="alias in selectedAsset.aliases" :key="alias" class="mini-tag">{{ alias }}</span><em v-if="selectedAsset.aliases.length === 0">暂无别名</em></div></div>
          <div v-if="!editingAsset" class="asset-content-fields"><div v-for="[key, value] in selectedAssetContent" :key="key"><dt>{{ contentLabels[key] || key }}</dt><dd>{{ formatValue(value) }}</dd></div></div>
          <div v-if="editingAsset" class="asset-editor"><div class="editor-note"><strong>版本化编辑</strong><span>资产名称和 asset_key 保持稳定；编辑 ready 资产会自动降为待审核。</span></div><label class="form-field"><span>别名（用、分隔）</span><input v-model="assetDraftAliases" maxlength="500" /></label><label class="form-field"><span>来源章节（用、分隔）</span><input v-model="assetDraftSourceChapters" maxlength="300" /></label><div class="asset-editor-fields"><label v-for="[key] in assetDraftEntries" :key="key" class="form-field"><span>{{ contentLabels[key] || key }}</span><textarea :value="assetDraftValue(key)" rows="2" @input="updateAssetDraftValue(key, inputValue($event))" /></label></div><div class="editor-actions"><span class="field-hint">状态：{{ statusLabel(assetDraftStatus) }}</span><button class="primary-button" type="button" :disabled="savingAsset" @click="saveAsset">{{ savingAsset ? '保存中…' : '保存为新版本' }}</button></div></div>
          <div v-if="!editingAsset && canReviewAsset" class="review-box"><div class="review-box-heading"><div><strong>提交审核</strong><span>审核会创建新版本</span></div><span v-if="loadingReviews" class="field-hint">读取历史…</span></div><div class="review-form"><label class="form-field"><span>变更状态</span><select v-model="reviewStatus" :disabled="reviewOptions.length === 0"><option v-for="status in reviewOptions" :key="status" :value="status">{{ statusLabel(status) }}</option></select></label><label class="form-field"><span>审核人</span><input v-model="reviewer" maxlength="120" /></label><label class="form-field form-field-wide"><span>审核备注（可选）</span><textarea v-model="reviewComment" rows="2" maxlength="1000" placeholder="记录参考图、连续性或内容风险检查结果" /></label></div><button class="primary-button review-submit" type="button" :disabled="!canSubmitReview" @click="submitReview">{{ submittingReview ? '正在保存…' : '提交审核版本' }}</button></div>
          <details class="review-history"><summary>审核历史（{{ reviews.length }}）</summary><div v-if="reviews.length === 0" class="history-empty">暂无审核记录</div><div v-for="review in reviews" :key="review.id" class="history-row"><span class="history-version">v{{ review.version }}</span><div><strong>{{ statusLabel(review.from_status) }} → {{ statusLabel(review.to_status) }}</strong><p>{{ review.comment || '无备注' }}</p><small>{{ review.reviewer }} · {{ formatTime(review.created_at) }}</small></div></div></details>
        </div>
      </div>
    </article>
  </section>

  <section class="footer-note"><span class="footer-note-icon">i</span><span>资产状态会直接影响后续分镜视频任务：只有 ready 资产能自动绑定；缺失、歧义或未审核资产会保留 warning，不会被静默放行。</span></section>
</template>

<style scoped>
.success-card { border-color: #bde8d6; background: var(--green-soft); }
.card-header-actions, .asset-heading-actions { display: flex; align-items: center; gap: 8px; }
.asset-detail-heading { min-width: 0; flex-wrap: wrap; }
.asset-detail-heading > div:first-child { min-width: 0; flex: 1 1 120px; }
.asset-heading-actions { flex: 0 1 auto; flex-wrap: wrap; justify-content: flex-end; }
.compact-button { padding: 6px 9px; font-size: 10px; }
.draft-status { border-radius: 999px; padding: 4px 8px; color: #8a5a0a; background: var(--amber-soft); font-size: 9px; font-weight: 700; }
.stale-warning { display: flex; gap: 8px; margin-bottom: 15px; border: 1px solid #f1dcae; border-radius: 8px; padding: 10px 11px; color: var(--amber); background: var(--amber-soft); font-size: 10px; line-height: 1.5; }
.stale-warning strong { flex: 0 0 auto; color: #99630c; }
.impact-card { display: grid; gap: 9px; margin-bottom: 15px; border: 1px solid #d9e2f4; border-radius: 9px; padding: 11px 12px; color: var(--text-muted); background: #f8fbff; font-size: 10px; line-height: 1.5; }
.impact-card.blocked { border-color: #f1dcae; background: var(--amber-soft); }
.impact-card-heading, .impact-metrics, .impact-actions { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; }
.impact-card-heading { justify-content: space-between; }
.impact-card-heading > div { display: grid; gap: 2px; }
.impact-card-heading strong { color: var(--text); font-size: 11px; }
.impact-state { color: var(--text); font-size: 9px; font-weight: 700; }
.impact-metrics span { border-radius: 999px; padding: 3px 7px; color: var(--text-muted); background: #edf2fb; }
.impact-actions strong { color: var(--text); font-size: 9px; }
.impact-action-tag { border-radius: 999px; padding: 3px 7px; color: #3d4a9b; background: #e9eaff; font-weight: 650; }
.impact-footnote { margin: 0; color: var(--text-muted); }
.editor-note { display: flex; gap: 8px; margin-bottom: 15px; border-radius: 8px; padding: 10px 11px; color: var(--primary); background: #f5f5ff; font-size: 10px; line-height: 1.5; }
.editor-note strong { flex: 0 0 auto; }
.draft-save-state { margin-left: auto; color: var(--text-muted); white-space: nowrap; }
.draft-conflict-panel { display: grid; gap: 5px; margin-bottom: 15px; border: 1px solid #efc3c3; border-radius: 8px; padding: 10px 11px; color: #9a3030; background: #fff5f5; font-size: 10px; line-height: 1.5; }
.draft-conflict-panel strong { color: #8c2525; }
.draft-conflict-panel small { color: #a55d5d; }
.draft-conflict-actions { display: flex; flex-wrap: wrap; gap: 7px; margin-top: 4px; }
.draft-merge-preview { display: grid; gap: 5px; margin-bottom: 15px; border: 1px solid #c4e5d5; border-radius: 8px; padding: 10px 11px; color: #236b4b; background: #f2fbf6; font-size: 10px; line-height: 1.5; }
.draft-merge-preview strong { color: #1c6243; }
.draft-merge-preview small { color: #56816d; }
.draft-merge-preview.conflict { border-color: #f0d1a9; color: #8b5b17; background: #fff9ee; }
.draft-merge-preview.conflict strong { color: #80500d; }
.draft-merge-preview.conflict small { color: #9a7744; }
.editor-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
.editor-grid .form-field-wide { grid-column: 1 / -1; }
.editor-section-heading, .dialogue-editor-heading { display: flex; align-items: center; justify-content: space-between; gap: 10px; margin-top: 18px; border-top: 1px solid var(--border-soft); padding-top: 14px; }
.editor-section-heading > div, .dialogue-editor-heading > div { display: grid; gap: 4px; }
.editor-section-heading strong, .dialogue-editor-heading strong { color: var(--text); font-size: 11px; }
.editor-section-heading span, .dialogue-editor-heading span { color: var(--text-muted); font-size: 9px; }
.structure-button { flex: 0 0 auto; border: 1px solid #d9dcf7; border-radius: 6px; padding: 6px 8px; color: var(--primary); background: #fafaff; font-size: 9px; font-weight: 650; }
.structure-button:hover { border-color: var(--primary); background: #f0f1ff; }
.structure-button:disabled { cursor: not-allowed; opacity: 0.45; }
.structure-button.danger { border-color: #f0d1d1; color: var(--red); background: #fffafa; }
.structure-button.danger:hover { border-color: var(--red); background: var(--red-soft); }
.scene-editor-card { margin-top: 11px; border: 1px solid #e4e7f8; border-radius: 9px; padding: 13px; background: #fafaff; }
.scene-editor-card .scene-card-heading { align-items: center; }
.scene-editor-heading-actions { display: flex; align-items: center; gap: 8px; }
.scene-editor-grid { margin-top: 12px; }
.inline-editor-input { display: block; width: 100%; margin-top: 5px; border: 1px solid #d9deea; border-radius: 6px; padding: 7px 8px; color: var(--text); background: #fff; font-size: 12px; font-weight: 650; }
.scene-title-input { min-width: 220px; }
.duration-editor { display: flex; align-items: center; gap: 5px; color: var(--text-muted); font-size: 9px; }
.duration-editor input { width: 54px; border: 1px solid #d9deea; border-radius: 6px; padding: 7px 6px; color: var(--text); background: #fff; font-size: 10px; text-align: right; }
.dialogue-editor-row { display: grid; grid-template-columns: 88px minmax(0, 1fr) 88px 54px; gap: 7px; margin-top: 7px; }
.dialogue-editor-row input, .dialogue-editor-row textarea { width: 100%; border: 1px solid #d9deea; border-radius: 6px; padding: 7px 8px; color: var(--text); background: #fff; font-size: 10px; }
.dialogue-editor-row textarea { resize: vertical; line-height: 1.5; }
.dialogue-editor-row .dialogue-delete-button { align-self: stretch; }
.dialogue-empty { margin-top: 8px; border: 1px dashed #d9deea; border-radius: 7px; padding: 10px; color: var(--text-muted); background: #fff; font-size: 10px; }
.editor-actions { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-top: 16px; border-top: 1px solid var(--border-soft); padding-top: 13px; }
.asset-editor { display: grid; gap: 12px; margin-top: 17px; border-top: 1px solid var(--border-soft); padding-top: 14px; }
.asset-editor .form-field > span { font-size: 9px; }
.asset-editor .form-field input, .asset-editor .form-field textarea { padding: 8px 9px; font-size: 10px; }
.asset-editor-fields { display: grid; gap: 10px; }
.workbench-toolbar { display: grid; grid-template-columns: minmax(260px, 1.15fr) minmax(220px, 0.85fr) minmax(220px, 0.8fr); align-items: end; gap: 15px; margin-bottom: 18px; padding: 17px 20px; }
.workbench-toolbar-summary { min-width: 0; min-height: 37px; padding: 0 2px 2px; }
.workbench-toolbar-summary strong, .workbench-toolbar-summary span { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.workbench-toolbar-summary strong { color: var(--text); font-size: 12px; }
.workbench-toolbar-summary span { margin-top: 6px; color: var(--text-muted); font-size: 10px; }
.workbench-toolbar-summary .access-badge { display: inline-block; margin-top: 8px; border-radius: 999px; padding: 4px 8px; color: var(--primary); background: #eef0ff; font-size: 9px; font-weight: 700; }
.workbench-summary-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 14px; margin-bottom: 18px; }
.audit-card { margin-bottom: 18px; padding: 16px 20px; }
.audit-card summary { display: flex; align-items: center; justify-content: space-between; gap: 14px; cursor: pointer; list-style: none; }
.audit-card summary::-webkit-details-marker { display: none; }
.audit-card summary > div { display: grid; gap: 4px; }
.audit-card summary strong { color: var(--text); font-size: 13px; }
.audit-card summary small { color: var(--text-muted); font-size: 9px; }
.audit-disclaimer { margin-top: 13px; border-radius: 7px; padding: 8px 10px; color: var(--text-muted); background: #f6f7fa; font-size: 9px; line-height: 1.5; }
.audit-row { display: flex; gap: 9px; margin-top: 13px; border-top: 1px solid var(--border-soft); padding-top: 12px; }
.audit-dot { flex: 0 0 7px; width: 7px; height: 7px; margin-top: 4px; border-radius: 50%; background: var(--primary); box-shadow: 0 0 0 3px #eef0ff; }
.audit-row > div { min-width: 0; flex: 1; }
.audit-row-heading { display: flex; align-items: center; flex-wrap: wrap; gap: 7px; }
.audit-row-heading strong { color: var(--text); font-size: 10px; }
.audit-row-heading span { border-radius: 999px; padding: 3px 6px; color: var(--primary); background: #f0f1ff; font-size: 8px; }
.audit-row p { margin: 4px 0; color: var(--text-muted); font-size: 9px; line-height: 1.5; }
.audit-row small { color: #adb5c3; font-size: 8px; }
.members-card { margin-bottom: 18px; padding: 18px 20px; }
.members-header, .member-form-heading { display: flex; align-items: flex-start; justify-content: space-between; gap: 14px; }
.members-header h2 { margin: 4px 0 0; color: var(--text-strong); font-size: 16px; }
.members-header p { margin: 6px 0 0; color: var(--text-muted); font-size: 10px; line-height: 1.5; }
.members-list { display: grid; gap: 8px; margin-top: 15px; }
.member-row { display: flex; align-items: center; gap: 10px; border: 1px solid var(--border-soft); border-radius: 8px; padding: 10px 11px; background: #fcfdff; }
.member-avatar { display: grid; place-items: center; flex: 0 0 30px; width: 30px; height: 30px; border-radius: 9px; color: #fff; background: linear-gradient(135deg, #6872df, #9b79dc); font-size: 12px; font-weight: 750; }
.member-copy { min-width: 135px; flex: 1; }
.member-copy strong, .member-copy small { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.member-copy strong { color: var(--text); font-size: 10px; }
.member-copy small { margin-top: 4px; color: var(--text-muted); font-family: "SFMono-Regular", Consolas, monospace; font-size: 8px; }
.member-role-pill { flex: 0 0 auto; border-radius: 999px; padding: 5px 8px; font-size: 9px; font-weight: 700; }
.member-role-pill.owner { color: #6d47a6; background: #f0e9ff; }
.member-role-pill.editor { color: var(--primary); background: #eef0ff; }
.member-role-pill.reviewer { color: #9a650e; background: var(--amber-soft); }
.member-role-pill.viewer { color: var(--text-muted); background: #f0f2f6; }
.member-role-description { min-width: 145px; color: var(--text-muted); font-size: 9px; }
.member-actions { display: flex; gap: 6px; }
.danger-button { border: 1px solid #f0d1d1; border-radius: 6px; padding: 6px 9px; color: var(--red); background: #fffafa; font-size: 10px; }
.danger-button:hover { border-color: var(--red); background: var(--red-soft); }
.danger-button:disabled { cursor: not-allowed; opacity: 0.5; }
.member-form { display: grid; gap: 12px; margin-top: 16px; border-top: 1px solid var(--border-soft); padding-top: 15px; }
.member-form-heading strong, .member-form-heading span { display: block; }
.member-form-heading strong { color: var(--text); font-size: 11px; }
.member-form-heading span { margin-top: 4px; color: var(--text-muted); font-size: 9px; }
.member-form-grid { display: grid; grid-template-columns: 1fr 1fr 1.25fr auto; align-items: end; gap: 10px; }
.member-save-button { white-space: nowrap; }
.members-readonly-note { margin-top: 15px; border-radius: 7px; padding: 9px 10px; color: var(--text-muted); background: #f6f7fa; font-size: 9px; }
.invitation-panel { display: grid; gap: 12px; margin-top: 16px; border-top: 1px solid var(--border-soft); padding-top: 15px; }
.invitation-form-grid { display: grid; grid-template-columns: 1.1fr 1fr 0.85fr 0.65fr auto; align-items: end; gap: 10px; }
.invitation-submit-button { white-space: nowrap; }
.invitation-token-box { display: grid; grid-template-columns: minmax(0, 1fr) auto auto; align-items: center; gap: 10px; border: 1px solid #c9d4fa; border-radius: 8px; padding: 10px 11px; color: #30428f; background: #f5f7ff; }
.invitation-token-box strong, .invitation-token-box span { display: block; }
.invitation-token-box strong { font-size: 10px; }
.invitation-token-box span { margin-top: 4px; color: #6874a5; font-size: 8px; }
.invitation-token-box code { overflow: hidden; border-radius: 5px; padding: 7px 8px; color: #25336d; background: #fff; font-size: 8px; text-overflow: ellipsis; white-space: nowrap; }
.invitation-list { display: grid; gap: 7px; }
.invitation-row { display: flex; align-items: center; gap: 9px; border-top: 1px solid var(--border-soft); padding-top: 9px; }
.invitation-row .member-copy { min-width: 0; }
.invitation-status { flex: 0 0 auto; border-radius: 999px; padding: 5px 8px; font-size: 9px; font-weight: 700; }
.invitation-status.pending { color: #9a650e; background: var(--amber-soft); }
.invitation-status.accepted { color: var(--green); background: var(--green-soft); }
.invitation-status.revoked, .invitation-status.expired { color: var(--text-muted); background: #f0f2f6; }
.workbench-main-grid { display: grid; grid-template-columns: minmax(0, 1.18fr) minmax(390px, 0.82fr); gap: 18px; }
.script-panel, .asset-panel { min-height: 640px; overflow: hidden; padding: 22px; }
.script-panel .mode-switch { margin-bottom: 18px; }
.script-document, .shot-stack { min-width: 0; }
.script-title-row, .asset-detail-heading, .scene-card-heading, .shot-card-heading, .review-box-heading { display: flex; align-items: flex-start; justify-content: space-between; gap: 14px; }
.script-title-row h3, .asset-detail-heading h3 { margin: 0; color: var(--text-strong); font-size: 20px; letter-spacing: -0.03em; }
.script-title-row .page-kicker, .asset-detail-heading .page-kicker { margin-bottom: 7px; }
.script-logline { margin: 14px 0 0; color: var(--text); font-size: 12px; line-height: 1.7; }
.hook-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-top: 17px; }
.hook-grid > div { border: 1px solid #e4e7f8; border-radius: 8px; padding: 12px; background: #fafaff; }
.hook-grid span { color: var(--primary); font-size: 10px; font-weight: 700; }
.hook-grid p { margin: 7px 0 0; color: var(--text-muted); font-size: 10px; line-height: 1.55; }
.scene-stack, .shot-stack { display: grid; gap: 11px; margin-top: 18px; }
.scene-card, .shot-card { border: 1px solid var(--border-soft); border-radius: 9px; padding: 14px; background: #fcfdff; }
.scene-index { color: var(--primary); font-size: 9px; font-weight: 750; letter-spacing: 0.06em; text-transform: uppercase; }
.scene-card h4, .shot-card h4 { margin: 5px 0 0; color: var(--text); font-size: 13px; }
.scene-duration { flex: 0 0 auto; border-radius: 999px; padding: 4px 7px; color: var(--text-muted); background: #f0f2f6; font-family: "SFMono-Regular", Consolas, monospace; font-size: 9px; }
.scene-meta { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 11px; }
.scene-meta span, .mini-tag { border-radius: 999px; padding: 4px 7px; color: var(--text-muted); background: #f1f3f7; font-size: 9px; }
.scene-action, .scene-narration, .shot-prompt { margin: 12px 0 0; color: var(--text); font-size: 11px; line-height: 1.65; }
.scene-narration { color: #5f69bd; }
.dialogue-list { display: grid; gap: 6px; margin-top: 12px; border-top: 1px solid var(--border-soft); padding-top: 11px; }
.dialogue-row { display: grid; grid-template-columns: 62px minmax(0, 1fr) auto; gap: 7px; align-items: baseline; color: var(--text-muted); font-size: 10px; line-height: 1.5; }
.dialogue-row strong { color: var(--text); font-size: 10px; }
.dialogue-row small { color: var(--purple); font-size: 9px; }
.shot-health { display: flex; flex-wrap: wrap; gap: 7px; }
.shot-health span { border-radius: 999px; padding: 5px 8px; color: var(--green); background: var(--green-soft); font-size: 9px; }
.shot-health span.warning { color: var(--amber); background: var(--amber-soft); }
.shot-health span.danger { color: var(--red); background: var(--red-soft); }
.shot-tags { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 12px; }
.asset-ref-tag { border-radius: 999px; padding: 5px 8px; color: var(--green); background: var(--green-soft); font-size: 9px; }
.asset-ref-tag.unresolved, .asset-ref-tag.warning { color: var(--red); background: var(--red-soft); }
.asset-ref-tag.warning { color: var(--amber); background: var(--amber-soft); }
.shot-footnote { display: block; margin-top: 12px; color: var(--text-muted); font-size: 9px; line-height: 1.5; }
.asset-filter-row { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin-bottom: 13px; }
.asset-filter-row select { width: 100%; border: 1px solid #d9deea; border-radius: 7px; padding: 9px 10px; color: var(--text); background: #fff; font-size: 10px; }
.asset-workspace { display: grid; grid-template-columns: minmax(145px, 0.7fr) minmax(0, 1.3fr); gap: 14px; min-height: 490px; }
.asset-list { overflow: auto; border: 1px solid var(--border-soft); border-radius: 8px; background: #fff; }
.asset-list-row { display: flex; align-items: center; gap: 8px; width: 100%; border: 0; border-bottom: 1px solid var(--border-soft); padding: 10px; color: var(--text); background: #fff; text-align: left; }
.asset-list-row:last-child { border-bottom: 0; }
.asset-list-row:hover, .asset-list-row.selected { background: #fafbff; }
.asset-list-row.selected { box-shadow: inset 3px 0 0 var(--primary); }
.asset-type-icon { display: grid; place-items: center; flex: 0 0 26px; width: 26px; height: 26px; border-radius: 7px; color: #fff; background: #6872df; font-size: 10px; font-weight: 700; }
.asset-type-icon.location { background: #b67a18; }
.asset-type-icon.prop { background: #277b60; }
.asset-list-copy { min-width: 0; flex: 1; }
.asset-list-copy strong, .asset-list-copy small { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.asset-list-copy strong { font-size: 10px; font-weight: 650; }
.asset-list-copy small { margin-top: 4px; color: var(--text-muted); font-size: 8px; }
.asset-status-dot { width: 6px; height: 6px; border-radius: 50%; background: #b4bdcb; }
.asset-status-dot.ready { background: var(--green); }
.asset-status-dot.needs_review { background: var(--amber); }
.asset-status-dot.archived { background: #8994a5; }
.asset-detail { min-width: 0; border-left: 1px solid var(--border-soft); padding-left: 14px; }
.asset-detail-heading p { overflow: hidden; margin: 6px 0 0; color: var(--text-muted); font-family: "SFMono-Regular", Consolas, monospace; font-size: 8px; text-overflow: ellipsis; white-space: nowrap; }
.asset-status-pill { flex: 0 0 auto; border-radius: 999px; padding: 5px 8px; font-size: 9px; font-weight: 650; }
.asset-status-pill.ready { color: var(--green); background: var(--green-soft); }
.asset-status-pill.needs_review { color: var(--amber); background: var(--amber-soft); }
.asset-status-pill.draft { color: var(--primary); background: #f0f1ff; }
.asset-status-pill.archived { color: var(--text-muted); background: #f0f2f6; }
.asset-detail-meta { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin: 20px 0 0; border-top: 1px solid var(--border-soft); padding-top: 14px; }
.asset-detail-meta dt, .asset-content-fields dt { margin-bottom: 5px; color: var(--text-muted); font-size: 9px; }
.asset-detail-meta dd, .asset-content-fields dd { overflow: hidden; margin: 0; color: var(--text); font-size: 10px; line-height: 1.55; }
.asset-aliases { margin-top: 15px; border-top: 1px solid var(--border-soft); padding-top: 13px; }
.asset-aliases > span { display: block; margin-bottom: 7px; color: var(--text-muted); font-size: 9px; }
.asset-aliases > div { display: flex; flex-wrap: wrap; gap: 5px; }
.asset-aliases em { color: #b1b9c6; font-size: 9px; font-style: normal; }
.asset-content-fields { display: grid; gap: 11px; margin-top: 15px; border-top: 1px solid var(--border-soft); padding-top: 14px; }
.asset-content-fields div { min-width: 0; }
.review-box { margin-top: 17px; border: 1px solid #e3e6fb; border-radius: 8px; padding: 12px; background: #fafaff; }
.review-box-heading strong, .review-box-heading span { display: block; }
.review-box-heading strong { color: var(--text); font-size: 11px; }
.review-box-heading span { margin-top: 4px; color: var(--text-muted); font-size: 9px; }
.review-form { display: grid; grid-template-columns: 1fr 1fr; gap: 11px; margin-top: 13px; }
.review-form .form-field-wide { grid-column: 1 / -1; }
.review-form .form-field > span { font-size: 9px; }
.review-form .form-field input, .review-form .form-field select, .review-form .form-field textarea { padding: 8px 9px; font-size: 10px; }
.review-form .form-field textarea { min-height: 54px; }
.review-submit { width: 100%; margin-top: 12px; padding: 8px 11px; font-size: 10px; }
.review-history { margin-top: 16px; border-top: 1px solid var(--border-soft); padding-top: 12px; }
.review-history summary { cursor: pointer; color: var(--text); font-size: 10px; font-weight: 650; }
.history-empty { margin-top: 10px; color: var(--text-muted); font-size: 9px; }
.history-row { display: flex; gap: 8px; margin-top: 10px; }
.history-version { flex: 0 0 auto; color: var(--primary); font-family: "SFMono-Regular", Consolas, monospace; font-size: 9px; }
.history-row strong, .history-row p, .history-row small { display: block; }
.history-row strong { color: var(--text); font-size: 9px; }
.history-row p { margin: 3px 0; color: var(--text-muted); font-size: 9px; }
.history-row small { color: #adb5c3; font-size: 8px; }

@media (max-width: 1100px) {
  .workbench-main-grid { grid-template-columns: 1fr; }
  .asset-panel { min-height: auto; }
}

@media (max-width: 720px) {
  .workbench-toolbar, .workbench-summary-grid, .member-form-grid, .invitation-form-grid { grid-template-columns: 1fr; }
  .workbench-main-grid { display: block; }
  .asset-panel { margin-top: 18px; }
  .hook-grid, .asset-workspace, .review-form, .editor-grid { grid-template-columns: 1fr; }
  .editor-grid .form-field-wide { grid-column: auto; }
  .dialogue-editor-row { grid-template-columns: 1fr; }
  .editor-actions { align-items: stretch; flex-direction: column; }
  .editor-actions .primary-button { width: 100%; }
  .asset-detail { border-top: 1px solid var(--border-soft); border-left: 0; margin-top: 12px; padding-top: 14px; padding-left: 0; }
  .asset-workspace { display: block; }
  .asset-list { max-height: 240px; }
  .review-form .form-field-wide { grid-column: auto; }
  .member-role-description { display: none; }
  .member-actions { flex-direction: column; }
  .member-save-button { width: 100%; }
  .invitation-submit-button { width: 100%; }
  .invitation-token-box { grid-template-columns: 1fr; }
  .dialogue-row { grid-template-columns: 55px minmax(0, 1fr); }
  .dialogue-row small { grid-column: 2; }
}
</style>

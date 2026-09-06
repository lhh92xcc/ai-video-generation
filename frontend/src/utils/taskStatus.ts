import type { GenerationTaskRecord, TaskError, TaskStatus } from '../types/task'

const statusLabels: Record<TaskStatus, string> = {
  created: '已创建',
  queued: '排队中',
  running: '处理中',
  succeeded: '已完成',
  failed: '失败',
  canceled: '已取消',
}

const taskKindLabels: Record<string, string> = {
  novel_story_bible: '故事设定分析',
  novel_episode_plan: '分集大纲',
  novel_episode_script: '分场剧本',
  novel_shot_list: '分镜生成',
  asset_reference_image: '参考图生成',
  audio_narration: '旁白配音',
  audio_bgm: 'BGM 音频',
  subtitle_asr: 'ASR 字幕',
  subtitle_align: '字幕对齐',
  subtitle_srt: '字幕文件',
  video_clip: '视频片段',
  video_assembly: '成片合成',
  lip_sync: '唇形同步',
  info_script: '信息短视频脚本',
}

const errorLabels: Record<string, string> = {
  VIDEO_PROVIDER_RATE_LIMITED: '视频服务当前请求较多，暂时触发限流。',
  VIDEO_PROVIDER_AUTH_FAILED: '视频服务密钥无效或尚未配置。',
  VIDEO_PROVIDER_NOT_CONFIGURED: '视频服务尚未配置，请先在制作后台检查 Provider。',
  VIDEO_PROVIDER_TIMEOUT: '视频服务响应超时，已保留任务，可稍后重试。',
  VIDEO_PROVIDER_UNAVAILABLE: '视频服务当前不可用，请检查 Worker 网络或服务状态。',
  VIDEO_PROVIDER_FAILED: '视频服务生成失败，请稍后重试或查看制作后台详情。',
  IMAGE_PROVIDER_RATE_LIMITED: '参考图服务当前请求较多，暂时触发限流。',
  IMAGE_PROVIDER_AUTH_FAILED: '参考图服务密钥无效或尚未配置。',
  IMAGE_PROVIDER_NOT_CONFIGURED: '参考图服务尚未配置，请先检查 Provider。',
  IMAGE_PROVIDER_TIMEOUT: '参考图服务响应超时，可稍后重试。',
  IMAGE_PROVIDER_UNAVAILABLE: '参考图服务当前不可用，请检查 Worker 状态。',
  TTS_PROVIDER_RATE_LIMITED: '配音服务当前请求较多，暂时触发限流。',
  TTS_PROVIDER_AUTH_FAILED: '配音服务密钥无效或尚未配置。',
  TTS_PROVIDER_TIMEOUT: '配音服务响应超时，可稍后重试。',
  TTS_PROVIDER_UNAVAILABLE: '配音服务当前不可用，请检查 Worker 网络。',
  ASR_PROVIDER_RATE_LIMITED: '字幕识别服务当前请求较多，暂时触发限流。',
  ASR_PROVIDER_AUTH_FAILED: '字幕识别服务密钥无效或尚未配置。',
  ASR_PROVIDER_TIMEOUT: '字幕识别服务响应超时，可稍后重试。',
  ASR_PROVIDER_UNAVAILABLE: '字幕识别服务当前不可用，请检查 Provider。',
  STORAGE_NOT_FOUND: '任务已登记，但对象存储中找不到产物文件。',
  STORAGE_UNAVAILABLE: '对象存储当前不可用，请稍后重试。',
  ARTIFACT_CONTENT_UNAVAILABLE: '产物已登记，但服务端暂时无法读取文件内容。',
  VIDEO_ARTIFACT_NOT_PLAYABLE: '视频已返回，但播放性校验未通过。',
  AUDIO_ARTIFACT_NOT_PLAYABLE: '音频已返回，但播放性校验未通过。',
  FFMPEG_SUBTITLE_UNAVAILABLE: '当前 FFmpeg 缺少字幕滤镜，无法烧录字幕。',
  PROJECT_PERMISSION_DENIED: '当前账号没有访问该项目的权限。',
}

export function formatStatus(status: TaskStatus | string): string {
  return statusLabels[status as TaskStatus] ?? status
}

export function formatTaskKind(kind: string): string {
  return taskKindLabels[kind] ?? kind.replaceAll('_', ' ')
}

export function friendlyErrorMessage(error: TaskError | null | undefined): string {
  if (!error) return '任务失败，请稍后重试。'
  const code = error.code ?? ''
  if (errorLabels[code]) return errorLabels[code]

  const rawMessage = error.message?.trim() ?? ''
  if (/\b402\b|payment required|余额不足|quota/i.test(rawMessage)) {
    return '服务商返回余额不足或当前账号尚未开通额度。'
  }
  if (/\b429\b|too many requests|rate limit|限流/i.test(rawMessage)) {
    return '服务商当前请求较多，暂时触发限流，请稍后重试。'
  }
  if (/\b401\b|\b403\b|unauthorized|forbidden|authentication/i.test(rawMessage)) {
    return '服务商鉴权失败，请检查 API Key 和配置档案。'
  }
  return rawMessage || '任务失败，请稍后重试。'
}

export function taskErrorDetail(error: TaskError | null | undefined): string {
  if (!error) return ''
  const message = friendlyErrorMessage(error)
  return error.code ? `${message}（错误码：${error.code}）` : message
}

export function isActiveTask(task: GenerationTaskRecord): boolean {
  return ['created', 'queued', 'running'].includes(task.status)
}

export function isMediaTaskKind(kind: string): boolean {
  return ['asset_reference_image', 'video_clip', 'lip_sync', 'video_assembly'].includes(kind)
}

export function hasBinaryArtifact(task: GenerationTaskRecord): boolean {
  return task.artifacts.some((artifact) => [
    'reference_image',
    'video_clip',
    'lip_synced_video',
    'rendered_video',
  ].includes(artifact.type))
}

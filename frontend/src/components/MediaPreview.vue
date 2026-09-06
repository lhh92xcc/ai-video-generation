<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { getArtifactContentUrl } from '../api/tasks'

type PreviewArtifact = {
  id: string
  type: string
  metadata: Record<string, unknown>
  download_url?: string | null
}

const props = withDefaults(defineProps<{
  artifact: PreviewArtifact
  variant?: 'thumb' | 'panel'
  controls?: boolean
  showDownload?: boolean
  alt?: string
}>(), {
  variant: 'panel',
  controls: true,
  showDownload: false,
  alt: '媒体资产预览',
})

const fallbackUsed = ref(false)
const loadFailed = ref(false)
const mediaLoaded = ref(false)
const reloadKey = ref(0)

const storageKey = computed(() => {
  const value = props.artifact.metadata.storage_key
  return typeof value === 'string' && value.trim() ? value : ''
})

const contentUrl = computed(() => storageKey.value ? getArtifactContentUrl(props.artifact.id) : '')
const signedUrl = computed(() => props.artifact.download_url ?? '')
const previewUrl = computed(() => {
  if (fallbackUsed.value) return signedUrl.value
  return contentUrl.value || signedUrl.value
})
const downloadUrl = computed(() => contentUrl.value ? getArtifactContentUrl(props.artifact.id, true) : signedUrl.value)

const mediaKind = computed<'image' | 'video' | 'audio' | 'document'>(() => {
  if (['reference_image'].includes(props.artifact.type)) return 'image'
  if (['rendered_video', 'video_clip', 'lip_synced_video'].includes(props.artifact.type)) return 'video'
  if (['audio_narration', 'audio_bgm'].includes(props.artifact.type)) return 'audio'

  const contentType = String(props.artifact.metadata.content_type ?? '').toLowerCase()
  if (contentType.startsWith('image/')) return 'image'
  if (contentType.startsWith('video/')) return 'video'
  if (contentType.startsWith('audio/')) return 'audio'
  return 'document'
})

const canPreview = computed(() => Boolean(previewUrl.value) && mediaKind.value !== 'document')
const isThumb = computed(() => props.variant === 'thumb')

function resetPreview() {
  fallbackUsed.value = false
  loadFailed.value = false
  mediaLoaded.value = false
  reloadKey.value += 1
}

function onLoaded() {
  mediaLoaded.value = true
  loadFailed.value = false
}

function onError() {
  // The same-origin route is the reliable path for local and Docker storage.
  // Only fall back to a signed object URL when the authorized route itself is
  // unavailable (for example, while an older API image is still running).
  if (!fallbackUsed.value && contentUrl.value && signedUrl.value) {
    fallbackUsed.value = true
    mediaLoaded.value = false
    reloadKey.value += 1
    return
  }
  mediaLoaded.value = false
  loadFailed.value = true
}

watch(() => props.artifact.id, resetPreview)
</script>

<template>
  <div class="media-preview" :class="[`media-preview-${variant}`, `media-preview-${mediaKind}`, { 'is-loading': canPreview && !mediaLoaded && !loadFailed, 'is-failed': loadFailed }]">
    <template v-if="canPreview && !loadFailed">
      <video
        v-if="mediaKind === 'video'"
        :key="`${props.artifact.id}-${reloadKey}`"
        :src="previewUrl"
        :controls="controls && !isThumb"
        preload="metadata"
        :muted="isThumb"
        playsinline
        :aria-label="alt"
        @loadedmetadata="onLoaded"
        @error="onError"
      />
      <audio
        v-else-if="mediaKind === 'audio'"
        :key="`${props.artifact.id}-${reloadKey}`"
        :src="previewUrl"
        :controls="controls"
        preload="metadata"
        :aria-label="alt"
        @canplay="onLoaded"
        @error="onError"
      />
      <img
        v-else
        :key="`${props.artifact.id}-${reloadKey}`"
        :src="previewUrl"
        :alt="alt"
        @load="onLoaded"
        @error="onError"
      />
      <span v-if="!mediaLoaded" class="media-preview-spinner spinner" aria-label="正在加载预览" />
    </template>

    <div v-else-if="loadFailed" class="media-preview-message">
      <span class="media-preview-message-icon">!</span>
      <strong>预览暂时失败</strong>
      <small>已尝试服务端安全通道和临时链接</small>
      <button type="button" @click="resetPreview">重新加载</button>
    </div>

    <div v-else-if="mediaKind === 'document'" class="media-preview-message">
      <span class="media-preview-message-icon">文</span>
      <strong>结构化文件</strong>
      <small>下载后查看完整内容</small>
    </div>

    <div v-else class="media-preview-message">
      <span class="media-preview-message-icon">i</span>
      <strong>暂无可预览文件</strong>
      <small>该 Artifact 没有可读取的二进制内容</small>
    </div>

    <a v-if="showDownload && downloadUrl" class="media-preview-download" :href="downloadUrl" target="_blank" rel="noopener" download>
      下载文件 <span>↓</span>
    </a>
  </div>
</template>

<style scoped>
.media-preview { position: relative; display: grid; place-items: center; overflow: hidden; width: 100%; min-height: 100%; background: #151a28; }
.media-preview-thumb { min-height: 42px; border-radius: 9px; }
.media-preview-panel { min-height: 250px; border-radius: 9px; }
.media-preview video, .media-preview img { display: block; width: 100%; height: 100%; object-fit: contain; background: #10131d; }
.media-preview-thumb video, .media-preview-thumb img { object-fit: cover; }
.media-preview audio { width: calc(100% - 36px); }
.media-preview-panel video { max-height: 420px; }
.media-preview-panel img { max-height: 420px; }
.media-preview-spinner { position: absolute; top: 50%; left: 50%; margin: -9px 0 0 -9px; border-color: rgba(255,255,255,.3); border-top-color: #fff; }
.media-preview-message { display: grid; place-items: center; gap: 6px; min-height: 140px; padding: 20px; color: #d4d9e8; text-align: center; }
.media-preview-thumb .media-preview-message { min-height: 42px; padding: 4px; gap: 0; }
.media-preview-message-icon { display: grid; place-items: center; width: 29px; height: 29px; border: 1px solid rgba(214,220,237,.45); border-radius: 9px; color: #fff; background: rgba(255,255,255,.12); font-size: 11px; font-weight: 750; }
.media-preview-thumb .media-preview-message-icon { width: 24px; height: 24px; border-radius: 7px; font-size: 9px; }
.media-preview-message strong { font-size: 11px; }
.media-preview-message small { max-width: 220px; color: #9da7bc; font-size: 9px; line-height: 1.5; }
.media-preview-thumb .media-preview-message strong, .media-preview-thumb .media-preview-message small { display: none; }
.media-preview-message button { margin-top: 4px; border: 1px solid rgba(214,220,237,.35); border-radius: 6px; padding: 6px 9px; color: #fff; background: rgba(255,255,255,.1); font-size: 9px; }
.media-preview-message button:hover { background: rgba(255,255,255,.18); }
.media-preview-download { position: absolute; right: 10px; bottom: 10px; border: 1px solid rgba(255,255,255,.28); border-radius: 6px; padding: 6px 8px; color: #fff; background: rgba(16,19,29,.72); font-size: 9px; text-decoration: none; }
.media-preview-download:hover { background: rgba(16,19,29,.92); }
</style>

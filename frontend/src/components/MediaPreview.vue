<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
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
  lazy?: boolean
}>(), {
  variant: 'panel',
  controls: true,
  showDownload: false,
  alt: '媒体资产预览',
  lazy: undefined,
})

const previewRoot = ref<HTMLElement | null>(null)
const fallbackUsed = ref(false)
const loadFailed = ref(false)
const mediaLoaded = ref(false)
const loadingTimedOut = ref(false)
const reloadKey = ref(0)
const inViewport = ref(props.lazy !== true)
let intersectionObserver: IntersectionObserver | null = null
let loadTimer: number | null = null

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
const previewRequestUrl = computed(() => {
  // Cache-bust only the authorized content endpoint. Signed object URLs are
  // signed over their query string and must not be modified.
  if (!previewUrl.value || fallbackUsed.value || !contentUrl.value || reloadKey.value === 0) return previewUrl.value
  const separator = previewUrl.value.includes('?') ? '&' : '?'
  return `${previewUrl.value}${separator}preview_attempt=${reloadKey.value}`
})
const downloadUrl = computed(() => fallbackUsed.value && signedUrl.value
  ? signedUrl.value
  : contentUrl.value
    ? getArtifactContentUrl(props.artifact.id, true)
    : signedUrl.value)
const previewSourceLabel = computed(() => fallbackUsed.value ? '临时链接回退' : contentUrl.value ? '服务端安全预览' : signedUrl.value ? '签名链接预览' : '未登记文件地址')

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

// Thumbnails are intentionally eager by default: the asset list is a visual
// review surface, so a placeholder must not be mistaken for an unavailable
// Artifact. Callers can opt into IntersectionObserver loading with lazy=true.
const lazyPreview = computed(() => props.lazy === true)
const deferred = computed(() => lazyPreview.value && !inViewport.value && mediaKind.value !== 'document')
const canPreview = computed(() => Boolean(previewUrl.value) && mediaKind.value !== 'document' && !deferred.value)
const isThumb = computed(() => props.variant === 'thumb')
const isMediaLoading = computed(() => canPreview.value && !mediaLoaded.value && !loadFailed.value && !loadingTimedOut.value)
const previewStatus = computed(() => {
  if (loadFailed.value) return '文件读取失败'
  if (loadingTimedOut.value) return '读取时间较长'
  if (mediaLoaded.value) return '已加载'
  if (deferred.value) return '等待进入视口'
  if (mediaKind.value === 'document') return '结构化文件'
  if (canPreview.value) return '正在读取文件'
  return '未关联文件'
})
const previewStatusClass = computed(() => {
  if (loadFailed.value) return 'failed'
  if (loadingTimedOut.value) return 'slow'
  if (mediaLoaded.value) return 'loaded'
  return 'loading'
})

function clearLoadTimer() {
  if (loadTimer !== null) {
    window.clearTimeout(loadTimer)
    loadTimer = null
  }
}

function armLoadTimer() {
  clearLoadTimer()
  loadingTimedOut.value = false
  if (!canPreview.value || mediaLoaded.value || loadFailed.value) return
  loadTimer = window.setTimeout(() => {
    if (!mediaLoaded.value && !loadFailed.value) loadingTimedOut.value = true
  }, 12000)
}

function resetPreview() {
  fallbackUsed.value = false
  loadFailed.value = false
  mediaLoaded.value = false
  loadingTimedOut.value = false
  clearLoadTimer()
  reloadKey.value += 1
}

function observePreview() {
  intersectionObserver?.disconnect()
  intersectionObserver = null
  inViewport.value = !lazyPreview.value
  if (!lazyPreview.value || !previewRoot.value || typeof IntersectionObserver === 'undefined') {
    inViewport.value = true
    return
  }
  intersectionObserver = new IntersectionObserver((entries) => {
    if (entries.some((entry) => entry.isIntersecting)) {
      inViewport.value = true
      intersectionObserver?.disconnect()
      intersectionObserver = null
    }
  }, { rootMargin: '160px' })
  intersectionObserver.observe(previewRoot.value)
}

function onLoaded() {
  clearLoadTimer()
  mediaLoaded.value = true
  loadFailed.value = false
  loadingTimedOut.value = false
}

function onError() {
  clearLoadTimer()
  // The same-origin route is the reliable path for local and Docker storage.
  // Only fall back to a signed object URL when the authorized route itself is
  // unavailable (for example, while an older API image is still running).
  if (!fallbackUsed.value && contentUrl.value && signedUrl.value) {
    fallbackUsed.value = true
    mediaLoaded.value = false
    loadingTimedOut.value = false
    reloadKey.value += 1
    return
  }
  mediaLoaded.value = false
  loadFailed.value = true
  loadingTimedOut.value = false
}

function onLoadStart() {
  mediaLoaded.value = false
  loadingTimedOut.value = false
  armLoadTimer()
}

watch(() => props.artifact.id, () => {
  resetPreview()
  observePreview()
})
watch(() => [props.lazy, props.variant], observePreview)
watch([canPreview, previewUrl], armLoadTimer)
onMounted(observePreview)
onUnmounted(() => {
  intersectionObserver?.disconnect()
  clearLoadTimer()
})
</script>

<template>
  <div ref="previewRoot" class="media-preview" :class="[`media-preview-${variant}`, `media-preview-${mediaKind}`, { 'is-loading': isMediaLoading || loadingTimedOut, 'is-failed': loadFailed, 'is-deferred': deferred }]" :aria-busy="isMediaLoading || loadingTimedOut ? 'true' : 'false'">
    <template v-if="canPreview && !loadFailed && !loadingTimedOut">
      <video
        v-if="mediaKind === 'video'"
        :key="`${props.artifact.id}-${reloadKey}`"
        :src="previewRequestUrl"
        :controls="controls && !isThumb"
        :preload="isThumb ? 'auto' : 'metadata'"
        :muted="isThumb"
        :autoplay="isThumb"
        :loop="isThumb"
        playsinline
        :aria-label="alt"
        @loadstart="onLoadStart"
        @loadedmetadata="onLoaded"
        @loadeddata="onLoaded"
        @canplay="onLoaded"
        @error="onError"
      >
        正在读取视频文件…
      </video>
      <audio
        v-else-if="mediaKind === 'audio'"
        :key="`${props.artifact.id}-${reloadKey}`"
        :src="previewRequestUrl"
        :controls="controls"
        preload="metadata"
        :aria-label="alt"
        @loadstart="onLoadStart"
        @loadedmetadata="onLoaded"
        @canplay="onLoaded"
        @error="onError"
      />
      <img
        v-else
        :key="`${props.artifact.id}-${reloadKey}`"
        :src="previewRequestUrl"
        :alt="alt"
        :loading="lazyPreview ? 'lazy' : 'eager'"
        decoding="async"
        @loadstart="onLoadStart"
        @load="onLoaded"
        @error="onError"
      />
      <span v-if="isMediaLoading" class="media-preview-spinner spinner" aria-hidden="true" />
    </template>

    <div v-else-if="deferred" class="media-preview-message">
      <span class="media-preview-message-icon">◌</span>
      <strong>滚动后加载预览</strong>
      <small>仅加载当前可见的媒体，避免一次请求全部文件</small>
    </div>

    <div v-else-if="loadFailed" class="media-preview-message">
      <span class="media-preview-message-icon">!</span>
      <strong>文件加载失败</strong>
      <small>已尝试{{ contentUrl && signedUrl ? '服务端安全通道和临时链接' : '当前可用的文件地址' }}；这不代表任务没有生成产物。</small>
      <button v-if="!isThumb" type="button" @click="resetPreview">重新加载</button>
    </div>

    <div v-else-if="loadingTimedOut" class="media-preview-message media-preview-slow-message" role="status" aria-live="polite">
      <span class="media-preview-message-icon">↻</span>
      <strong>文件读取较慢</strong>
      <small>媒体仍在从服务端读取，可以继续等待或重新加载。</small>
      <button v-if="!isThumb" type="button" @click="resetPreview">重新加载</button>
    </div>

    <div v-else-if="mediaKind === 'document'" class="media-preview-message">
      <span class="media-preview-message-icon">文</span>
      <strong>结构化文件</strong>
      <small>下载后查看完整内容</small>
    </div>

    <div v-else class="media-preview-message">
      <span class="media-preview-message-icon">i</span>
      <strong>未找到媒体文件</strong>
      <small>Artifact 记录存在，但服务端没有登记可读取的文件地址；这不是浏览器预览故障。</small>
    </div>

    <a v-if="showDownload && downloadUrl" class="media-preview-download" :href="downloadUrl" target="_blank" rel="noopener" download>
      下载文件 <span>↓</span>
    </a>
    <span v-if="!isThumb && (canPreview || loadFailed || downloadUrl)" class="media-preview-source" :class="previewStatusClass">
      {{ previewStatus }} · {{ previewSourceLabel }}
    </span>
  </div>
</template>

<style scoped>
.media-preview { position: relative; display: grid; place-items: center; overflow: hidden; width: 100%; min-height: 100%; background: #151a28; }
.media-preview-thumb { min-height: 42px; border-radius: 9px; }
.media-preview-panel { min-height: 250px; border-radius: 9px; }
.media-preview video, .media-preview img { display: block; width: 100%; height: 100%; object-fit: contain; background: #10131d; }
.media-preview-thumb video, .media-preview-thumb img { object-fit: cover; }
.media-preview-thumb video { pointer-events: none; }
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
.media-preview-slow-message { position: absolute; inset: 0; z-index: 2; background: rgba(21,26,40,.86); }
.media-preview-download { position: absolute; right: 10px; bottom: 10px; z-index: 3; border: 1px solid rgba(255,255,255,.28); border-radius: 6px; padding: 6px 8px; color: #fff; background: rgba(16,19,29,.72); font-size: 9px; text-decoration: none; }
.media-preview-download:hover { background: rgba(16,19,29,.92); }
.media-preview-source { position: absolute; top: 10px; left: 10px; z-index: 3; border: 1px solid rgba(255,255,255,.18); border-radius: 999px; padding: 4px 7px; color: #d9deed; background: rgba(16,19,29,.62); font-size: 8px; }
.media-preview-source.loaded { color: #d8f4e6; border-color: rgba(120,221,171,.35); }
.media-preview-source.slow { color: #ffe9b7; border-color: rgba(239,193,103,.4); }
.media-preview-source.failed { color: #ffd8dd; border-color: rgba(240,145,157,.4); }
.media-preview-thumb .media-preview-source { display: none; }
</style>

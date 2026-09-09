<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { getHealth, type HealthResponse } from './api/client'
import ProviderProfileSelect from './components/ProviderProfileSelect.vue'
import RuntimeOverview from './components/RuntimeOverview.vue'
import ArtifactLibrary from './components/ArtifactLibrary.vue'
import SubtitleTaskView from './components/SubtitleTaskView.vue'
import TaskCenter from './components/TaskCenter.vue'
import ProductionQueue from './components/ProductionQueue.vue'
import RunLog from './components/RunLog.vue'
import ScriptAssetWorkbench from './components/ScriptAssetWorkbench.vue'
import CreatorHome from './components/CreatorHome.vue'
import { useProviderProfiles } from './composables/useProviderProfiles'
import type { ProviderCapability } from './api/providerProfiles'

type AppView = 'overview' | 'provider' | 'subtitle' | 'tasks' | 'queue' | 'artifacts' | 'workbench' | 'logs'
type AppSurface = 'operator' | 'creator'

const initialSurface = new URLSearchParams(window.location.search).get('surface')
const initialViewParam = new URLSearchParams(window.location.search).get('view')
const supportedViews: AppView[] = ['overview', 'provider', 'subtitle', 'tasks', 'queue', 'artifacts', 'workbench', 'logs']
const initialView = supportedViews.includes(initialViewParam as AppView) ? initialViewParam as AppView : 'overview'
const activeView = ref<AppView>(initialView)
const activeSurface = ref<AppSurface>(initialSurface === 'creator' || (!initialSurface && !initialViewParam) ? 'creator' : 'operator')

function syncLocation() {
  const params = new URLSearchParams(window.location.search)
  const surface = params.get('surface')
  const view = params.get('view')
  activeSurface.value = surface === 'creator' || (!surface && !view) ? 'creator' : 'operator'
  activeView.value = supportedViews.includes(view as AppView) ? view as AppView : 'overview'
}

function persistLocation() {
  const params = new URLSearchParams(window.location.search)
  if (activeSurface.value === 'creator') {
    params.set('surface', 'creator')
    params.delete('view')
  } else {
    params.delete('surface')
    params.set('view', activeView.value)
  }
  const query = params.toString()
  window.history.replaceState(null, '', `${window.location.pathname}${query ? `?${query}` : ''}${window.location.hash}`)
}

function setOperatorView(view: AppView = 'overview') {
  activeSurface.value = 'operator'
  activeView.value = view
  showNotifications.value = false
  showHelp.value = false
  persistLocation()
}

function openCreatorSurface() {
  activeSurface.value = 'creator'
  showNotifications.value = false
  showHelp.value = false
  persistLocation()
}

function openHelpCreator() {
  showHelp.value = false
  openCreatorSurface()
}

function openOperator(view: AppView = 'overview') {
  setOperatorView(view)
}

function selectMobileView(event: Event) {
  const view = (event.target as HTMLSelectElement).value as AppView
  if (supportedViews.includes(view)) setOperatorView(view)
}

const {
  profiles,
  availableProfiles,
  defaultProfileId,
  selectedProfile,
  selectedProfileId,
  isLoading: profilesLoading,
  hasLoaded: profilesLoaded,
  errorMessage: profilesError,
  errorCode: profilesErrorCode,
  refresh: refreshProfiles,
  selectProfile,
} = useProviderProfiles()
const imageProviderProfiles = useProviderProfiles('image')
const videoProviderProfiles = useProviderProfiles('video')

const providerCapabilityTabs: Array<{
  id: ProviderCapability
  eyebrow: string
  label: string
  description: string
  total: number
}> = [
  { id: 'asr', eyebrow: 'ASR', label: '字幕识别', description: '把旁白转成带时间轴的字幕', total: 4 },
  { id: 'image', eyebrow: 'IMAGE', label: '参考图生成', description: '生成角色、场景和道具关键帧', total: 4 },
  { id: 'video', eyebrow: 'VIDEO', label: '视频片段', description: '把参考图转换为可播放镜头', total: 6 },
]
const activeProviderCapability = ref<ProviderCapability>('asr')
const providerStateByCapability = {
  asr: {
    profiles,
    availableProfiles,
    defaultProfileId,
    selectedProfile,
    selectedProfileId,
    isLoading: profilesLoading,
    hasLoaded: profilesLoaded,
    errorMessage: profilesError,
    errorCode: profilesErrorCode,
    refresh: refreshProfiles,
    selectProfile,
  },
  image: imageProviderProfiles,
  video: videoProviderProfiles,
} as const

const activeProviderState = computed(() => providerStateByCapability[activeProviderCapability.value])
const activeProviderMeta = computed(() => providerCapabilityTabs.find((tab) => tab.id === activeProviderCapability.value) ?? providerCapabilityTabs[0])
const activeProfiles = computed(() => activeProviderState.value.profiles.value)
const activeAvailableProfiles = computed(() => activeProviderState.value.availableProfiles.value)
const activeDefaultProfileId = computed(() => activeProviderState.value.defaultProfileId.value)
const activeSelectedProfile = computed(() => activeProviderState.value.selectedProfile.value)
const activeSelectedProfileId = computed({
  get: () => activeProviderState.value.selectedProfileId.value,
  set: (value: string | null) => activeProviderState.value.selectProfile(value),
})
const activeProfilesLoading = computed(() => activeProviderState.value.isLoading.value)
const activeProfilesLoaded = computed(() => activeProviderState.value.hasLoaded.value)
const activeProfilesError = computed(() => activeProviderState.value.errorMessage.value)
const activeProfilesErrorCode = computed(() => activeProviderState.value.errorCode.value)
const anyProfilesError = computed(() => profilesError.value || imageProviderProfiles.errorMessage.value || videoProviderProfiles.errorMessage.value)
const providerTabCounts = computed<Record<ProviderCapability, { configured: number; total: number }>>(() => ({
  asr: { configured: availableProfiles.value.length, total: profiles.value.length || 4 },
  image: { configured: imageProviderProfiles.availableProfiles.value.length, total: imageProviderProfiles.profiles.value.length || 4 },
  video: { configured: videoProviderProfiles.availableProfiles.value.length, total: videoProviderProfiles.profiles.value.length || 6 },
}))

const health = ref<HealthResponse | null>(null)
const healthLoading = ref(true)
const healthError = ref<string | null>(null)
const showNotifications = ref(false)
const showHelp = ref(false)

const configuredCount = computed(() => activeAvailableProfiles.value.length)
const selectedProfileName = computed(() => activeSelectedProfile.value?.label ?? '尚未选择')
const activeViewLabel = computed(() => activeView.value === 'overview' ? '运行概览' : activeView.value === 'provider' ? 'Provider 配置' : activeView.value === 'subtitle' ? '字幕任务' : activeView.value === 'tasks' ? '生产任务' : activeView.value === 'queue' ? '远程生产队列' : activeView.value === 'artifacts' ? '媒体资产' : activeView.value === 'workbench' ? '脚本与资产' : '运行日志')

function providerMark(provider: string): string {
  const normalized = provider.toLowerCase()
  if (normalized === 'mock') return 'M'
  if (normalized.includes('aliyun')) return '阿'
  if (normalized.includes('siliconflow')) return 'S'
  if (normalized.includes('comfyui')) return 'C'
  if (normalized.includes('openai')) return 'O'
  if (normalized.includes('ffmpeg')) return 'F'
  return provider.trim().slice(0, 1).toUpperCase() || '?'
}

async function refreshHealth() {
  healthLoading.value = true
  healthError.value = null
  try {
    health.value = await getHealth()
  } catch {
    health.value = null
    healthError.value = 'API 离线'
  } finally {
    healthLoading.value = false
  }
}

async function refreshAll() {
  await Promise.all([
    refreshProfiles(),
    imageProviderProfiles.refresh(),
    videoProviderProfiles.refresh(),
    refreshHealth(),
  ])
}

async function refreshActiveProvider() {
  await activeProviderState.value.refresh()
}

onMounted(() => {
  refreshHealth()
  window.addEventListener('popstate', syncLocation)
})
onUnmounted(() => window.removeEventListener('popstate', syncLocation))
</script>

<template>
  <CreatorHome v-if="activeSurface === 'creator'" @open-operator="openOperator" />
  <div v-else class="app-shell">
    <aside class="sidebar">
      <div class="brand-lockup">
        <div class="brand-mark">V</div>
        <div class="brand-copy">
          <strong>Video Forge</strong>
          <span>AI CONTENT PLATFORM</span>
        </div>
      </div>

      <div class="workspace-switch">
        <span class="workspace-avatar">A</span>
        <div class="workspace-copy">
          <strong>AI Video Generation</strong>
          <span>本地开发环境</span>
        </div>
        <span class="workspace-chevron">⌄</span>
      </div>

      <button class="workspace-mode-button" type="button" @click="openCreatorSurface">
        <span>↗</span>
        <span>查看用户前台</span>
      </button>

      <nav class="side-nav" aria-label="主导航">
        <p class="nav-label">工作台</p>
        <button class="nav-item" :class="{ active: activeView === 'overview' }" type="button" @click="setOperatorView('overview')">
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 13h6V4H4v9Zm0 7h6v-4H4v4Zm10 0h6v-9h-6v9Zm0-16v4h6V4h-6Z" /></svg>
          <span class="nav-text">概览</span>
        </button>
        <button class="nav-item" :class="{ active: activeView === 'tasks' }" type="button" @click="setOperatorView('tasks')">
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m8 5 11 7-11 7V5Zm-3 1h1v12H5V6Z" /></svg>
          <span class="nav-text">生产任务</span>
        </button>
        <button class="nav-item" :class="{ active: activeView === 'queue' }" type="button" @click="setOperatorView('queue')">
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 5h16v3H4V5Zm0 5.5h10v3H4v-3Zm0 5.5h16v3H4v-3Zm13-5.5 3 1.5-3 1.5v-3Z" /></svg>
          <span class="nav-text">远程队列</span>
        </button>
        <button class="nav-item" :class="{ active: activeView === 'subtitle' }" type="button" @click="setOperatorView('subtitle')">
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 5h16v10H8l-4 4V5Zm3 3v2h10V8H7Zm0 4v2h6v-2H7Z" /></svg>
          <span class="nav-text">字幕任务</span>
        </button>
        <button class="nav-item" :class="{ active: activeView === 'artifacts' }" type="button" @click="setOperatorView('artifacts')">
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 5.5A2.5 2.5 0 0 1 6.5 3h11A2.5 2.5 0 0 1 20 5.5v13a2.5 2.5 0 0 1-2.5 2.5h-11A2.5 2.5 0 0 1 4 18.5v-13ZM7 7v10h10V7H7Zm1.5 1.5h7v2h-7v-2Zm0 3.5h7v2h-7v-2Z" /></svg>
          <span class="nav-text">媒体资产</span>
        </button>
        <button class="nav-item" :class="{ active: activeView === 'workbench' }" type="button" @click="setOperatorView('workbench')">
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 5.5A2.5 2.5 0 0 1 6.5 3h11A2.5 2.5 0 0 1 20 5.5v13a2.5 2.5 0 0 1-2.5 2.5h-11A2.5 2.5 0 0 1 4 18.5v-13ZM7 7h4v4H7V7Zm6 0h4v4h-4V7ZM7 13h4v4H7v-4Zm6 0h4v4h-4v-4Z" /></svg>
          <span class="nav-text">脚本与资产</span>
        </button>

        <p class="nav-label nav-label-spaced">系统</p>
        <button class="nav-item" :class="{ active: activeView === 'provider' }" type="button" @click="setOperatorView('provider')">
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 8.2a3.8 3.8 0 1 0 0 7.6 3.8 3.8 0 0 0 0-7.6Zm0 2a1.8 1.8 0 1 1 0 3.6 1.8 1.8 0 0 1 0-3.6ZM19.4 13.2a7.8 7.8 0 0 0 0-2.4l1.6-1.2-2-3.4-1.9.8a8.4 8.4 0 0 0-2.1-1.2L14.7 4h-4l-.3 1.8a8.4 8.4 0 0 0-2.1 1.2l-1.9-.8-2 3.4L6 10.8a7.8 7.8 0 0 0 0 2.4l-1.6 1.2 2 3.4 1.9-.8a8.4 8.4 0 0 0 2.1 1.2l.3 1.8h4l.3-1.8a8.4 8.4 0 0 0 2.1-1.2l1.9.8 2-3.4-1.6-1.2Z" /></svg>
          <span class="nav-text">Provider 配置</span>
          <span v-if="activeView === 'provider'" class="nav-current">当前</span>
        </button>
        <button class="nav-item" :class="{ active: activeView === 'logs' }" type="button" @click="setOperatorView('logs')">
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3a9 9 0 1 0 9 9 9 9 0 0 0-9-9Zm0 2a7 7 0 1 1-7 7 7 7 0 0 1 7-7Zm-1 2v5.4l4 2.3 1-1.7-3-1.7V7h-2Z" /></svg>
          <span class="nav-text">运行日志</span>
          <span v-if="activeView === 'logs'" class="nav-current">当前</span>
        </button>
      </nav>

      <div class="sidebar-footer">
        <button class="help-row" type="button" @click="showHelp = true">
          <span class="help-icon">?</span>
          <span class="sidebar-footer-copy">帮助与文档</span>
        </button>
        <div class="user-row">
          <span class="user-avatar">Q</span>
          <div class="sidebar-footer-copy">
            <strong>本地管理员</strong>
            <span>开发工作区</span>
          </div>
          <span class="user-more">···</span>
        </div>
      </div>
    </aside>

    <div class="main-shell">
      <header class="top-header">
        <div class="breadcrumb"><span>工作台</span><b>/</b><strong>{{ activeViewLabel }}</strong></div>
        <div class="top-header-actions">
          <span class="api-status" :class="{ online: health, offline: healthError }">
            <span class="status-dot" />
            {{ healthLoading ? '连接中' : health ? `API 在线 · v${health.version}` : 'API 离线' }}
          </span>
          <div class="header-popover-anchor">
          <button class="icon-button" :class="{ active: showNotifications }" type="button" aria-label="通知" :aria-expanded="showNotifications" aria-controls="runtime-notifications" @click="showNotifications = !showNotifications">
            <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 21a2.4 2.4 0 0 0 2.3-1.8h-4.6A2.4 2.4 0 0 0 12 21Zm7-4H5l1.5-2V10a5.5 5.5 0 0 1 4.5-5.4V4a1 1 0 1 1 2 0v.6a5.5 5.5 0 0 1 4.5 5.4v5l1.5 2Z" /></svg>
          </button>
          <section v-if="showNotifications" id="runtime-notifications" class="header-popover" role="status" aria-label="系统通知">
            <div class="header-popover-heading"><strong>系统通知</strong><button type="button" aria-label="关闭通知" @click="showNotifications = false">×</button></div>
            <div class="header-notification-item" :class="health ? 'good' : 'warning'"><span>{{ health ? '✓' : '!' }}</span><div><strong>{{ health ? 'API 服务在线' : 'API 暂不可用' }}</strong><small>{{ health ? `当前版本 v${health.version}` : '请检查 Docker API 服务后重试' }}</small></div></div>
            <div v-if="anyProfilesError" class="header-notification-item warning"><span>!</span><div><strong>Provider 配置读取失败</strong><small>{{ anyProfilesError }}</small></div></div>
            <p v-else class="header-popover-empty">暂无新的系统通知</p>
          </section>
          </div>
          <button class="user-avatar top-avatar" type="button">Q</button>
        </div>
      </header>

      <nav class="mobile-operator-navigation" aria-label="手机后台导航">
        <label for="mobile-operator-view">工作台</label>
        <select id="mobile-operator-view" :value="activeView" @change="selectMobileView">
          <option value="overview">运行概览</option>
          <option value="tasks">生产任务</option>
          <option value="queue">远程队列</option>
          <option value="subtitle">字幕任务</option>
          <option value="artifacts">媒体资产</option>
          <option value="workbench">脚本与资产</option>
          <option value="provider">Provider 配置</option>
          <option value="logs">运行日志</option>
        </select>
        <button type="button" @click="openCreatorSurface">创作者前台 ↗</button>
      </nav>
      <main class="main-content">
        <RuntimeOverview v-if="activeView === 'overview'" @open-view="setOperatorView" @open-creator="openCreatorSurface" />

        <RunLog v-else-if="activeView === 'logs'" />

        <template v-else-if="activeView === 'provider'">
        <section class="page-header">
          <div>
            <p class="page-kicker">SYSTEM SETTINGS</p>
            <h1>Provider 配置</h1>
            <p>管理内容生产任务使用的 AI 服务。配置只在服务端生效，密钥不会暴露给浏览器。</p>
          </div>
          <button class="primary-button" type="button" @click="refreshAll">
            <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M19.4 8.1A8 8 0 0 0 5.2 6.4L3.5 8.1V3.5h4.6L6.5 5.1A6 6 0 0 1 17.6 7l1.8 1.1ZM4.6 15.9A8 8 0 0 0 18.8 17.6l1.7-1.7v4.6h-4.6l1.6-1.6A6 6 0 0 1 6.4 17l-1.8-1.1Z" /></svg>
            刷新配置
          </button>
        </section>

        <section v-if="activeProfilesError" class="alert-card error-card">
          <div>
            <strong>Provider 配置读取失败</strong>
            <p>{{ activeProfilesError }}<code v-if="activeProfilesErrorCode"> · {{ activeProfilesErrorCode }}</code></p>
          </div>
          <button class="secondary-button" type="button" @click="refreshActiveProvider">重试</button>
        </section>

        <section v-else-if="activeProfilesLoaded && configuredCount === 0" class="alert-card warning-card">
          <div>
            <strong>当前没有可用的{{ activeProviderMeta.label }}配置</strong>
            <p>可以先启用本地配置，或在 API / Worker 环境变量中补充真实供应商 Key。</p>
          </div>
        </section>

        <section class="provider-capability-tabs" aria-label="Provider 能力">
          <button
            v-for="tab in providerCapabilityTabs"
            :key="tab.id"
            class="provider-capability-tab"
            :class="{ active: activeProviderCapability === tab.id }"
            type="button"
            :aria-selected="activeProviderCapability === tab.id"
            role="tab"
            @click="activeProviderCapability = tab.id"
          >
            <span class="provider-capability-tab-icon">{{ tab.eyebrow.slice(0, 1) }}</span>
            <span class="provider-capability-tab-copy"><strong>{{ tab.label }}</strong><small>{{ tab.description }}</small></span>
            <span class="provider-capability-tab-count">{{ providerTabCounts[tab.id].configured }}/{{ providerTabCounts[tab.id].total }}</span>
          </button>
        </section>

        <section class="summary-grid" aria-label="运行摘要">
          <article class="summary-card">
            <div class="summary-icon blue"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 12a8 8 0 1 1 16 0h-2a6 6 0 1 0-12 0H4Zm2 1h3v6H6v-6Zm9 0h3v6h-3v-6Z" /></svg></div>
            <div><span>API 服务</span><strong>{{ health ? '运行正常' : healthError ? '连接失败' : '检查中' }}</strong><small>{{ health ? `版本 ${health.version}` : '等待服务响应' }}</small></div>
            <span class="summary-state" :class="{ good: health }">{{ health ? '在线' : '—' }}</span>
          </article>
          <article class="summary-card">
            <div class="summary-icon purple"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 5h16v3H4V5Zm0 5.5h16v3H4v-3ZM4 16h16v3H4v-3Z" /></svg></div>
            <div><span>{{ activeProviderMeta.label }}配置</span><strong>{{ configuredCount }} <em>/ {{ activeProfiles.length || activeProviderMeta.total }}</em></strong><small>{{ activeProviderMeta.description }}</small></div>
            <span class="summary-state" :class="{ good: configuredCount > 0 }">{{ configuredCount > 0 ? '可用' : '待配置' }}</span>
          </article>
          <article class="summary-card">
            <div class="summary-icon green"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3a9 9 0 1 0 9 9 9 9 0 0 0-9-9Zm0 2a7 7 0 1 1-7 7 7 7 0 0 1 7-7Zm-1 2v5.4l4 2.3 1-1.7-3-1.7V7h-2Z" /></svg></div>
            <div><span>当前默认</span><strong class="summary-provider">{{ selectedProfileName }}</strong><small>{{ activeDefaultProfileId ?? '等待配置加载' }}</small></div>
            <span class="summary-state" :class="{ good: activeDefaultProfileId }">{{ activeDefaultProfileId ? '默认' : '待配置' }}</span>
          </article>
        </section>

        <section class="content-grid">
          <article class="card current-card">
            <div class="card-header">
              <div><h2>当前运行配置</h2><p>选择后，下一次{{ activeProviderMeta.label }}任务会携带对应的 Profile ID。</p></div>
              <span class="status-pill" :class="{ neutral: !activeSelectedProfile }">{{ activeSelectedProfile ? '已就绪' : '待选择' }}</span>
            </div>

            <ProviderProfileSelect
              v-model="activeSelectedProfileId"
              :profiles="activeProfiles"
              :default-profile-id="activeDefaultProfileId"
              :disabled="activeProfilesLoading || activeProfiles.length === 0"
              :label="`${activeProviderMeta.label} Provider`"
              :select-id="`operator-${activeProviderCapability}-provider`"
            />

            <div v-if="activeProfilesLoading" class="loading-line"><span class="spinner" />正在读取服务端配置…</div>
            <div v-else-if="activeSelectedProfile" class="selected-profile">
              <div class="selected-heading"><div><span>已选择的配置</span><strong>{{ activeSelectedProfile.label }}</strong></div><span class="configured-tag">可用</span></div>
              <dl class="profile-detail-grid">
                <div><dt>Profile ID</dt><dd>{{ activeSelectedProfile.profile_id }}</dd></div>
                <div><dt>Provider</dt><dd>{{ activeSelectedProfile.provider }}</dd></div>
                <div><dt>模型</dt><dd>{{ activeSelectedProfile.model }}</dd></div>
                <div><dt>密钥来源</dt><dd>{{ activeSelectedProfile.api_key_env || '无需密钥' }}</dd></div>
              </dl>
              <div class="security-note"><span class="note-icon">✓</span><span>密钥仅由 API / Worker 从服务端环境变量读取，前端只接收安全元数据。</span></div>
            </div>
            <div v-else class="empty-state">选择一个已配置的 Provider 后，这里会显示运行参数。</div>
          </article>

          <article class="card guide-card">
            <div class="card-header"><div><h2>配置如何生效</h2><p>Provider Profile 将配置与任务解耦。</p></div><span class="guide-badge">运行机制</span></div>
            <ol class="steps-list">
              <li><span>1</span><div><strong>服务端准备密钥</strong><p>密钥放在 API 和 Worker 的环境变量中。</p></div></li>
              <li><span>2</span><div><strong>后台选择配置</strong><p>页面只展示可用状态和安全元数据。</p></div></li>
              <li><span>3</span><div><strong>任务记录 Profile ID</strong><p>Worker 按任务快照选择真实 Provider。</p></div></li>
            </ol>
            <div class="next-action"><span class="next-action-icon">→</span><div><strong>下一步</strong><p>打开创作者前台，创建项目并按阶段推进内容生产。</p></div><button class="next-action-button" type="button" @click="openCreatorSurface">打开前台 <span>↗</span></button></div>
          </article>
        </section>

        <section class="card profiles-card">
          <div class="card-header table-heading"><div><h2>{{ activeProviderMeta.label }} Provider 档案</h2><p>共 {{ activeProfiles.length }} 个内置档案，仅展示不含密钥的运行信息。</p></div><span class="table-count">{{ configuredCount }} 个可用</span></div>
          <div class="table-wrap">
            <table class="profile-table">
              <thead><tr><th>配置名称</th><th>Provider / 模型</th><th>密钥状态</th><th>默认</th><th class="action-column">操作</th></tr></thead>
              <tbody>
                <tr v-for="profile in activeProfiles" :key="profile.profile_id" :class="{ disabled: !profile.configured }">
                  <td><div class="table-profile"><span class="provider-logo" :class="profile.provider">{{ providerMark(profile.provider) }}</span><div><strong>{{ profile.label }}</strong><small>{{ profile.profile_id }}</small></div></div></td>
                  <td><div class="provider-cell"><strong>{{ profile.provider }}</strong><small>{{ profile.model }}</small></div></td>
                  <td><span class="table-status" :class="profile.configured ? 'ready' : 'not-ready'"><i />{{ profile.configured ? '已配置' : '未配置' }}</span></td>
                  <td><span v-if="profile.profile_id === activeDefaultProfileId" class="default-label">默认配置</span><span v-else class="muted-label">—</span></td>
                  <td class="action-column"><button v-if="profile.configured && profile.profile_id !== activeSelectedProfileId" class="table-action" type="button" @click="activeSelectedProfileId = profile.profile_id">使用此配置</button><span v-else-if="profile.profile_id === activeSelectedProfileId" class="selected-action">当前使用</span><span v-else class="muted-label">需配置 Key</span></td>
                </tr>
                <tr v-if="!activeProfilesLoading && activeProfiles.length === 0 && !activeProfilesError"><td colspan="5" class="empty-table">暂无 Provider Profile。</td></tr>
              </tbody>
            </table>
          </div>
        </section>

        <section class="footer-note"><span class="footer-note-icon">i</span><span>当前页面属于内部运营控制台。普通用户前台将只看到“创建视频、选择脚本、查看进度和预览成片”，不会看到 Provider、模型或环境变量。</span></section>
        </template>

        <SubtitleTaskView v-else-if="activeView === 'subtitle'" @submitted="setOperatorView('tasks')" />
        <TaskCenter v-else-if="activeView === 'tasks'" />
        <ProductionQueue v-else-if="activeView === 'queue'" />
        <ArtifactLibrary v-else-if="activeView === 'artifacts'" @open-tasks="setOperatorView('tasks')" />
        <ScriptAssetWorkbench v-else />
      </main>
    </div>
    <div v-if="showHelp" class="operator-modal-backdrop" role="presentation" @click.self="showHelp = false">
      <section class="operator-help-modal" role="dialog" aria-modal="true" aria-labelledby="operator-help-title">
        <div class="operator-modal-heading"><div><p class="page-kicker">QUICK GUIDE</p><h2 id="operator-help-title">帮助与快速使用</h2><p>这个工作区分为用户前台和内部制作后台两部分。</p></div><button class="operator-modal-close" type="button" aria-label="关闭帮助" @click="showHelp = false">×</button></div>
        <div class="operator-help-grid">
          <article><span>01</span><div><strong>创建视频</strong><p>从创作者前台上传小说或输入原文，按阶段生成故事设定、分集、剧本和分镜。</p></div></article>
          <article><span>02</span><div><strong>人工审核</strong><p>在资产、分镜、声音和字幕阶段确认结果，再提交视频片段和成片任务。</p></div></article>
          <article><span>03</span><div><strong>后台运维</strong><p>生产任务、远程队列、媒体资产和 Provider 配置仅供本地管理员使用。</p></div></article>
        </div>
        <div class="operator-help-note"><span>i</span><p>运行日志会按时间汇总任务状态和阶段尝试；需要执行重试或创建批次时，请在“生产任务”中操作。</p></div>
        <div class="operator-modal-actions"><button class="secondary-button" type="button" @click="showHelp = false">关闭</button><button class="primary-button" type="button" @click="openHelpCreator">打开创作者前台 <span>↗</span></button></div>
      </section>
    </div>
  </div>
</template>

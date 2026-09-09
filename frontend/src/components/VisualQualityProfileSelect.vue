<script setup lang="ts">
import { computed } from 'vue'
import type { VisualQualityProfile } from '../types/provider'

const props = withDefaults(defineProps<{
  modelValue: string | null
  profiles: VisualQualityProfile[]
  defaultProfileId: string | null
  disabled?: boolean
}>(), {
  disabled: false,
})

const emit = defineEmits<{
  'update:modelValue': [value: string | null]
}>()

const selectedProfile = computed(() =>
  props.profiles.find((profile) => profile.profile_id === props.modelValue) ?? null,
)

function selectProfile(profileId: string) {
  if (props.disabled || !props.profiles.some((profile) => profile.profile_id === profileId)) return
  emit('update:modelValue', profileId)
}

function moveSelection(event: KeyboardEvent, index: number) {
  if (props.disabled || !props.profiles.length) return
  let next = index
  if (event.key === 'ArrowRight' || event.key === 'ArrowDown') next = (index + 1) % props.profiles.length
  else if (event.key === 'ArrowLeft' || event.key === 'ArrowUp') next = (index - 1 + props.profiles.length) % props.profiles.length
  else if (event.key === 'Home') next = 0
  else if (event.key === 'End') next = props.profiles.length - 1
  else return
  event.preventDefault()
  selectProfile(props.profiles[next].profile_id)
  const group = (event.currentTarget as HTMLElement).parentElement
  group?.querySelectorAll<HTMLButtonElement>('[role="radio"]')[next]?.focus()
}
</script>

<template>
  <div class="provider-selector visual-quality-selector">
    <div class="visual-quality-selector-heading">
      <div>
        <p class="visual-quality-eyebrow">VISUAL QUALITY</p>
        <p id="creator-visual-quality-label" class="field-label">视觉质量档案</p>
        <p class="visual-quality-caption">一键选择一套可复现的分辨率、采样和身份参数</p>
      </div>
      <span v-if="selectedProfile" class="visual-quality-current-badge">
        {{ selectedProfile.profile_id === props.defaultProfileId ? '默认档案' : '已选择' }}
      </span>
    </div>

    <div
      class="visual-quality-options"
      role="radiogroup"
      aria-labelledby="creator-visual-quality-label"
      :aria-disabled="props.disabled"
    >
      <button
        v-for="(profile, index) in props.profiles"
        :key="profile.profile_id"
        class="visual-quality-option"
        :class="{ selected: profile.profile_id === props.modelValue, default: profile.profile_id === props.defaultProfileId }"
        type="button"
        role="radio"
        :aria-checked="profile.profile_id === props.modelValue"
        :aria-label="`${profile.label}，${profile.recommended_for}`"
        :disabled="props.disabled"
        :tabindex="profile.profile_id === props.modelValue || (!selectedProfile && index === 0) ? 0 : -1"
        @click="selectProfile(profile.profile_id)"
        @keydown="moveSelection($event, index)"
      >
        <span class="visual-quality-option-top">
          <span class="visual-quality-option-index">{{ String(index + 1).padStart(2, '0') }}</span>
          <span class="visual-quality-option-title">
            <strong>{{ profile.label }}</strong>
            <small v-if="profile.profile_id === props.defaultProfileId">默认</small>
          </span>
          <span v-if="profile.profile_id === props.modelValue" class="visual-quality-option-check" aria-hidden="true">✓</span>
        </span>
        <span class="visual-quality-option-recommended">{{ profile.recommended_for }}</span>
        <span class="visual-quality-option-metrics">
          <span><b>图</b>{{ profile.image_width }}×{{ profile.image_height }}</span>
          <span><b>视频</b>{{ profile.video_width }}×{{ profile.video_height }}</span>
          <span><b>画幅</b>{{ profile.aspect_ratio ?? '9:16' }}</span>
          <span><b>帧率</b>{{ profile.video_fps }} fps</span>
          <span><b>采样</b>{{ profile.image_steps }}/{{ profile.video_steps }}</span>
        </span>
        <span class="visual-quality-option-description">{{ profile.description }}</span>
      </button>
    </div>

    <div v-if="selectedProfile" class="visual-quality-selected-summary">
      <span class="visual-quality-selected-mark">✓</span>
      <div>
        <strong>已选择 {{ selectedProfile.label }}</strong>
        <p>
          图像 {{ selectedProfile.image_steps }} steps / guidance {{ selectedProfile.image_guidance }} ·
          视频 {{ selectedProfile.video_steps }} steps / CFG {{ selectedProfile.video_cfg }} ·
          身份权重 {{ Math.round(selectedProfile.image_identity_weight * 100) }}% ·
          画幅 {{ selectedProfile.aspect_ratio ?? '9:16' }}
        </p>
      </div>
      <small>任务创建后会保存完整快照</small>
    </div>

    <div v-if="selectedProfile" class="quality-choice-guidance" role="status">
      <span class="quality-choice-kicker">下一步怎么做</span>
      <strong v-if="selectedProfile.profile_id === 'local_safe'">先确认一张人设图和一个短镜头</strong>
      <strong v-else-if="selectedProfile.profile_id === 'local_balanced'">用同一人物试拍两个不同景别</strong>
      <strong v-else>先对比关键镜头，再决定是否批量使用</strong>
      <p>确认脸型、服装、动作和声音满意后，再生成整集。提高分辨率会增加资源消耗；更多采样步数不保证效果更好。</p>
      <details>
        <summary>这些参数分别影响什么？</summary>
        <dl>
          <div><dt>分辨率与帧率</dt><dd>决定画面尺寸和每秒帧数。放大成片无法补回原始镜头缺失的细节。</dd></div>
          <div><dt>采样与引导强度</dt><dd>影响生成过程和对描述的遵循程度。不同模型的合适范围不同，应固定镜头逐项比较。</dd></div>
          <div><dt>身份权重</dt><dd>控制参考人设对生成的影响，并非角色一致性的成功概率；过强也可能限制表情和构图。</dd></div>
        </dl>
      </details>
    </div>
  </div>
</template>

<style scoped>
.quality-choice-guidance { margin-top: 16px; padding: 20px 22px; border: 1px solid #dde4f0; border-radius: 14px; background: linear-gradient(120deg, #f3f6ff, #fcfdff); color: #35445e; }
.quality-choice-kicker { display: block; margin-bottom: 8px; color: #6265ad; font-size: 12px; font-weight: 600; }
.quality-choice-guidance strong { font-size: 16px; line-height: 1.5; }
.quality-choice-guidance p, .quality-choice-guidance dd { font-size: 13px; line-height: 1.8; color: #58677f; }
.quality-choice-guidance p { margin: 8px 0 14px; }
.quality-choice-guidance summary { cursor: pointer; font-size: 13px; font-weight: 600; color: #5253ac; }
.quality-choice-guidance dl { display: grid; gap: 12px; margin: 16px 0 0; }
.quality-choice-guidance dt { font-size: 13px; font-weight: 600; }
.quality-choice-guidance dd { margin: 3px 0 0; }
.visual-quality-option:focus-visible, summary:focus-visible { outline: 3px solid #7678e4; outline-offset: 4px; }
@media (prefers-reduced-motion: reduce) { .visual-quality-option { transition: none; } }
</style>

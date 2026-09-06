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
        @click="selectProfile(profile.profile_id)"
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
          身份权重 {{ Math.round(selectedProfile.image_identity_weight * 100) }}%
        </p>
      </div>
      <small>任务创建后会保存完整快照</small>
    </div>
  </div>
</template>

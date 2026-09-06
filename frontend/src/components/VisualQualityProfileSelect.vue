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

function onChange(event: Event) {
  const value = (event.target as HTMLSelectElement).value
  emit('update:modelValue', value || null)
}
</script>

<template>
  <div class="provider-selector visual-quality-selector">
    <label class="field-label" for="creator-visual-quality">视觉质量档案</label>
    <select
      id="creator-visual-quality"
      class="provider-select"
      :value="props.modelValue ?? ''"
      :disabled="props.disabled"
      @change="onChange"
    >
      <option value="" disabled>请选择质量档案</option>
      <option v-for="profile in props.profiles" :key="profile.profile_id" :value="profile.profile_id">
        {{ profile.label }}{{ profile.profile_id === props.defaultProfileId ? ' · 默认' : '' }}
      </option>
    </select>
    <p v-if="selectedProfile" class="field-hint">
      {{ selectedProfile.description }} · {{ selectedProfile.image_width }}×{{ selectedProfile.image_height }} 图 / {{ selectedProfile.video_width }}×{{ selectedProfile.video_height }} 视频
    </p>
  </div>
</template>

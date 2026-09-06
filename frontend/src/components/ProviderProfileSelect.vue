<script setup lang="ts">
import type { ProviderProfile } from '../types/provider'

const props = withDefaults(defineProps<{
  modelValue: string | null
  profiles: ProviderProfile[]
  defaultProfileId: string | null
  disabled?: boolean
  label?: string
  selectId?: string
  hint?: string
}>(), {
  disabled: false,
  label: 'Provider 配置',
  selectId: 'provider-profile',
  hint: '只展示后端允许的配置；API Key 仍保存在服务端环境变量中。',
})

const emit = defineEmits<{
  'update:modelValue': [value: string | null]
}>()

function onChange(event: Event) {
  const value = (event.target as HTMLSelectElement).value
  emit('update:modelValue', value || null)
}
</script>

<template>
  <div class="provider-selector">
    <label class="field-label" :for="props.selectId">{{ props.label }}</label>
    <select
      :id="props.selectId"
      class="provider-select"
      :value="modelValue ?? ''"
      :disabled="disabled"
      @change="onChange"
    >
      <option value="" disabled>请选择可用配置</option>
      <option
        v-for="profile in profiles"
        :key="profile.profile_id"
        :value="profile.profile_id"
        :disabled="!profile.configured"
      >
        {{ profile.label }}{{ profile.profile_id === defaultProfileId ? ' · 默认' : '' }}{{ !profile.configured ? ' · 未配置' : '' }}
      </option>
    </select>
    <p class="field-hint">{{ props.hint }}</p>
  </div>
</template>

import { computed, onMounted, ref } from 'vue'
import { getVisualQualityProfiles } from '../api/visualQualityProfiles'
import type { VisualQualityProfile } from '../types/provider'
import { ApiClientError } from '../api/client'

export function useVisualQualityProfiles() {
  const profiles = ref<VisualQualityProfile[]>([])
  const defaultProfileId = ref<string | null>(null)
  const selectedProfileId = ref<string | null>(null)
  const isLoading = ref(false)
  const errorMessage = ref<string | null>(null)

  const selectedProfile = computed(() =>
    profiles.value.find((profile) => profile.profile_id === selectedProfileId.value) ?? null,
  )

  async function refresh() {
    isLoading.value = true
    errorMessage.value = null
    try {
      const response = await getVisualQualityProfiles()
      profiles.value = response.items
      defaultProfileId.value = response.default_profile_id
      selectedProfileId.value = response.items.some(
        (profile) => profile.profile_id === response.default_profile_id,
      )
        ? response.default_profile_id
        : response.items[0]?.profile_id ?? null
    } catch (error) {
      if (error instanceof ApiClientError) errorMessage.value = error.message
      else errorMessage.value = '无法读取视觉质量档案，请确认后端 API 已启动。'
    } finally {
      isLoading.value = false
    }
  }

  function selectProfile(profileId: string | null) {
    if (profiles.value.some((profile) => profile.profile_id === profileId)) {
      selectedProfileId.value = profileId
    }
  }

  onMounted(refresh)

  return {
    profiles,
    defaultProfileId,
    selectedProfileId,
    selectedProfile,
    isLoading,
    errorMessage,
    refresh,
    selectProfile,
  }
}

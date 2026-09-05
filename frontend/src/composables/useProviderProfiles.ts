import { computed, onMounted, ref } from 'vue'
import { getProviderProfiles } from '../api/providerProfiles'
import type { ProviderProfile } from '../types/provider'
import { ApiClientError } from '../api/client'

export function useProviderProfiles() {
  const profiles = ref<ProviderProfile[]>([])
  const defaultProfileId = ref<string | null>(null)
  const selectedProfileId = ref<string | null>(null)
  const isLoading = ref(false)
  const hasLoaded = ref(false)
  const errorMessage = ref<string | null>(null)
  const errorCode = ref<string | null>(null)

  const availableProfiles = computed(() => profiles.value.filter((profile) => profile.configured))
  const selectedProfile = computed(() =>
    profiles.value.find((profile) => profile.profile_id === selectedProfileId.value) ?? null,
  )

  async function refresh() {
    isLoading.value = true
    errorMessage.value = null
    errorCode.value = null
    try {
      const response = await getProviderProfiles()
      profiles.value = response.items
      defaultProfileId.value = response.default_profile_id
      const configuredDefault = response.items.find(
        (profile) => profile.profile_id === response.default_profile_id && profile.configured,
      )
      selectedProfileId.value = configuredDefault?.profile_id ?? response.items.find((profile) => profile.configured)?.profile_id ?? null
      hasLoaded.value = true
    } catch (error) {
      hasLoaded.value = false
      if (error instanceof ApiClientError) {
        errorCode.value = error.code
        errorMessage.value = error.message
      } else {
        errorMessage.value = '无法连接后端 API，请确认 API 已启动。'
      }
    } finally {
      isLoading.value = false
    }
  }

  function selectProfile(profileId: string | null) {
    const profile = profiles.value.find((item) => item.profile_id === profileId)
    if (profile?.configured) {
      selectedProfileId.value = profile.profile_id
    }
  }

  onMounted(refresh)

  return {
    profiles,
    availableProfiles,
    defaultProfileId,
    selectedProfile,
    selectedProfileId,
    isLoading,
    hasLoaded,
    errorMessage,
    errorCode,
    refresh,
    selectProfile,
  }
}

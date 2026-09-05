import type { ApiErrorBody } from '../types/provider'

const configuredBaseUrl = (import.meta.env.VITE_API_BASE_URL as string | undefined) ?? ''
const apiBaseUrl = configuredBaseUrl.replace(/\/$/, '')

export class ApiClientError extends Error {
  readonly code: string
  readonly status: number
  readonly requestId: string | null
  readonly details: Record<string, unknown>

  constructor(
    message: string,
    code: string,
    status: number,
    requestId: string | null = null,
    details: Record<string, unknown> = {},
  ) {
    super(message)
    this.name = 'ApiClientError'
    this.code = code
    this.status = status
    this.requestId = requestId
    this.details = details
  }
}

export async function apiRequest<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers)
  if (init.body && !(init.body instanceof FormData) && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json')
  }

  const response = await fetch(`${apiBaseUrl}${path}`, {
    ...init,
    headers,
  })
  const payload = (await response.json().catch(() => null)) as ApiErrorBody | T | null

  if (!response.ok) {
    const error = payload as ApiErrorBody | null
    throw new ApiClientError(
      error?.error?.message ?? `请求失败（HTTP ${response.status}）`,
      error?.error?.code ?? 'HTTP_ERROR',
      response.status,
      error?.error?.request_id ?? response.headers.get('X-Request-ID'),
      error?.error?.details ?? {},
    )
  }

  return payload as T
}

export interface HealthResponse {
  status: string
  version: string
}

export function getHealth(): Promise<HealthResponse> {
  return apiRequest<HealthResponse>('/healthz')
}

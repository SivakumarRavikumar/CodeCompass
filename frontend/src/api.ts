import type { Answer, Job, Purge, Repository, Trace, UploadResult } from './types'

const API_ROOT = '/api/v1'

export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
    public details?: unknown,
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

async function parseResponse<T>(response: Response): Promise<T> {
  const contentType = response.headers.get('content-type') ?? ''
  const body = contentType.includes('application/json')
    ? await response.json()
    : await response.text()
  if (!response.ok) {
    const message =
      typeof body === 'object' && body && 'detail' in body
        ? String((body as { detail: unknown }).detail)
        : `Request failed (${response.status})`
    throw new ApiError(message, response.status, body)
  }
  return body as T
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers)
  if (init?.body && !(init.body instanceof FormData)) headers.set('Content-Type', 'application/json')
  headers.set('Accept', 'application/json')
  const response = await fetch(`${API_ROOT}${path}`, { ...init, headers })
  return parseResponse<T>(response)
}

export const api = {
  listRepositories: async () => {
    const result = await request<Repository[] | { repositories: Repository[] }>('/repositories')
    return Array.isArray(result) ? result : result.repositories
  },
  getRepository: (id: string) => request<Repository>(`/repositories/${encodeURIComponent(id)}`),
  getJob: (id: string) => request<Job>(`/jobs/${encodeURIComponent(id)}`),
  cancelJob: (id: string) => request<Job>(`/jobs/${encodeURIComponent(id)}/cancel`, { method: 'POST' }),
  askQuestion: (id: string, question: string) =>
    request<Answer>(`/repositories/${encodeURIComponent(id)}/questions`, {
      method: 'POST',
      body: JSON.stringify({ question }),
    }),
  getTrace: (id: string) => request<Trace>(`/traces/${encodeURIComponent(id)}`),
  deleteRepository: (id: string) =>
    request<Purge | { purge: Purge }>(`/repositories/${encodeURIComponent(id)}`, {
      method: 'DELETE',
    }).then((value) => ('purge' in value ? value.purge : value)),
  getPurge: (id: string) => request<Purge>(`/purges/${encodeURIComponent(id)}`),
}

export function uploadRepository(
  displayName: string,
  file: File,
  idempotencyKey: string,
  onProgress: (value: number) => void,
): Promise<UploadResult> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    xhr.open('POST', `${API_ROOT}/repositories`)
    xhr.setRequestHeader('Accept', 'application/json')
    xhr.setRequestHeader('Idempotency-Key', idempotencyKey)
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable) onProgress(Math.round((event.loaded / event.total) * 100))
    }
    xhr.onerror = () => reject(new ApiError('Network error while uploading. Your retry key was preserved.', 0))
    xhr.onload = () => {
      let body: unknown
      try {
        body = JSON.parse(xhr.responseText)
      } catch {
        body = xhr.responseText
      }
      if (xhr.status >= 200 && xhr.status < 300) {
        onProgress(100)
        resolve(body as UploadResult)
      } else {
        const detail = typeof body === 'object' && body && 'detail' in body
          ? String((body as { detail: unknown }).detail)
          : `Upload failed (${xhr.status})`
        reject(new ApiError(detail, xhr.status, body))
      }
    }
    const form = new FormData()
    form.append('display_name', displayName)
    form.append('file', file)
    xhr.send(form)
  })
}

export function newIdempotencyKey(): string {
  return globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(36).slice(2)}`
}

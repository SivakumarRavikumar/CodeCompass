import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from './api'

describe('API client', () => {
  beforeEach(() => vi.restoreAllMocks())

  it('normalizes wrapped repository lists', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(
      JSON.stringify({ repositories: [{ id: 'r1', display_name: 'Core', availability: 'available', indexing_status: 'ready' }] }),
      { status: 200, headers: { 'content-type': 'application/json' } },
    )))
    await expect(api.listRepositories()).resolves.toHaveLength(1)
    expect(fetch).toHaveBeenCalledWith('/api/v1/repositories', expect.objectContaining({
      headers: expect.any(Headers),
    }))
  })

  it('sends buffered question JSON', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(
      JSON.stringify({ outcome: 'answered', answer: 'A', citations: [] }),
      { status: 200, headers: { 'content-type': 'application/json' } },
    )))
    await api.askQuestion('repo/a', 'Where?')
    expect(fetch).toHaveBeenCalledWith('/api/v1/repositories/repo%2Fa/questions', expect.objectContaining({
      method: 'POST',
      body: JSON.stringify({ question: 'Where?' }),
    }))
  })

  it('surfaces API detail and status', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(
      JSON.stringify({ detail: 'Repository is not ready' }),
      { status: 409, headers: { 'content-type': 'application/json' } },
    )))
    await expect(api.getRepository('r1')).rejects.toEqual(
      expect.objectContaining({ message: 'Repository is not ready', status: 409 }),
    )
  })
})

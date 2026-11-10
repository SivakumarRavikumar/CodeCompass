import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import App from './App'

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } })
}

describe('CodeCompass', () => {
  beforeEach(() => {
    vi.useRealTimers()
    vi.restoreAllMocks()
  })

  it('shows the intentional empty state', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(json([])))
    render(<App />)
    expect(await screen.findByText('Ask your codebase.')).toBeInTheDocument()
    expect(screen.getByText('Get answers with proof.')).toBeInTheDocument()
  })

  it('keeps availability and indexing status separate', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(json([{
      id: 'r1', display_name: 'Billing', availability: 'available', indexing_status: 'processing',
    }])))
    const user = userEvent.setup()
    render(<App />)
    expect((await screen.findAllByText('Billing')).length).toBeGreaterThan(0)
    expect(screen.getByText('available')).toBeInTheDocument()
    expect(screen.getByText('processing')).toBeInTheDocument()
    await user.click((await screen.findAllByText('Billing'))[1])
    expect(screen.getByLabelText(/Ask a question/)).toBeInTheDocument()
    expect(screen.getByText('Building your code map')).toBeInTheDocument()
  })

  it('shows lexical-only degradation without blocking the ready workspace', async () => {
    const repository = {
      id: 'r1',
      display_name: 'Billing',
      availability: 'available',
      indexing_status: 'ready',
      degradation: 'lexical-only: ConnectionError',
    }
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(json([repository])))
    const user = userEvent.setup()
    render(<App />)
    await user.click((await screen.findAllByText('Billing'))[1])
    expect(screen.getByText('Reduced retrieval quality')).toBeInTheDocument()
    expect(screen.getByText(/lexical-only: ConnectionError/)).toBeInTheDocument()
    expect(screen.getByLabelText(/Ask a question/)).toBeInTheDocument()
  })

  it('renders explicit insufficient-evidence state', async () => {
    const repository = { id: 'r1', display_name: 'Billing', availability: 'available', indexing_status: 'ready' }
    vi.stubGlobal('fetch', vi.fn()
      .mockResolvedValueOnce(json([repository]))
      .mockResolvedValueOnce(json({ outcome: 'insufficient_evidence', message: 'No matching implementation.', citations: [] })))
    const user = userEvent.setup()
    render(<App />)
    await user.click((await screen.findAllByText('Billing'))[1])
    await user.type(screen.getByLabelText(/Ask a question/), 'Where is settlement?')
    await user.click(screen.getByLabelText('Ask question'))
    expect(await screen.findByText('Insufficient evidence')).toBeInTheDocument()
    expect(screen.getByText('No matching implementation.')).toBeInTheDocument()
  })

  it('opens a sanitized source citation', async () => {
    const repository = { id: 'r1', display_name: 'Core', availability: 'available', indexing_status: 'ready' }
    vi.stubGlobal('fetch', vi.fn()
      .mockResolvedValueOnce(json([repository]))
      .mockResolvedValueOnce(json({
        outcome: 'answered',
        answer: '<img src=x onerror=alert(1)> **Found it.**',
        citations: [{ path: 'src/core.ts', start_line: 4, end_line: 8, snippet: 'export const core = true' }],
      })))
    const user = userEvent.setup()
    render(<App />)
    await user.click((await screen.findAllByText('Core'))[1])
    await user.type(screen.getByLabelText(/Ask a question/), 'Where?')
    await user.click(screen.getByLabelText('Ask question'))
    expect(await screen.findByText('Found it.')).toBeInTheDocument()
    expect(document.querySelector('img')).not.toBeInTheDocument()
    await user.click(screen.getByText('src/core.ts'))
    await waitFor(() => expect(screen.getByText('export const core = true')).toBeInTheDocument())
  })

  it('continues deletion polling after a transient failure', async () => {
    const repository = {
      id: 'r1',
      display_name: 'Core',
      availability: 'available',
      indexing_status: 'ready',
    }
    let purgeRequests = 0
    vi.stubGlobal('fetch', vi.fn().mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.endsWith('/repositories') && !init?.method) return Promise.resolve(json([repository]))
      if (url.endsWith('/repositories/r1') && init?.method === 'DELETE') {
        return Promise.resolve(json({ id: 'p1', status: 'queued', progress: 0 }))
      }
      if (url.endsWith('/purges/p1')) {
        purgeRequests += 1
        if (purgeRequests === 1) return Promise.reject(new Error('Temporary polling failure'))
        return Promise.resolve(json({ id: 'p1', status: 'completed', progress: 100 }))
      }
      return Promise.reject(new Error(`Unexpected request: ${url}`))
    }))
    const user = userEvent.setup()
    render(<App />)
    await user.click((await screen.findAllByText('Core'))[1])
    await user.click(screen.getByLabelText('Delete repository'))
    await user.type(screen.getByLabelText(/Type Core/), 'Core')
    await user.click(screen.getAllByRole('button', { name: 'Delete repository' }).at(-1)!)

    expect(await screen.findByText('Temporary polling failure', {}, { timeout: 2500 })).toBeInTheDocument()
    expect(await screen.findByText('Deletion complete', {}, { timeout: 2500 })).toBeInTheDocument()
    expect(purgeRequests).toBe(2)
  }, 7000)
})

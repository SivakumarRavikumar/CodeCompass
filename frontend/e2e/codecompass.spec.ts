import { expect, test } from '@playwright/test'

test('asks a question and inspects cited evidence', async ({ page }, testInfo) => {
  const repository = { id: 'r1', display_name: 'Checkout service', availability: 'available', indexing_status: 'ready' }
  await page.route('**/api/v1/repositories', (route) => route.fulfill({ json: [repository] }))
  await page.route('**/api/v1/repositories/r1/questions', (route) => route.fulfill({
    json: {
      outcome: 'answered',
      answer: 'Checkout is orchestrated by `CheckoutService`.',
      citations: [{ id: 'chunk-1', path: 'src/checkout.ts', start_line: 12, end_line: 21, snippet: 'export class CheckoutService {}', redacted: true }],
      trace_id: 't1',
    },
  }))
  await page.route('**/api/v1/traces/t1', (route) => route.fulfill({
    json: {
      id: 't1',
      query: 'How does checkout work?',
      duration_ms: 34,
      candidates: 8,
      retrieved: [{ path: 'src/checkout.ts', score: 0.94 }],
      stages: [{ name: 'retrieve', detail: 'hybrid' }],
    },
  }))

  await page.goto('/')
  await page.getByRole('button', { name: /^Checkout service Uploaded/ }).click()
  await page.getByLabel('Ask a question about this repository').fill('How does checkout work?')
  await page.getByLabel('Ask question').click()
  await expect(page.getByText('Checkout is orchestrated by')).toBeVisible()
  await page.getByRole('button', { name: 'Trace' }).click()
  await expect(page.getByText('hybrid')).toBeVisible()
  await expect(page.getByText('34 ms')).toBeVisible()
  await page.getByText('src/checkout.ts').click()
  await expect(page.getByText('export class CheckoutService {}')).toBeVisible()
  if (testInfo.project.name === 'chromium') {
    await page.screenshot({
      path: '../demo/screenshots/cited-answer.png',
      fullPage: true,
    })
  }
})

test('shows an evidence abstention instead of inventing an answer', async ({ page }, testInfo) => {
  await page.route('**/api/v1/repositories', (route) => route.fulfill({
    json: [{ id: 'r2', display_name: 'API', availability: 'available', indexing_status: 'ready' }],
  }))
  await page.route('**/api/v1/repositories/r2/questions', (route) => route.fulfill({
    json: { outcome: 'insufficient_evidence', message: 'No relevant source was found.', citations: [], trace_id: 't2' },
  }))
  await page.goto('/')
  await page.getByRole('button', { name: /^API Uploaded/ }).click()
  await page.getByLabel('Ask a question about this repository').fill('How is billing implemented?')
  await page.getByLabel('Ask question').click()
  await expect(page.getByText('Insufficient evidence')).toBeVisible()
  if (testInfo.project.name === 'chromium') {
    await page.screenshot({
      path: '../demo/screenshots/abstention.png',
      fullPage: true,
    })
  }
})

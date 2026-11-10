import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const nginx = readFileSync(resolve(process.cwd(), 'nginx.conf'), 'utf8')
const compose = readFileSync(resolve(process.cwd(), '../docker-compose.yml'), 'utf8')

describe('local deployment security boundary', () => {
  it('rejects unknown hosts and applies restrictive browser headers', () => {
    expect(nginx).toContain('listen 80 default_server;')
    expect(nginx).toContain('return 444;')
    expect(nginx).toContain('server_name localhost 127.0.0.1;')
    expect(nginx).toContain("default-src 'self'")
    expect(nginx).toContain("object-src 'none'")
    expect(nginx).toContain("frame-ancestors 'none'")
    expect(nginx).toContain('X-Content-Type-Options "nosniff"')
    expect(nginx).toContain('Referrer-Policy "no-referrer"')
    expect(nginx).toContain('Permissions-Policy')
    expect(compose).toContain('"127.0.0.1:8000:8000"')
    expect(compose).toContain('"127.0.0.1:8080:80"')
  })
})

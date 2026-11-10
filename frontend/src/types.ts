export type Availability = 'available' | 'unavailable' | 'deleting'
export type IndexingStatus = 'queued' | 'processing' | 'ready' | 'failed' | 'cancelled'

export interface Repository {
  id: string
  display_name: string
  availability: Availability
  indexing_status: IndexingStatus
  created_at?: string
  snapshot_id?: string | null
  active_snapshot_id?: string | null
  pending_snapshot_id?: string | null
  job_id?: string
  error?: string | null
  file_name?: string
  degradation?: string | null
}

export interface Job {
  id: string
  status: IndexingStatus
  progress?: number
  phase?: string
  error?: string | null
}

export interface Citation {
  id?: string
  path: string
  start_line?: number
  end_line?: number
  snippet?: string
  redacted?: boolean
}

export interface Trace {
  id: string
  query?: string
  duration_ms?: number
  candidates?: number
  retrieved?: Array<{
    path: string
    score?: number
    start_line?: number
    end_line?: number
  }>
  stages?: Array<{ name: string; duration_ms?: number; detail?: string }>
}

export interface Answer {
  outcome: 'answered' | 'insufficient_evidence'
  answer?: string
  message?: string
  citations: Citation[]
  trace?: Trace | { id: string }
  trace_id?: string
}

export interface Purge {
  id: string
  status: 'queued' | 'processing' | 'completed' | 'failed'
  progress?: number
  error?: string | null
}

export interface UploadResult {
  repository: Repository
  snapshot: { id: string }
  job: Job
}

import { useCallback, useEffect, useRef, useState } from 'react'
import {
  AlertCircle, Archive, ArrowLeft, BookOpen, Check, ChevronRight, CircleStop,
  Clock3, Code2, FileArchive, FileCode2, LoaderCircle, Menu, Plus, Search,
  Send, ShieldCheck, Sparkles, Trash2, UploadCloud, X,
} from 'lucide-react'
import { api, newIdempotencyKey, uploadRepository } from './api'
import { Markdown } from './Markdown'
import type { Answer, Citation, Job, Purge, Repository, Trace } from './types'

const terminal = new Set(['ready', 'failed', 'cancelled'])

function StatusPill({ kind, value }: { kind: 'availability' | 'indexing'; value: string }) {
  const good = value === 'available' || value === 'ready' || value === 'completed'
  const bad = ['unavailable', 'failed', 'cancelled'].includes(value)
  return (
    <span className={`status ${good ? 'good' : bad ? 'bad' : 'working'}`}>
      <span className="status-dot" aria-hidden="true" />
      <span className="status-label">{kind === 'availability' ? 'Access' : 'Index'}</span>
      {value.replace('_', ' ')}
    </span>
  )
}

function EmptyState({ onUpload }: { onUpload: () => void }) {
  return (
    <div className="empty-state">
      <div className="empty-orbit"><Code2 size={30} /></div>
      <p className="eyebrow">Your private code index</p>
      <h2>Ask your codebase.<br /><span>Get answers with proof.</span></h2>
      <p>Upload a ZIP archive to build a local, searchable map of your code. Every answer links back to its source.</p>
      <button className="button primary" onClick={onUpload}><Plus size={17} /> Add repository</button>
      <div className="trust-row">
        <span><ShieldCheck size={15} /> Local-first</span>
        <span><BookOpen size={15} /> Source citations</span>
        <span><Sparkles size={15} /> Evidence aware</span>
      </div>
    </div>
  )
}

interface UploadDialogProps {
  onClose: () => void
  onComplete: (repository: Repository, job: Job) => void
}

function UploadDialog({ onClose, onComplete }: UploadDialogProps) {
  const [file, setFile] = useState<File | null>(null)
  const [name, setName] = useState('')
  const [dragging, setDragging] = useState(false)
  const [progress, setProgress] = useState<number | null>(null)
  const [error, setError] = useState('')
  const keyRef = useRef(newIdempotencyKey())
  const inputRef = useRef<HTMLInputElement>(null)

  const choose = (candidate?: File) => {
    if (!candidate) return
    if (!candidate.name.toLowerCase().endsWith('.zip')) {
      setError('Choose a .zip archive.')
      return
    }
    setFile(candidate)
    setName(candidate.name.replace(/\.zip$/i, ''))
    setError('')
    keyRef.current = newIdempotencyKey()
  }

  const submit = async () => {
    if (!file || !name.trim()) return
    setProgress(0)
    setError('')
    try {
      const result = await uploadRepository(name.trim(), file, keyRef.current, setProgress)
      onComplete({ ...result.repository, job_id: result.job.id }, result.job)
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Upload failed.')
      setProgress(null)
    }
  }

  return (
    <div className="modal-backdrop" role="presentation" onMouseDown={(e) => e.target === e.currentTarget && progress === null && onClose()}>
      <section className="modal" role="dialog" aria-modal="true" aria-labelledby="upload-title">
        <button className="icon-button close" aria-label="Close upload" onClick={onClose} disabled={progress !== null}><X /></button>
        <p className="eyebrow">New codebase</p>
        <h2 id="upload-title">Add a repository</h2>
        <p className="muted">Upload a ZIP archive. It stays within your configured CodeCompass environment.</p>
        <button
          type="button"
          className={`dropzone ${dragging ? 'dragging' : ''} ${file ? 'has-file' : ''}`}
          onClick={() => inputRef.current?.click()}
          onDragOver={(e) => { e.preventDefault(); setDragging(true) }}
          onDragLeave={() => setDragging(false)}
          onDrop={(e) => { e.preventDefault(); setDragging(false); choose(e.dataTransfer.files[0]) }}
        >
          <input ref={inputRef} type="file" accept=".zip,application/zip" hidden onChange={(e) => choose(e.target.files?.[0])} />
          {file ? <><FileArchive size={28} /><strong>{file.name}</strong><span>{(file.size / 1024 / 1024).toFixed(1)} MB · Click to replace</span></>
            : <><UploadCloud size={30} /><strong>Drop your ZIP here</strong><span>or click to browse</span></>}
        </button>
        <label className="field-label" htmlFor="repo-name">Display name</label>
        <input id="repo-name" value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Payments service" disabled={progress !== null} />
        {progress !== null && (
          <div className="upload-progress" aria-live="polite">
            <div><span>Uploading archive</span><strong>{progress}%</strong></div>
            <progress value={progress} max="100" />
            <small>Safe to retry if interrupted — this upload keeps the same request key.</small>
          </div>
        )}
        {error && <div className="inline-error" role="alert"><AlertCircle size={17} /><span>{error} You can retry safely.</span></div>}
        <div className="modal-actions">
          <button className="button ghost" onClick={onClose} disabled={progress !== null}>Cancel</button>
          <button className="button primary" onClick={submit} disabled={!file || !name.trim() || progress !== null}>
            {progress !== null ? <LoaderCircle className="spin" size={17} /> : <UploadCloud size={17} />} Upload & index
          </button>
        </div>
      </section>
    </div>
  )
}

function CitationViewer({ citation, onClose }: { citation: Citation; onClose: () => void }) {
  return (
    <aside className="drawer" aria-label="Source citation">
      <div className="drawer-head">
        <div><p className="eyebrow">Source evidence</p><h3>{citation.path}</h3></div>
        <button className="icon-button" aria-label="Close citation" onClick={onClose}><X /></button>
      </div>
      <div className="line-meta"><FileCode2 size={16} /> Lines {citation.start_line ?? '?'}–{citation.end_line ?? citation.start_line ?? '?'}</div>
      {citation.redacted && <div className="redaction"><ShieldCheck size={16} /> Sensitive content was redacted by the server.</div>}
      <pre className="snippet"><code>{citation.snippet || 'No source preview was returned for this citation.'}</code></pre>
    </aside>
  )
}

function TracePanel({ trace, loading }: { trace: Trace | null; loading: boolean }) {
  return (
    <div className="trace-panel">
      <div className="trace-summary">
        <span><Search size={16} /> Retrieval trace</span>
        {trace?.duration_ms != null && <strong>{trace.duration_ms} ms</strong>}
      </div>
      {loading ? <div className="skeleton-line" /> : trace ? (
        <>
          <div className="trace-stats">
            <span><b>{trace.candidates ?? trace.retrieved?.length ?? 0}</b> candidates</span>
            <span><b>{trace.retrieved?.length ?? 0}</b> cited chunks</span>
          </div>
          {(trace.stages ?? []).map((stage, i) => (
            <div className="trace-stage" key={`${stage.name}-${i}`}><Check size={14} /><span>{stage.name}</span><small>{stage.detail}</small><b>{stage.duration_ms != null ? `${stage.duration_ms} ms` : ''}</b></div>
          ))}
        </>
      ) : <p className="muted small">No trace details were returned.</p>}
    </div>
  )
}

function QuestionWorkspace({ repository }: { repository: Repository }) {
  const [question, setQuestion] = useState('')
  const [submitted, setSubmitted] = useState('')
  const [answer, setAnswer] = useState<Answer | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [citation, setCitation] = useState<Citation | null>(null)
  const [showTrace, setShowTrace] = useState(false)
  const [trace, setTrace] = useState<Trace | null>(null)
  const [traceLoading, setTraceLoading] = useState(false)

  const ask = async (event: React.FormEvent) => {
    event.preventDefault()
    const value = question.trim()
    if (!value || busy) return
    setSubmitted(value); setAnswer(null); setError(''); setBusy(true)
    try {
      const result = await api.askQuestion(repository.id, value)
      setAnswer(result)
      const embedded = result.trace && 'query' in result.trace ? result.trace as Trace : null
      setTrace(embedded)
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Could not answer this question.')
    } finally {
      setBusy(false)
    }
  }

  const openTrace = async () => {
    setShowTrace((value) => !value)
    const id = answer?.trace_id ?? answer?.trace?.id
    if (!trace && id) {
      setTraceLoading(true)
      try { setTrace(await api.getTrace(id)) } catch { setTrace(null) } finally { setTraceLoading(false) }
    }
  }

  return (
    <div className="qa-workspace">
      {!submitted && !busy ? (
        <div className="ask-intro">
          <div className="compass-mark"><Sparkles /></div>
          <h2>What do you want to understand?</h2>
          <p>Ask about architecture, behavior, data flow, or implementation details.</p>
          <div className="suggestions">
            {['How does authentication work?', 'Where are errors handled?', 'Explain the main data flow'].map((item) =>
              <button key={item} onClick={() => setQuestion(item)}>{item}<ChevronRight size={14} /></button>)}
          </div>
        </div>
      ) : (
        <div className="answer-area" aria-live="polite">
          <div className="question-bubble"><span>You asked</span><p>{submitted}</p></div>
          {busy && <div className="thinking"><LoaderCircle className="spin" /><div><strong>Finding evidence…</strong><span>Searching indexed code and verifying sources</span></div></div>}
          {error && <div className="answer-error" role="alert"><AlertCircle /><div><strong>We couldn’t answer that</strong><p>{error}</p><button onClick={() => { setSubmitted(''); setError('') }}>Try another question</button></div></div>}
          {answer?.outcome === 'insufficient_evidence' && (
            <div className="abstention"><Archive /><div><p className="eyebrow">Insufficient evidence</p><h3>There isn’t enough indexed code to answer reliably.</h3><p>{answer.message || 'Try asking about a different area, or upload a more complete archive.'}</p></div></div>
          )}
          {answer?.outcome === 'answered' && (
            <article className="answer-card">
              <div className="answer-heading"><div><Sparkles size={18} /><span>CodeCompass answer</span></div><button className={showTrace ? 'active' : ''} onClick={openTrace}><Search size={14} /> Trace</button></div>
              <Markdown content={answer.answer ?? ''} />
              {!!answer.citations?.length && <div className="citations"><p className="eyebrow">Sources</p>{answer.citations.map((item, index) =>
                <button key={`${item.path}-${index}`} onClick={() => setCitation(item)}>
                  <span>{index + 1}</span><div><strong>{item.path}</strong><small>Lines {item.start_line ?? '?'}–{item.end_line ?? item.start_line ?? '?'}</small></div><ChevronRight size={15} />
                </button>)}</div>}
              {showTrace && <TracePanel trace={trace} loading={traceLoading} />}
            </article>
          )}
        </div>
      )}
      <form className="question-form" onSubmit={ask}>
        <label className="sr-only" htmlFor="question">Ask a question about this repository</label>
        <textarea id="question" rows={1} value={question} onChange={(e) => setQuestion(e.target.value)} placeholder="Ask about this codebase…" maxLength={2000} />
        <button className="send-button" aria-label="Ask question" disabled={!question.trim() || busy}><Send size={18} /></button>
      </form>
      {citation && <CitationViewer citation={citation} onClose={() => setCitation(null)} />}
    </div>
  )
}

function DeleteDialog({ repository, onClose, onDeleted }: { repository: Repository; onClose: () => void; onDeleted: () => void }) {
  const [confirmation, setConfirmation] = useState('')
  const [purge, setPurge] = useState<Purge | null>(null)
  const [error, setError] = useState('')
  const [pollCycle, setPollCycle] = useState(0)
  const deleteRepo = async () => {
    try { setError(''); setPurge(await api.deleteRepository(repository.id)) } catch (caught) { setError(caught instanceof Error ? caught.message : 'Deletion failed.') }
  }
  useEffect(() => {
    if (!purge || ['completed', 'failed'].includes(purge.status)) return
    const timer = window.setTimeout(async () => {
      try {
        const next = await api.getPurge(purge.id)
        setError(''); setPurge(next)
        if (next.status === 'completed') window.setTimeout(onDeleted, 650)
      } catch (caught) {
        setError(caught instanceof Error ? caught.message : 'Could not check purge status.')
        setPollCycle((value) => value + 1)
      }
    }, 1500)
    return () => clearTimeout(timer)
  }, [purge, onDeleted, pollCycle])

  return <div className="modal-backdrop"><section className="modal danger-modal" role="dialog" aria-modal="true" aria-labelledby="delete-title">
    <div className="danger-icon"><Trash2 /></div><h2 id="delete-title">Delete {repository.display_name}?</h2>
    {!purge ? <><p>This permanently removes the archive, index, answers, and traces. This cannot be undone.</p>
      <label className="field-label" htmlFor="confirm">Type <strong>{repository.display_name}</strong> to confirm</label>
      <input id="confirm" value={confirmation} onChange={(e) => setConfirmation(e.target.value)} autoComplete="off" />
      {error && <div className="inline-error"><AlertCircle size={16} />{error}</div>}
      <div className="modal-actions"><button className="button ghost" onClick={onClose}>Cancel</button><button className="button danger" disabled={confirmation !== repository.display_name} onClick={deleteRepo}><Trash2 size={16} /> Delete repository</button></div>
    </> : <div className="purge-state"><LoaderCircle className={purge.status !== 'failed' ? 'spin' : ''} /><div><strong>{purge.status === 'completed' ? 'Deletion complete' : purge.status === 'failed' ? 'Deletion failed' : 'Securely deleting…'}</strong><p>{error || purge.error || `Purge is ${purge.status}. This window will update automatically.`}</p></div></div>}
  </section></div>
}

function RepositoryDetail({ repository: initial, onBack, onRefresh, onDeleted }: { repository: Repository; onBack: () => void; onRefresh: (r: Repository) => void; onDeleted: () => void }) {
  const [repository, setRepository] = useState(initial)
  const [job, setJob] = useState<Job | null>(null)
  const [deleteOpen, setDeleteOpen] = useState(false)
  const refresh = useCallback(async () => {
    try {
      const next = await api.getRepository(repository.id)
      setRepository(next); onRefresh(next)
      if (next.job_id && !terminal.has(next.indexing_status)) setJob(await api.getJob(next.job_id))
    } catch { /* visible state remains usable */ }
  }, [repository.id, onRefresh])
  useEffect(() => {
    if (terminal.has(repository.indexing_status)) return
    const timer = window.setInterval(refresh, 1800)
    return () => clearInterval(timer)
  }, [refresh, repository.indexing_status])
  const cancel = async () => {
    if (!repository.job_id) return
    try { const next = await api.cancelJob(repository.job_id); setJob(next); await refresh() } catch { /* polling will reconcile */ }
  }
  const progress = job?.progress ?? (repository.indexing_status === 'queued' ? 8 : 48)
  return <main className="detail">
    <div className="detail-top">
      <button className="back-button" onClick={onBack}><ArrowLeft size={17} /> Repositories</button>
      <button className="icon-button delete-trigger" aria-label="Delete repository" onClick={() => setDeleteOpen(true)}><Trash2 size={17} /></button>
    </div>
    <header className="repo-header">
      <div className="repo-icon"><FileCode2 /></div>
      <div><p className="eyebrow">Repository</p><h1>{repository.display_name}</h1><div className="header-statuses"><StatusPill kind="availability" value={repository.availability} /><StatusPill kind="indexing" value={repository.indexing_status} /></div></div>
    </header>
    {repository.degradation && <div className="state-banner warning" role="status"><AlertCircle /><div><strong>Reduced retrieval quality</strong><p>{repository.degradation}. Answers use the available lexical index and may miss semantic matches.</p></div></div>}
    {!terminal.has(repository.indexing_status) && <section className="index-banner">
      <LoaderCircle className="spin" /><div><div><strong>{job?.phase || (repository.indexing_status === 'queued' ? 'Waiting to index' : 'Building your code map')}</strong><span>{Math.round(progress)}%</span></div><progress value={progress} max="100" /><p>You can leave this page. Indexing continues in the background.</p></div>
      {repository.job_id && <button onClick={cancel}><CircleStop size={15} /> Cancel</button>}
    </section>}
    {repository.indexing_status === 'failed' && <div className="state-banner error"><AlertCircle /><div><strong>Indexing failed</strong><p>{repository.error || 'The archive could not be indexed. Delete it and try a new archive.'}</p></div></div>}
    {repository.indexing_status === 'cancelled' && <div className="state-banner"><CircleStop /><div><strong>Indexing cancelled</strong><p>This repository is not ready for questions.</p></div></div>}
    {repository.availability === 'available' ? <QuestionWorkspace repository={repository} /> : <div className="locked-workspace"><Clock3 /><h2>Questions unlock when indexing completes</h2><p>CodeCompass is preparing searchable, citable evidence.</p></div>}
    {deleteOpen && <DeleteDialog repository={repository} onClose={() => setDeleteOpen(false)} onDeleted={onDeleted} />}
  </main>
}

export default function App() {
  const [repositories, setRepositories] = useState<Repository[]>([])
  const [selected, setSelected] = useState<Repository | null>(null)
  const [uploadOpen, setUploadOpen] = useState(false)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [sidebarOpen, setSidebarOpen] = useState(false)

  const load = useCallback(async () => {
    try { setRepositories(await api.listRepositories()); setError('') }
    catch (caught) { setError(caught instanceof Error ? caught.message : 'Could not load repositories.') }
    finally { setLoading(false) }
  }, [])
  useEffect(() => { load() }, [load])
  const updateRepository = useCallback((next: Repository) => setRepositories((items) => items.map((item) => item.id === next.id ? next : item)), [])

  if (selected) return <div className="app-shell">
    <Sidebar repositories={repositories} selected={selected} open={sidebarOpen} onClose={() => setSidebarOpen(false)} onSelect={(r) => { setSelected(r); setSidebarOpen(false) }} onUpload={() => setUploadOpen(true)} />
    <button className="mobile-menu" onClick={() => setSidebarOpen(true)} aria-label="Open navigation"><Menu /></button>
    <RepositoryDetail repository={selected} onBack={() => setSelected(null)} onRefresh={(r) => { setSelected(r); updateRepository(r) }} onDeleted={() => { setSelected(null); setRepositories((items) => items.filter((r) => r.id !== selected.id)) }} />
    {uploadOpen && <UploadDialog onClose={() => setUploadOpen(false)} onComplete={(repository) => { setRepositories((items) => [repository, ...items]); setSelected(repository); setUploadOpen(false) }} />}
  </div>

  return <div className="app-shell">
    <Sidebar repositories={repositories} selected={null} open={sidebarOpen} onClose={() => setSidebarOpen(false)} onSelect={setSelected} onUpload={() => setUploadOpen(true)} />
    <button className="mobile-menu" onClick={() => setSidebarOpen(true)} aria-label="Open navigation"><Menu /></button>
    <main className="home">
      <header className="home-header"><div><p className="eyebrow">Workspace</p><h1>Repositories</h1><p>Index code. Ask questions. Follow the evidence.</p></div><button className="button primary" onClick={() => setUploadOpen(true)}><Plus size={17} /> Add repository</button></header>
      {loading ? <div className="loading-grid" aria-label="Loading repositories">{[1,2,3].map((i) => <div className="repo-card skeleton" key={i} />)}</div>
      : error ? <div className="load-error"><AlertCircle /><h2>Couldn’t reach CodeCompass</h2><p>{error}</p><button className="button" onClick={load}>Try again</button></div>
      : repositories.length === 0 ? <EmptyState onUpload={() => setUploadOpen(true)} />
      : <div className="repo-grid">{repositories.map((repo) => <button className="repo-card" key={repo.id} onClick={() => setSelected(repo)}>
        <div className="card-top"><span className="repo-icon small"><Code2 /></span><ChevronRight /></div><h2>{repo.display_name}</h2><p>{repo.file_name || 'Uploaded code archive'}</p><div className="card-status"><StatusPill kind="availability" value={repo.availability} /><StatusPill kind="indexing" value={repo.indexing_status} /></div>
      </button>)}</div>}
    </main>
    {uploadOpen && <UploadDialog onClose={() => setUploadOpen(false)} onComplete={(repository) => { setRepositories((items) => [repository, ...items]); setSelected(repository); setUploadOpen(false) }} />}
  </div>
}

function Sidebar({ repositories, selected, open, onClose, onSelect, onUpload }: { repositories: Repository[]; selected: Repository | null; open: boolean; onClose: () => void; onSelect: (r: Repository) => void; onUpload: () => void }) {
  return <><div className={`sidebar-scrim ${open ? 'show' : ''}`} onClick={onClose} /><aside className={`sidebar ${open ? 'open' : ''}`}>
    <div className="brand"><div className="brand-mark"><Code2 /></div><span>Code<b>Compass</b></span><button className="icon-button sidebar-close" onClick={onClose}><X /></button></div>
    <nav aria-label="Repository navigation"><p className="nav-label">Your repositories</p>{repositories.map((repo) => <button className={selected?.id === repo.id ? 'active' : ''} key={repo.id} onClick={() => onSelect(repo)}><FileCode2 /><span>{repo.display_name}</span><span className={`nav-dot ${repo.indexing_status}`} /></button>)}
      {!repositories.length && <p className="nav-empty">No repositories yet</p>}</nav>
    <button className="sidebar-add" onClick={onUpload}><Plus /> Add repository</button>
    <div className="sidebar-foot"><ShieldCheck /><span><strong>Local-first workspace</strong><small>Your code stays in your environment</small></span></div>
  </aside></>
}

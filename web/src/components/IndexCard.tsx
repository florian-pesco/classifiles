import { useCallback, useEffect, useState } from 'react'
import { api, shortDate, type IndexStatus } from '../api'

/** Status and controls of the example index (what "Learn from my folders" uses). */
export function IndexCard({ active, notify, fail }: {
  active: boolean  // is the examples backend selected?
  notify: (m: string) => void
  fail: (e: unknown) => void
}) {
  const [st, setSt] = useState<IndexStatus | null>(null)
  const load = useCallback(() => api.index().then(setSt).catch(fail), [fail])
  useEffect(() => { load() }, [load])

  const running = st?.status.state === 'running'
  useEffect(() => {
    if (!running) return
    const t = setInterval(async () => {
      try {
        const s = await api.index()
        setSt(s)
        if (s.status.state !== 'running') {
          notify(s.status.state === 'done' ? 'Example index is up to date.' : `Index ${s.status.state}.`)
        }
      } catch (e) { fail(e) }
    }, 1000)
    return () => clearInterval(t)
  }, [running, notify, fail])

  const build = async () => { try { setSt(await api.buildIndex()) } catch (e) { fail(e) } }
  const cancel = async () => { try { setSt(await api.cancelIndex()) } catch (e) { fail(e) } }
  const [learning, setLearning] = useState(false)
  const learnPast = async () => {
    setLearning(true)
    try {
      const r = await api.learnPast()
      notify(`Learned ${r.learned} past decisions${r.by_action.delete ? ` (${r.by_action.delete} deletions)` : ''}.`)
      await load()
    } catch (e) { fail(e) } finally { setLearning(false) }
  }

  if (!st) return null
  const s = st.status
  return (
    <section className="card">
      <div className="section-head">
        <h3>Example index</h3>
        {running
          ? <button onClick={cancel}>Cancel</button>
          : <button className={st.total ? '' : 'primary'} onClick={build}>{st.total ? 'Update index' : 'Build index'}</button>}
      </div>
      <p className="muted small">
        "Learn from my folders" compares new files with files you already sorted. The index holds a sample of
        them (spread over subfolders), built locally with <code>{st.model}</code>. Update it after big changes to
        your folders. Files you apply are learned automatically.
        {!active && <> It is only used when Settings → Model is "Learn from my folders".</>}
      </p>
      {running && (
        <div className="progress" role="progressbar" aria-valuenow={s.done} aria-valuemax={s.total}>
          <div style={{ width: `${s.total ? (100 * (s.done ?? 0)) / s.total : 0}%` }} />
          <span>{s.message}{s.total ? ` · ${s.done}/${s.total}` : ''}</span>
        </div>
      )}
      {s.state === 'failed' && <div className="banner error">Index build failed: {s.error}</div>}
      {st.unlearned > 0 && !running && (
        <div className="banner info">
          {st.unlearned} files you applied earlier are not learned yet (e.g. applied while another model was selected).
          {' '}<button className="linkish" onClick={learnPast} disabled={learning}>
            {learning ? 'Learning…' : 'Learn them now'}
          </button>
        </div>
      )}
      {!running && s.state !== 'idle' && s.state !== 'failed' && (
        <p className="small muted">
          Last run {s.state}{s.finished_at ? ` at ${shortDate(s.finished_at)}` : ''}: {s.added ?? 0} new,{' '}
          {s.reused ?? 0} unchanged, {s.removed ?? 0} removed.
        </p>
      )}
      {st.images?.enabled && (
        <p className="small muted">
          Pictures: {st.images.state === 'running'
            ? `${st.images.message ?? 'reading pictures'}${st.images.total ? ` · ${st.images.done ?? 0}/${st.images.total}` : ''}…`
            : st.images.total
              ? `${st.images.total.toLocaleString()} sorted pictures are compared with inbox pictures by what they look like.`
              : 'not indexed yet. Update the index to compare inbox pictures with your sorted pictures.'}
          {st.images.state === 'failed' && <span className="warn-text"> Failed: {st.images.error}</span>}
        </p>
      )}
      {st.catalog?.enabled && (
        <p className="small muted">
          Duplicate check: {st.catalog.state === 'running'
            ? `${st.catalog.message ?? 'listing files'}…`
            : st.catalog.files
              ? `${st.catalog.files.toLocaleString()} files in your folders are listed, so inbox files that already exist there are found.`
              : 'not listed yet. Update the index to also find inbox files that already exist in your folders.'}
        </p>
      )}
      <ul className="simple-list">
        {st.categories.filter(c => c.usable).map(c => (
          <li key={c.name}>
            <span className="grow">{c.name}</span>
            <span className="small muted">
              {c.examples} examples{c.learned ? ` + ${c.learned} learned` : ''}
              {st.images?.per_category?.[c.name] ? ` · ${st.images.per_category[c.name]} pictures` : ''}
            </span>
          </li>
        ))}
      </ul>
    </section>
  )
}

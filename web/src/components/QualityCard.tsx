import { useEffect, useState } from 'react'
import { api, type Quality, type QualitySummary } from '../api'

const VIA_LABEL: Record<string, string> = {
  confirm: 'confirmed', override: 'changed', bulk: 'accepted in bulk', reject: 'kept', unknown: 'before tracking',
}
const BACKEND_LABEL: Record<string, string> = {
  examples: 'Learn from my folders', laya: 'Laya', keyword: 'Keyword', rule: 'Rules',
}

const pct = (x?: number) => (x == null ? '—' : `${Math.round(x * 100)}%`)

function Line({ label, s }: { label: string; s: QualitySummary }) {
  if (!s.files) return null
  const right = Math.round((s.accuracy ?? 0) * s.files)
  return (
    <li>
      <span className="grow">{label}</span>
      <span className="small"><b>{pct(s.accuracy)}</b> right ({right} of {s.files}) · right one in top 3: {pct(s.top3)}</span>
    </li>
  )
}

/** How often proposals matched what you actually chose in Review. Aggregates only. */
export function QualityCard({ fail }: { fail: (e: unknown) => void }) {
  const [q, setQ] = useState<Quality | null>(null)
  const [checking, setChecking] = useState(false)
  useEffect(() => { api.quality().then(setQ).catch(fail) }, [fail])

  const recheck = async () => {
    setChecking(true)
    try { setQ(await api.quality(true)) } catch (e) { fail(e) } finally { setChecking(false) }
  }

  if (!q) return null
  return (
    <section className="card">
      <div className="section-head">
        <h3>Quality</h3>
        <button onClick={recheck} disabled={checking || !q.decisions}>
          {checking ? 'Checking…' : 'Re-check with current model'}
        </button>
      </div>
      {q.decisions === 0 ? (
        <p className="muted small">No decisions yet. Accept or change proposals in Review to measure quality.</p>
      ) : (
        <>
          <p className="muted small">
            Measured on {q.decisions} files you decided in Review (rules not counted):{' '}
            {Object.entries(q.decided_via).map(([k, n]) => `${n} ${VIA_LABEL[k] ?? k}`).join(', ')}.
            Files accepted in bulk count as right, so "changed" decisions are the most telling.
          </p>
          <ul className="simple-list">
            {Object.entries(q.recorded).map(([backend, s]) => (
              <Line key={backend} label={`Proposals at scan time (${BACKEND_LABEL[backend] ?? backend})`} s={s} />
            ))}
            {q.current && <Line label="Current model re-run on the same files" s={q.current} />}
          </ul>
          {q.current?.calibration && (
            <p className="small muted">
              Current model by confidence:{' '}
              {q.current.calibration.filter(b => b.files).map(b => `${b.range}: ${pct(b.accuracy ?? undefined)} of ${b.files}`).join(' · ')}
            </p>
          )}
        </>
      )}
    </section>
  )
}

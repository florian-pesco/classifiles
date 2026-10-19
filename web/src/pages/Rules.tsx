import { useEffect, useState } from 'react'
import { api, type Category, type Rule } from '../api'
import { CategorySelect } from '../components/CategorySelect'

/** Editable form of a rule; converted to/from the config format on load/save. */
interface Draft {
  id: number
  folder: string
  ext: string   // ".exe, .msi"
  kinds: string[]
  name: string  // regex
  glob: string
}

let nextId = 1
const asList = (v?: string | string[]) => (v == null ? [] : Array.isArray(v) ? v : [v])

function toDraft(r: Rule): Draft {
  return {
    id: nextId++, folder: r.folder, ext: asList(r.ext).join(', '), kinds: asList(r.kind),
    name: r.name ?? '', glob: r.glob ?? '',
  }
}

function toRule(d: Draft): Rule {
  const ext = d.ext.split(/[,\s]+/).map(e => e.trim()).filter(Boolean)
    .map(e => (e.startsWith('.') ? e.toLowerCase() : `.${e.toLowerCase()}`))
  return {
    folder: d.folder,
    ...(ext.length ? { ext } : {}),
    ...(d.kinds.length ? { kind: d.kinds } : {}),
    ...(d.name.trim() ? { name: d.name.trim() } : {}),
    ...(d.glob.trim() ? { glob: d.glob.trim() } : {}),
  }
}

const hasCondition = (d: Draft) => Boolean(d.ext.trim() || d.kinds.length || d.name.trim() || d.glob.trim())

export function RulesPage({ categories, notify, fail }: {
  categories: Category[]
  notify: (m: string) => void
  fail: (e: unknown) => void
}) {
  const [drafts, setDrafts] = useState<Draft[]>([])
  const [kinds, setKinds] = useState<string[]>([])
  const [dirty, setDirty] = useState(false)

  const load = () => api.rules().then(r => { setDrafts(r.rules.map(toDraft)); setKinds(r.kinds); setDirty(false) }).catch(fail)
  useEffect(() => { load() }, []) // eslint-disable-line react-hooks/exhaustive-deps

  const edit = (id: number, patch: Partial<Draft>) => {
    setDrafts(ds => ds.map(d => (d.id === id ? { ...d, ...patch } : d)))
    setDirty(true)
  }
  const move = (i: number, delta: number) => {
    setDrafts(ds => {
      const out = [...ds]
      const [d] = out.splice(i, 1)
      out.splice(i + delta, 0, d)
      return out
    })
    setDirty(true)
  }

  const save = async () => {
    const bad = drafts.find(d => !d.folder || !hasCondition(d))
    if (bad) { fail(new Error('Every rule needs a category and at least one condition.')); return }
    try {
      const r = await api.saveRules(drafts.map(toRule))
      setDrafts(r.rules.map(toDraft))
      setDirty(false)
      notify('Rules saved. They apply to the next scan.')
    } catch (e) { fail(e) }
  }

  return (
    <div className="page">
      <section className="card explain">
        <h2>Rules</h2>
        <p>
          Rules handle the obvious cases without the model. They are checked <b>top to bottom</b>, and the
          <b> first matching rule wins</b> (confidence 100%). A rule matches when <b>all</b> conditions you
          fill in match. Empty fields are ignored.
        </p>
        <ul className="small muted">
          <li><b>Extensions</b>: e.g. <code>.exe, .msi</code></li>
          <li><b>File types</b>: groups like <i>installer</i>, <i>image</i>, <i>audio</i></li>
          <li><b>Name contains</b>: words in the file name, case-insensitive. Use <code>|</code> for "or": <code>Rechnung|Invoice</code></li>
          <li><b>Name pattern</b>: wildcards: <code>Screenshot*</code>, <code>*_scan.pdf</code></li>
        </ul>
      </section>

      {drafts.length === 0 && <p className="empty">No rules yet.</p>}

      {drafts.map((d, i) => (
        <section key={d.id} className={`card rule${hasCondition(d) && d.folder ? '' : ' incomplete'}`}>
          <div className="rule-head">
            <span className="muted small">#{i + 1}</span>
            <span className="grow" />
            <button className="icon" disabled={i === 0} onClick={() => move(i, -1)} title="Move up">↑</button>
            <button className="icon" disabled={i === drafts.length - 1} onClick={() => move(i, 1)} title="Move down">↓</button>
            <button className="icon no" title="Delete rule" onClick={() => { setDrafts(ds => ds.filter(x => x.id !== d.id)); setDirty(true) }}>✕</button>
          </div>
          <div className="rule-grid">
            <label className="field"><span>Extensions</span>
              <input value={d.ext} placeholder=".exe, .msi" onChange={e => edit(d.id, { ext: e.target.value })} />
            </label>
            <label className="field"><span>Name contains</span>
              <input value={d.name} placeholder="Rechnung|Invoice" onChange={e => edit(d.id, { name: e.target.value })} />
            </label>
            <label className="field"><span>Name pattern</span>
              <input value={d.glob} placeholder="Screenshot*" onChange={e => edit(d.id, { glob: e.target.value })} />
            </label>
            <label className="field"><span>→ Category</span>
              <CategorySelect value={d.folder || null} categories={categories} label="Target category"
                              onChange={folder => edit(d.id, { folder })} />
            </label>
          </div>
          <div className="field">
            <span>File types</span>
            <div className="kind-chips">
              {kinds.map(k => (
                <button key={k} className={d.kinds.includes(k) ? 'chip active' : 'chip'}
                        onClick={() => edit(d.id, { kinds: d.kinds.includes(k) ? d.kinds.filter(x => x !== k) : [...d.kinds, k] })}>
                  {k}
                </button>
              ))}
            </div>
          </div>
        </section>
      ))}

      <footer className="actions">
        <button onClick={() => { setDrafts(ds => [...ds, { id: nextId++, folder: '', ext: '', kinds: [], name: '', glob: '' }]); setDirty(true) }}>
          + Add rule
        </button>
        <button className="primary" onClick={save} disabled={!dirty}>Save rules</button>
        <button onClick={load} disabled={!dirty}>Discard changes</button>
      </footer>
    </div>
  )
}

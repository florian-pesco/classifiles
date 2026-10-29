import { useEffect, useMemo, useState } from 'react'
import { api, type CategoriesPayload, type CategoryEntry, type DeleteSettings } from '../api'
import { FolderPicker } from '../components/FolderPicker'
import { IndexCard } from '../components/IndexCard'
import { PageHead } from '../components/PageHead'
import { Icon } from '../components/Icon'

interface Row {
  path: string
  label: string  // what is shown / used in rules
  name: string   // editable name (manual categories only)
  description: string
  enabled: boolean
  action: 'move' | 'copy'
  source: 'auto' | 'manual'
  exists: boolean
  root: string | null
  samples: string[]
}

const WORD_LIMIT = 12

function toRows(p: CategoriesPayload): Row[] {
  return p.categories
    .filter(c => c.source !== 'delete')
    .map(c => ({
      path: c.path!, label: c.name, name: c.source === 'manual' ? c.name : '', description: c.description,
      enabled: c.enabled, action: c.action === 'copy' ? 'copy' : 'move', source: c.source as 'auto' | 'manual',
      exists: c.exists, root: c.root, samples: c.samples,
    }))
}

const words = (s: string) => s.trim().split(/\s+/).filter(Boolean).length

export function CategoriesPage({ data, examplesActive, onSaved, notify, fail }: {
  data: CategoriesPayload
  examplesActive: boolean
  onSaved: (p: CategoriesPayload) => void
  notify: (m: string) => void
  fail: (e: unknown) => void
}) {
  const [roots, setRoots] = useState(data.roots)
  const [rows, setRows] = useState<Row[]>(() => toRows(data))
  const [del, setDel] = useState<DeleteSettings>(data.delete)
  const [dirty, setDirty] = useState(false)
  const [picker, setPicker] = useState<'root' | 'manual' | null>(null)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    setRoots(data.roots); setRows(toRows(data)); setDel(data.delete); setDirty(false)
  }, [data])

  const edit = (path: string, patch: Partial<Row>) => {
    setRows(rs => rs.map(r => (r.path === path ? { ...r, ...patch } : r)))
    setDirty(true)
  }

  const save = async () => {
    setSaving(true)
    try {
      const entries: CategoryEntry[] = rows.map(r => ({
        path: r.path,
        ...(r.source === 'manual' && r.name.trim() ? { name: r.name.trim() } : {}),
        description: r.description,
        enabled: r.enabled,
        action: r.action,
      }))
      const saved = await api.saveCategories(roots, entries, del)
      onSaved(saved)
      notify('Categories saved. They apply to the next scan.')
    } catch (e) { fail(e) } finally { setSaving(false) }
  }

  const reload = async () => {
    try { onSaved(await api.categories(true)); notify('Folders re-read.') } catch (e) { fail(e) }
  }

  const grouped = useMemo(() => {
    const g = new Map<string, Row[]>()
    for (const r of rows) {
      const key = r.source === 'manual' ? 'Manual categories' : r.root ?? '?'
      g.set(key, [...(g.get(key) ?? []), r])
    }
    return [...g.entries()].sort(([a], [b]) => (a === 'Manual categories' ? 1 : b === 'Manual categories' ? -1 : a.localeCompare(b)))
  }, [rows])

  const enabled = rows.filter(r => r.enabled).length

  return (
    <div className="page">
      <PageHead title="Categories" lead={`${enabled} of ${rows.length} folders are offered as categories. Files go into them, or into Delete.`}>
        <button onClick={() => setPicker('root')}><Icon name="folder" size={16} />Add root</button>
        <button onClick={() => setPicker('manual')}><Icon name="layers" size={16} />Add folder</button>
        <button className="ghost" onClick={reload} disabled={dirty} title="Re-read folders from disk"><Icon name="learn" size={16} />Re-read</button>
      </PageHead>

      <details className="card explain fold">
        <summary><Icon name="chevron" size={15} />How categories work</summary>
        <p>
          Every subfolder of a <b>root</b> becomes a category automatically; any other folder can be added as a
          <b> manual</b> category. Switch a category <b>off</b> to never offer it, choose <b>move</b> or <b>copy</b>
          (copy keeps the original in the inbox), and optionally describe what belongs there. Descriptions are optional
          with "Learn from my folders"; with Laya they are essential (about {WORD_LIMIT} words).
        </p>
      </details>

      <section className="card">
        <div className="section-head"><h3>Roots</h3></div>
        {roots.length === 0 && <p className="muted small">No roots yet. Add the folder that holds your category folders.</p>}
        <div className="root-chips">
          {roots.map(r => (
            <span key={r.path} className="root-chip" title={r.path}>
              <Icon name="folder" size={15} />
              <span className="mono">{r.path}</span>
              <button className="ghost icon-s" title="Remove root" aria-label={`Remove root ${r.path}`} onClick={() => {
                setRoots(rs => rs.filter(x => x.path !== r.path)); setDirty(true)
              }}><Icon name="x" size={14} /></button>
            </span>
          ))}
        </div>
        {dirty && roots.some(r => !rows.some(x => x.root === r.path)) && (
          <p className="muted small" style={{ marginTop: 10 }}>Save to load the subfolders of new roots.</p>
        )}
      </section>

      <IndexCard active={examplesActive} notify={notify} fail={fail} />

      {grouped.map(([group, items]) => (
        <section key={group} className="cat-section">
          <div className="cat-section-head">
            <h2 className={group === 'Manual categories' ? '' : 'mono-title'} title={group}>
              {group === 'Manual categories' ? group : group.split('/').filter(Boolean).pop()}
            </h2>
            <span className="muted small">{items.filter(r => r.enabled).length} of {items.length} on</span>
          </div>
          <div className="cat-grid">
            {items.map(r => {
              const n = words(r.description)
              return (
                <div key={r.path} className={`cat-card${r.enabled ? '' : ' off'}`}>
                  <div className="cat-card-head">
                    <input type="checkbox" className="switch" checked={r.enabled} aria-label={`Offer ${r.label}`}
                           title={r.enabled ? 'Offered as category' : 'Not offered'}
                           onChange={e => edit(r.path, { enabled: e.target.checked })} />
                    <div className="cat-name">
                      {r.source === 'manual'
                        ? <input value={r.name} placeholder={r.path.split('/').pop()} aria-label="Category name"
                                 onChange={e => edit(r.path, { name: e.target.value })} />
                        : <b>{r.label}</b>}
                      {r.source === 'manual' && <span className="muted small mono">{r.path}</span>}
                      {!r.exists && <span className="warn-text small">folder missing</span>}
                    </div>
                    <div className="seg seg-s" role="group" aria-label={`Action for ${r.label}`}>
                      {(['move', 'copy'] as const).map(a => (
                        <button key={a} className={r.action === a ? 'active' : ''} onClick={() => edit(r.path, { action: a })}
                                aria-pressed={r.action === a}>{a}</button>
                      ))}
                    </div>
                    {r.source === 'manual' && (
                      <button className="ghost icon-s" title="Remove category" aria-label={`Remove ${r.label}`} onClick={() => {
                        setRows(rs => rs.filter(x => x.path !== r.path)); setDirty(true)
                      }}><Icon name="x" size={14} /></button>
                    )}
                  </div>
                  <input value={r.description} aria-label={`Description for ${r.label}`}
                         placeholder={r.samples.length ? `e.g. ${r.samples.join(', ')}` : 'What belongs here? (optional)'}
                         onChange={e => edit(r.path, { description: e.target.value })} />
                  <span className={`small ${n > WORD_LIMIT ? 'warn-text' : 'muted'}`}>
                    {n === 0 ? 'No description' : `${n} words`}
                    {n > WORD_LIMIT ? ` (Laya reads about ${WORD_LIMIT})` : ''}
                  </span>
                </div>
              )
            })}
          </div>
        </section>
      ))}
      {!grouped.some(([g]) => g === 'Manual categories') && (
        <section className="cat-section">
          <div className="cat-section-head"><h2>Manual categories</h2></div>
          <button className="cat-card add-card" onClick={() => setPicker('manual')}>
            <Icon name="layers" size={18} />Add any folder outside your roots, e.g. on another drive
          </button>
        </section>
      )}

      <section className="cat-section">
        <div className="cat-section-head"><h2>Delete</h2></div>
        <div className={`cat-card delete-card${del.enabled ? '' : ' off'}`}>
          <div className="cat-card-head">
            <input type="checkbox" className="switch" checked={del.enabled} aria-label="Offer Delete"
                   onChange={e => { setDel({ ...del, enabled: e.target.checked }); setDirty(true) }} />
            <div className="cat-name"><b><Icon name="trash" size={15} className="inline-ico" />Delete</b></div>
          </div>
          <input value={del.description} placeholder="junk: installers, temporary or generated files, duplicates"
                 onChange={e => { setDel({ ...del, description: e.target.value }); setDirty(true) }} aria-label="Delete description" />
          <label className="check small" title="Otherwise only rules and your manual choice send files to Delete">
            <input type="checkbox" checked={del.suggest} onChange={e => { setDel({ ...del, suggest: e.target.checked }); setDirty(true) }} />
            Let the model suggest Delete
          </label>
          <span className="muted small">Files go to a hidden <code>.classifiles-trash</code> folder first; Undo works until you empty the trash.</span>
        </div>
      </section>

      {dirty && (
        <footer className="actions">
          <span className="muted small grow">Unsaved changes</span>
          <button className="ghost" onClick={() => { setRoots(data.roots); setRows(toRows(data)); setDel(data.delete); setDirty(false) }}>
            Discard
          </button>
          <button className="primary" onClick={save} disabled={saving}>{saving ? 'Saving…' : 'Save categories'}</button>
        </footer>
      )}

      {picker && (
        <FolderPicker
          title={picker === 'root' ? 'Add root (its subfolders become categories)' : 'Add a folder as category'}
          start={roots[0]?.path}
          onClose={() => setPicker(null)}
          onPick={path => {
            setPicker(null)
            if (picker === 'root') {
              if (!roots.some(r => r.path === path)) setRoots(rs => [...rs, { path }])
            } else if (!rows.some(r => r.path === path)) {
              setRows(rs => [...rs, {
                path, label: path.split('/').pop() ?? path, name: '', description: '', enabled: true,
                action: 'move', source: 'manual', exists: true, root: null, samples: [],
              }])
            }
            setDirty(true)
          }}
        />
      )}
    </div>
  )
}

import { useState } from 'react'
import { api, type Settings } from '../api'
import { PathInput } from '../components/FolderPicker'

const MODELS = [
  { value: 'laya:convaiinnovations/laya', label: 'Laya English (default)' },
  { value: 'laya:ml', label: 'Laya multilingual (better for German; downloads ~650 MB once)' },
  { value: 'keyword:', label: 'Keyword matching (no model; for testing)' },
]

export function SettingsPage({ settings, onSaved, notify, fail }: {
  settings: Settings
  onSaved: (s: Settings) => void
  notify: (m: string) => void
  fail: (e: unknown) => void
}) {
  const [inboxes, setInboxes] = useState(settings.inboxes.length ? settings.inboxes : [''])
  const [threshold, setThreshold] = useState(settings.threshold)
  const [maxFiles, setMaxFiles] = useState(settings.max_files ? String(settings.max_files) : '')
  const [model, setModel] = useState(
    settings.backend === 'keyword' ? 'keyword:' : `laya:${settings.laya_model === 'multilingual' ? 'ml' : settings.laya_model}`)
  const [samples, setSamples] = useState(settings.samples_per_folder)

  const save = async () => {
    const [backend, laya_model] = model.split(':')
    try {
      const s = await api.saveSettings({
        inboxes: inboxes.map(i => i.trim()).filter(Boolean),
        threshold,
        max_files: parseInt(maxFiles, 10) > 0 ? parseInt(maxFiles, 10) : 0,
        backend,
        ...(laya_model ? { laya_model } : {}),
        samples_per_folder: samples,
      })
      onSaved(s)
      notify('Settings saved.')
    } catch (e) { fail(e) }
  }

  return (
    <div className="page">
      <section className="card">
        <h3>Inbox folders</h3>
        <p className="muted small">Folders to clean up. The first one is pre-filled on the Review page. Inboxes never become categories.</p>
        {inboxes.map((p, i) => (
          <div key={i} className="row-form">
            <PathInput value={p} label="Inbox folder" placeholder="/mnt/d/…/Inbox"
                       onChange={v => setInboxes(xs => xs.map((x, j) => (j === i ? v : x)))} />
            <button className="icon no" title="Remove" onClick={() => setInboxes(xs => xs.filter((_, j) => j !== i))}>✕</button>
          </div>
        ))}
        <button onClick={() => setInboxes(xs => [...xs, ''])}>+ Add inbox</button>
      </section>

      <section className="card">
        <h3>Classification</h3>
        <label className="field">
          <span>Confidence threshold: <b>{threshold.toFixed(2)}</b>. At or above it, a file is "suggested". Below it, it "needs review".</span>
          <input type="range" min={0} max={1} step={0.05} value={threshold} onChange={e => setThreshold(Number(e.target.value))} />
        </label>
        <label className="field">
          <span>Default max files per scan (newest first; empty = all)</span>
          <input type="number" min={1} value={maxFiles} placeholder="all" onChange={e => setMaxFiles(e.target.value)} />
        </label>
        <label className="field">
          <span>Model</span>
          <select value={model} onChange={e => setModel(e.target.value)}>
            {MODELS.map(m => <option key={m.value} value={m.value}>{m.label}</option>)}
          </select>
        </label>
        <label className="field">
          <span>Sample file names shown to the model for folders without a description</span>
          <input type="number" min={0} max={10} value={samples} onChange={e => setSamples(Number(e.target.value))} />
        </label>
      </section>

      <footer className="actions">
        <button className="primary" onClick={save}>Save settings</button>
      </footer>
    </div>
  )
}

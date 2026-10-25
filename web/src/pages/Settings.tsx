import { useState } from 'react'
import { api, type Settings } from '../api'
import { PathInput } from '../components/FolderPicker'

const MODELS = [
  { value: 'examples:', label: 'Learn from my folders (recommended; build the index on the Categories page)' },
  { value: 'laya:convaiinnovations/laya', label: 'Laya English: decides from descriptions' },
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
    settings.backend === 'keyword' ? 'keyword:' : settings.backend === 'examples' ? 'examples:'
      : `laya:${settings.laya_model === 'multilingual' ? 'ml' : settings.laya_model}`)
  const [perCategory, setPerCategory] = useState(settings.examples_per_category)
  const [contentPerCategory, setContentPerCategory] = useState(settings.content_per_category)
  const [gpuShare, setGpuShare] = useState(Math.round(settings.gpu_memory_fraction * 100))
  const [samples, setSamples] = useState(settings.samples_per_folder)
  const [ocrPages, setOcrPages] = useState(settings.ocr_pdf_pages)
  const [ocrImages, setOcrImages] = useState(settings.ocr_images)
  const [useSource, setUseSource] = useState(settings.use_download_source)
  const [secondOpinion, setSecondOpinion] = useState(settings.second_opinion)
  const [llmModel, setLlmModel] = useState(settings.llm_model)
  const [llmMax, setLlmMax] = useState(settings.llm_max_files)

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
        examples_per_category: perCategory,
        content_per_category: contentPerCategory,
        gpu_memory_fraction: gpuShare / 100,
        ocr_pdf_pages: ocrPages,
        ocr_images: ocrImages,
        use_download_source: useSource,
        second_opinion: secondOpinion,
        llm_model: llmModel,
        llm_max_files: llmMax,
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

      <section className="card">
        <h3>Reading inbox files</h3>
        <p className="muted small">How much classifiles reads from new files. Everything runs on this PC; only inbox files are read this way, never while indexing.</p>
        <label className="field">
          <span>Scanned PDFs (no text inside): read the first <b>{ocrPages}</b> page{ocrPages === 1 ? '' : 's'} with text recognition (OCR, ~1 s per page; 0 = off)</span>
          <input type="number" min={0} max={5} value={ocrPages} onChange={e => setOcrPages(Number(e.target.value))} />
        </label>
        <label className="check field-check">
          <input type="checkbox" checked={ocrImages} onChange={e => setOcrImages(e.target.checked)} />
          Read text in images and screenshots (OCR)
        </label>
        <label className="check field-check">
          <input type="checkbox" checked={useSource} onChange={e => setUseSource(e.target.checked)} />
          Use where a file was downloaded from (Windows records the website; only the domain, e.g. lms.example.edu, is kept)
        </label>
      </section>

      <section className="card">
        <h3>Second opinion</h3>
        <p className="muted small">
          For files the model is unsure about, a local language model takes a second look and the Review list shows
          "✓ both agree" or its own suggestion. It is a hint for reviewing: it never changes the confidence or
          accepts anything. The model runs on this PC only during that step of a scan and is closed afterwards.
        </p>
        <label className="check field-check">
          <input type="checkbox" checked={secondOpinion} onChange={e => setSecondOpinion(e.target.checked)} />
          Ask for a second opinion on unsure files (needs "Learn from my folders")
        </label>
        <label className="field">
          <span>Language model</span>
          <select value={llmModel} onChange={e => setLlmModel(e.target.value)}>
            <option value="Qwen/Qwen3.5-2B">Qwen3.5 2B: light (≈4 GB GPU memory, ~0.6 s per file; one-time download 4.5 GB)</option>
            <option value="Qwen/Qwen3-4B-Instruct-2507">Qwen3 4B: stronger (≈8 GB GPU memory, ~0.9 s per file; download 8 GB)</option>
          </select>
        </label>
        <label className="field">
          <span>At most this many unsure files per scan</span>
          <input type="number" min={1} max={1000} value={llmMax} onChange={e => setLlmMax(Number(e.target.value))} />
        </label>
      </section>

      <section className="card">
        <h3>Learn from my folders: limits</h3>
        <p className="muted small">
          Everything runs on this PC. File contents never leave it. These limits keep the index build gentle:
          it runs at low priority in small batches and can be cancelled.
        </p>
        <label className="field">
          <span>Example files per category (sampled across subfolders)</span>
          <input type="number" min={100} max={20000} step={100} value={perCategory}
                 onChange={e => setPerCategory(Number(e.target.value))} />
        </label>
        <label className="field">
          <span>Documents per category whose text is read (slower on Windows drives)</span>
          <input type="number" min={0} max={5000} step={50} value={contentPerCategory}
                 onChange={e => setContentPerCategory(Number(e.target.value))} />
        </label>
        <label className="field">
          <span>Max GPU memory for classifiles: <b>{gpuShare}%</b> (applies after restarting the server)</span>
          <input type="range" min={10} max={60} step={5} value={gpuShare} onChange={e => setGpuShare(Number(e.target.value))} />
        </label>
      </section>

      <footer className="actions">
        <button className="primary" onClick={save}>Save settings</button>
      </footer>
    </div>
  )
}

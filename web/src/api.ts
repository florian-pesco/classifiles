// Typed client for the classifiles REST API.

export type Status = 'suggested' | 'needs_review' | 'accepted' | 'rejected' | 'moved'
export type Action = 'move' | 'copy' | 'delete'

export interface Alternative { folder: string; probability: number }

export interface Proposal {
  id: number
  scan_id: number
  path: string
  name: string
  kind: string
  size: number
  evidence: { text: string; meta: Record<string, unknown>; modified: string; ext: string }
  target: string | null
  confidence: number
  source: string
  alternatives: Alternative[]
  reason: string
  status: Status
  final_target: string | null
  applied_action: Action | null
  applied_dst: string | null
}

export interface Scan {
  id: number
  inbox: string
  backend: string
  threshold: number
  status: 'running' | 'done' | 'failed'
  max_files: number | null
  only_new: number
  inbox_total: number
  total: number
  processed: number
  error: string | null
  created_at: string
  counts?: Partial<Record<Status, number>>
}

export interface Category {
  name: string
  path: string | null
  description: string
  samples: string[]
  action: Action
  enabled: boolean
  source: 'auto' | 'manual' | 'delete'
  root: string | null
  exists: boolean
  suggest: boolean
  usable: boolean
}

export interface DeleteSettings { enabled: boolean; suggest: boolean; description: string }

export interface CategoriesPayload {
  roots: { path: string }[]
  delete: DeleteSettings
  categories: Category[]
}

/** One category's stored settings, as sent back to the server. */
export interface CategoryEntry {
  path: string
  name?: string
  description?: string
  enabled?: boolean
  action?: 'move' | 'copy'
}

export interface Rule {
  folder: string
  ext?: string | string[]
  kind?: string | string[]
  name?: string
  glob?: string
}

export interface Settings {
  inboxes: string[]
  threshold: number
  max_files: number | null
  backend: string
  laya_model: string
  samples_per_folder: number
}

export interface PlannedMove {
  proposal_id: number
  src: string
  dst: string | null
  action: Action | null
  category: string
  error: string | null
}

export interface ApplyResult {
  batch_id: string | null
  moved: { proposal_id: number; src: string; dst: string; action: Action }[]
  errors: { proposal_id: number; src: string; error: string }[]
}

export interface Batch {
  batch_id: string
  moved_at: string
  total: number
  moved: number
  copied: number
  trashed: number
  undoable: number
}

export interface TrashItem { id: number; src: string; dst: string; moved_at: string; size: number | null }

export interface FsListing { path: string | null; parent: string | null; dirs: string[]; shortcuts: string[] }

async function call<T>(method: string, url: string, body?: unknown): Promise<T> {
  const res = await fetch(url, {
    method,
    headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  if (!res.ok) {
    let detail = res.statusText
    try { detail = (await res.json()).detail ?? detail } catch { /* not JSON */ }
    throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail))
  }
  return res.json() as Promise<T>
}

const q = (params: Record<string, string | number | boolean | undefined>) => {
  const s = new URLSearchParams()
  for (const [k, v] of Object.entries(params)) if (v !== undefined) s.set(k, String(v))
  return s.toString()
}

export const api = {
  settings: () => call<Settings>('GET', '/api/settings'),
  saveSettings: (patch: Partial<Settings>) => call<Settings>('PATCH', '/api/settings', patch),

  categories: (refresh = false) => call<CategoriesPayload>('GET', `/api/categories?${q({ refresh })}`),
  saveCategories: (roots: { path: string }[], categories: CategoryEntry[], del: DeleteSettings) =>
    call<CategoriesPayload>('PUT', '/api/categories', { roots, categories, delete: del }),
  rules: () => call<{ rules: Rule[]; kinds: string[] }>('GET', '/api/rules'),
  saveRules: (rules: Rule[]) => call<{ rules: Rule[]; kinds: string[] }>('PUT', '/api/rules', { rules }),

  browse: (path?: string) => call<FsListing>('GET', `/api/fs?${q({ path })}`),

  scans: () => call<Scan[]>('GET', '/api/scans'),
  scan: (id: number) => call<Scan>('GET', `/api/scans/${id}`),
  startScan: (inbox: string | undefined, maxFiles: number | undefined, onlyNew: boolean) =>
    call<Scan>('POST', '/api/scans', { inbox, max_files: maxFiles ?? 0, only_new: onlyNew }),

  proposals: (scanId: number) => call<Proposal[]>('GET', `/api/proposals?${q({ scan_id: scanId })}`),
  review: (id: number, action: 'accept' | 'reject' | 'reset', folder?: string) =>
    call<Proposal>('PATCH', `/api/proposals/${id}`, { action, folder }),
  acceptSuggested: (scanId: number) =>
    call<{ accepted: number }>('POST', '/api/proposals/accept-suggested', { scan_id: scanId }),
  fileUrl: (id: number) => `/api/proposals/${id}/file`,
  openFile: (id: number, reveal = false) => call<{ ok: boolean }>('POST', `/api/proposals/${id}/open?${q({ reveal })}`),

  planApply: (scanId: number) => call<PlannedMove[]>('POST', '/api/apply/plan', { scan_id: scanId }),
  apply: (scanId: number) => call<ApplyResult>('POST', '/api/apply', { scan_id: scanId }),
  batches: () => call<Batch[]>('GET', '/api/batches'),
  undo: (batchId?: string) =>
    call<{ batch_id: string | null; restored: unknown[]; errors: { error: string }[] }>(
      'POST', `/api/undo?${q({ batch_id: batchId })}`),
  trash: () => call<TrashItem[]>('GET', '/api/trash'),
  emptyTrash: () => call<{ purged: number; errors: { error: string }[] }>('POST', '/api/trash/empty'),
}

export function humanSize(n: number | null | undefined) {
  if (n == null) return '—'
  const units = ['B', 'KB', 'MB', 'GB']
  let i = 0
  while (n >= 1024 && i < units.length - 1) { n /= 1024; i++ }
  return `${n.toFixed(i ? 1 : 0)} ${units[i]}`
}

export function shortDate(iso: string) {
  return iso.replace('T', ' ').slice(0, 16)
}

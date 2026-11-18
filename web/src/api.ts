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
  suggested_subpath: string | null
  subpath: string | null
  new_folder: string | null
  suggested_name: string | null // suggested new file name (null: none)
  new_name: string | null // the name apply will use (null: keep the original)
  name_reason: string | null
  knn_confidence: number | null
  llm_choice: string | null
  llm_confidence: number | null
  duplicate_of: string | null
  duplicate_kind: 'sorted' | 'inbox' | null
  duplicate_path: string | null
  relearned: number // 1: updated from the user's decisions in this scan
}

/** A suggested group of files to review together (name pattern, similar content, same proposal/doubt). */
export interface ReviewBatch {
  id: string
  kind: 'name' | 'content' | 'proposal' | 'doubt'
  title: string
  why: string
  ids: number[]
  size: number
  undecided: number
  distribution: { where: string; count: number }[]
  uniform: boolean
}

export type ReviewAction = 'accept' | 'reject' | 'reset'

export interface Scan {
  id: number
  inbox: string
  backend: string
  threshold: number
  status: 'running' | 'done' | 'failed'
  phase: 'classify' | 'second_opinion' | 'naming' | null
  llm_total: number
  llm_done: number
  llm_error: string | null
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
  unreachable_roots: string[]
}

export interface QualitySummary {
  files: number
  accuracy?: number
  top3?: number
  by_decided_via?: Record<string, { files: number; accuracy: number }>
  calibration?: { range: string; files: number; accuracy: number | null }[]
}

export interface Quality {
  decisions: number
  decided_via: Record<string, number>
  recorded: Record<string, QualitySummary>
  current?: QualitySummary
  mix?: { weight: number; neighbours: QualitySummary; mixed: QualitySummary }
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
  embedding_model: string
  examples_per_category: number
  content_per_category: number
  gpu_memory_fraction: number
  cpu_threads: number
  ocr_pdf_pages: number
  ocr_images: boolean
  use_download_source: boolean
  second_opinion: boolean
  image_similarity: boolean
  image_weight: number
  images_per_category: number
  detect_duplicates: boolean
  duplicates_in_sorted: boolean
  search_content: boolean
  llm_model: string
  llm_max_files: number
  relearn_in_scan: boolean
  trained_weight: number
  name_suggestions: boolean
  name_llm: boolean
  use_suggested_names: boolean
  name_transliterate: boolean
  name_media: boolean
  name_media_labels: boolean
  name_media_place: boolean
}

export interface IndexStatus {
  status: {
    state: 'idle' | 'running' | 'done' | 'failed' | 'cancelled'
    message?: string
    category?: string | null
    done?: number
    total?: number
    added?: number
    reused?: number
    removed?: number
    error?: string | null
    finished_at?: string
  }
  model: string
  model_loaded: boolean
  total: number
  categories: { name: string; usable: boolean; examples: number; learned: number }[]
  unlearned: number
  catalog: { state: string; message?: string; files: number; enabled: boolean; done?: number; error?: string }
  images: { enabled: boolean; state?: string; message?: string; total?: number; done?: number; error?: string;
            per_category?: Record<string, number> }
  trained: { state: string; message?: string; error?: string; weight: number; trained: boolean;
             trained_at: string | null; examples: number; categories: string[] }
}

export interface PlannedMove {
  proposal_id: number
  src: string
  dst: string | null
  action: Action | null
  category: string
  subpath: string
  renamed: boolean
  creates: string | null
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


export interface DuplicateSide {
  label: string
  exists: boolean
  size?: number
  modified?: string
  fingerprint?: string
}

export interface DuplicateDetails {
  kind: 'sorted' | 'inbox'
  this: DuplicateSide
  other: DuplicateSide
  identical_now: boolean
  keep_reason: string
  method: string
}

export interface Move {
  id: number
  batch_id: string
  proposal_id: number | null
  src: string
  dst: string
  action: Action
  category_key: string | null
  created_dir: string | null
  moved_at: string
  undone_at: string | null
  purged_at: string | null
}

/** A file in the trash: its delete move, plus the size now (null: no longer there). */
export type TrashItem = Move & { size: number | null }

export interface FsListing { path: string | null; parent: string | null; dirs: string[]; shortcuts: string[] }
export interface SubfolderListing { path: string; dirs: string[]; exists?: boolean }

export interface RulesPayload { rules: Rule[]; kinds: string[] }
export interface LearnPastResult { learned: number; by_action: Record<string, number> }
export type ReviewResult = Proposal & { relearned_others: Proposal[] }
export interface BulkReviewResult {
  updated: Proposal[]
  errors: { proposal_id: number; error: string }[]
  relearned: Proposal[]
}
export interface UndoResult {
  batch_id: string | null
  restored: { move_id: number; path: string }[]
  errors: { move_id: number; error: string }[]
}
export interface EmptyTrashResult { purged: number; errors: { move_id: number; error: string }[] }
export interface Ok { ok: boolean }

/** One file of a set of identical files (Duplicates page). `fit`: how well its folder fits (null: not in the index). */
export interface DuplicateCopy {
  id: number
  category: string
  relpath: string
  label: string
  folder: string
  mtime: number
  fit: number | null
}

export interface DuplicateGroup {
  hash: string
  size: number
  wasted: number
  spread: 'folder' | 'category' | 'categories'
  keep: number | null
  reason: string
  clear: boolean
  copies: DuplicateCopy[]
}

export interface FolderPair {
  a: string; b: string; a_label: string; b_label: string
  a_files: number; b_files: number; files: number; bytes: number
}

export interface DuplicateSearch {
  state: 'idle' | 'running' | 'done' | 'cancelled' | 'failed'
  root?: string | null
  phase?: 'listing' | 'comparing' | 'verifying'
  message?: string | null
  done?: number
  total?: number
  bytes_done?: number
  bytes_total?: number
  eta_seconds?: number | null
  finished_at?: string
  groups?: number
  error?: string
}

export interface DuplicateOverview {
  status: DuplicateSearch
  summary: { groups: number; copies: number; bytes: number; clear: number; ignored: number }
  groups: DuplicateGroup[]
  pairs: FolderPair[]
}

export interface TrashResult { batch_id: string | null; trashed: number; bytes: number; errors: { hash: string; error: string }[] }

export type SearchGroup = 'documents' | 'pictures' | 'videos' | 'audio' | 'archives' | 'installers' | 'code' | 'other'

export interface SearchRequest {
  text: string
  groups: SearchGroup[]
  ext: string
  after: number | null
  before: number | null
  location: string | null
  match: 'all' | 'name' | 'content'
  sort: 'relevance' | 'newest' | 'oldest' | 'largest' | 'name'
  limit: number
}

/** One search hit. `snippet`: content around the match, matched words between \u0002 and \u0003. */
export interface SearchResult {
  id: number
  name: string
  folder: string
  path: string
  location: string
  kind: string
  ext: string
  size: number
  modified: string
  in_name: boolean
  in_folder: boolean
  in_content: boolean
  snippet: string | null
}

export interface SearchResponse { total: number; more: boolean; results: SearchResult[]; took_ms: number }

export interface SearchStatus {
  state: 'idle' | 'running' | 'done' | 'cancelled' | 'failed'
  phase?: 'listing' | 'reading'
  message?: string | null
  done?: number
  total?: number
  eta_seconds?: number | null
  finished_at?: string
  error?: string
  files: number
  texts: number
  locations: string[]
  content: boolean
}

/** A failed request: `message` is the server's explanation (FastAPI `detail`), `status` the HTTP status. */
export class ApiError extends Error {
  readonly status: number

  constructor(status: number, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

async function call<T>(method: string, url: string, body?: unknown): Promise<T> {
  const res = await fetch(url, {
    method,
    headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  if (!res.ok) {
    let detail = res.statusText
    try { detail = (await res.json()).detail ?? detail } catch { /* not JSON */ }
    throw new ApiError(res.status, typeof detail === 'string' ? detail : JSON.stringify(detail))
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
  rules: () => call<RulesPayload>('GET', '/api/rules'),
  saveRules: (rules: Rule[]) => call<RulesPayload>('PUT', '/api/rules', { rules }),

  index: () => call<IndexStatus>('GET', '/api/index'),
  buildIndex: () => call<IndexStatus>('POST', '/api/index/build'),
  cancelIndex: () => call<IndexStatus>('POST', '/api/index/cancel'),
  retrain: () => call<IndexStatus['trained']>('POST', '/api/index/retrain'),
  learnPast: () => call<LearnPastResult>('POST', '/api/index/learn-past'),

  browse: (path?: string) => call<FsListing>('GET', `/api/fs?${q({ path })}`),

  scans: () => call<Scan[]>('GET', '/api/scans'),
  cancelSecondOpinion: () => call<Ok>('POST', '/api/scans/second-opinion/cancel'),
  scan: (id: number) => call<Scan>('GET', `/api/scans/${id}`),
  startScan: (inbox: string | undefined, maxFiles: number | undefined, onlyNew: boolean) =>
    call<Scan>('POST', '/api/scans', { inbox, max_files: maxFiles ?? 0, only_new: onlyNew }),

  proposals: (scanId: number) => call<Proposal[]>('GET', `/api/proposals?${q({ scan_id: scanId })}`),
  review: (id: number, action: ReviewAction, folder?: string, subpath?: string,
           name?: string, useSuggestedName?: boolean) =>
    call<ReviewResult>('PATCH', `/api/proposals/${id}`,
      { action, folder, subpath, name, use_suggested_name: useSuggestedName }),
  bulkReview: (ids: number[], action: ReviewAction, folder?: string, subpath?: string, useSuggestedName?: boolean) =>
    call<BulkReviewResult>('POST', '/api/proposals/bulk', { ids, action, folder, subpath, use_suggested_name: useSuggestedName }),
  reviewBatches: (scanId: number) => call<ReviewBatch[]>('GET', `/api/scans/${scanId}/batches`),
  acceptSuggested: (scanId: number, topLevel = false) =>
    call<{ accepted: number }>('POST', '/api/proposals/accept-suggested', { scan_id: scanId, top_level: topLevel }),
  subfolders: (category: string, path = '') =>
    call<SubfolderListing>('GET', `/api/categories/subfolders?${q({ category, path })}`),
  fileUrl: (id: number) => `/api/proposals/${id}/file`,
  duplicateDetails: (id: number) => call<DuplicateDetails>('GET', `/api/proposals/${id}/duplicate`),
  openFile: (id: number, reveal = false, duplicate = false) =>
    call<Ok>('POST', `/api/proposals/${id}/open?${q({ reveal, duplicate })}`),

  planApply: (scanId: number) => call<PlannedMove[]>('POST', '/api/apply/plan', { scan_id: scanId }),
  apply: (scanId: number) => call<ApplyResult>('POST', '/api/apply', { scan_id: scanId }),
  batches: () => call<Batch[]>('GET', '/api/batches'),
  quality: (rerun = false) => call<Quality>('GET', `/api/quality?${q({ rerun })}`),
  moves: () => call<Move[]>('GET', '/api/moves'),
  undo: (batchId?: string) => call<UndoResult>('POST', `/api/undo?${q({ batch_id: batchId })}`),
  trash: () => call<TrashItem[]>('GET', '/api/trash'),
  duplicates: (root?: string) => call<DuplicateOverview>('GET', `/api/duplicates?${q({ root })}`),
  ignoredDuplicates: (root?: string) => call<DuplicateGroup[]>('GET', `/api/duplicates/ignored?${q({ root })}`),
  duplicateSearch: () => call<DuplicateSearch>('GET', '/api/duplicates/search'),
  searchDuplicates: (root?: string) => call<DuplicateSearch>('POST', '/api/duplicates/search', { root }),
  cancelDuplicateSearch: () => call<DuplicateSearch>('POST', '/api/duplicates/search/cancel'),
  resolveDuplicates: (decisions: { hash: string; keep: number[] }[], root?: string) =>
    call<TrashResult>('POST', '/api/duplicates/resolve', { decisions, root }),
  resolveSuggested: (root?: string) => call<TrashResult>('POST', '/api/duplicates/resolve-suggested', { root }),
  resolveFolders: (keepFolder: string, dropFolder: string, root?: string) =>
    call<TrashResult>('POST', '/api/duplicates/resolve-folders', { keep_folder: keepFolder, drop_folder: dropFolder, root }),
  ignoreDuplicates: (hashes: string[], ignored = true) => call<Ok>('POST', '/api/duplicates/ignore', { hashes, ignored }),
  openCopy: (id: number, reveal = false) => call<Ok>('POST', `/api/duplicates/${id}/open?${q({ reveal })}`),
  search: (req: SearchRequest) => call<SearchResponse>('POST', '/api/search', req),
  searchStatus: () => call<SearchStatus>('GET', '/api/search/index'),
  updateSearch: () => call<SearchStatus>('POST', '/api/search/index/update'),
  cancelSearchUpdate: () => call<SearchStatus>('POST', '/api/search/index/cancel'),
  openResult: (id: number, reveal = false) => call<Ok>('POST', `/api/search/${id}/open?${q({ reveal })}`),
  emptyTrash: () => call<EmptyTrashResult>('POST', '/api/trash/empty'),
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

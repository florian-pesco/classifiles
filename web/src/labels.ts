import type { Status } from './api'

export const STATUS_LABEL: Record<Status, string> = {
  needs_review: 'Needs review',
  suggested: 'Suggested',
  accepted: 'Accepted',
  rejected: 'Kept in inbox',
  moved: 'Done',
}

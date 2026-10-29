import type { IconName } from './Icon'

/** The icon for a file kind. */
export function kindIcon(kind: string): IconName {
  switch (kind) {
    case 'pdf': case 'document': case 'text': case 'presentation': return 'doc'
    case 'spreadsheet': case 'data': return 'sheet'
    case 'image': return 'image'
    case 'video': return 'film'
    case 'audio': return 'music'
    case 'archive': return 'archive'
    case 'code': return 'code'
    case 'installer': return 'package'
    default: return 'file'
  }
}

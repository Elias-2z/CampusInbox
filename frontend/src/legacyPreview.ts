import { request } from '@/api'

export interface LegacyPreview {
  counts: { messages: number; proposals: number; tasks: number; history: number; materials: number }
  issues: {
    unknown_message_time: number
    multi_source_tasks: number
    messages_shared_by_tasks: number
    missing_task_sources: number
    duplicate_message_ids: number
    duplicate_task_ids: number
    missing_message_ids: number
    missing_task_ids: number
    attachment_references: number
  }
}

export async function previewLegacyExport(file: File, send: typeof request = request) {
  if (!/\.json$/i.test(file.name) || file.size <= 0 || file.size > 10 * 1024 * 1024)
    throw new Error('请选择不超过 10 MB 的旧版 JSON 导出文件。')
  return send<LegacyPreview>('/migrations/legacy/preview', {
    method: 'POST',
    headers: { 'Content-Type': 'application/octet-stream' },
    body: file,
  })
}

export interface MigrationPlan extends LegacyPreview {
  namespace: string
  can_import: boolean
  plan_token: string
  new: Record<string, number>
  skipped: Record<string, number>
  conflicts: Record<string, number>
  possible_task_collisions: number
  originals: { available: number; missing: number }
}

export interface MigrationResult {
  imported: Record<string, number>
  skipped: Record<string, number>
  backup: string | null
}

function migrationFile(file: File, namespace: string) {
  if (!/^[A-Za-z0-9_-]{1,64}$/.test(namespace))
    throw new Error('数据集名称须为 1–64 位字母、数字、下划线或短横线。')
  const limit = /\.zip$/i.test(file.name) ? 100 : 10
  if (!/\.(json|zip)$/i.test(file.name) || file.size <= 0 || file.size > limit * 1024 * 1024)
    throw new Error('请选择不超过 10 MB 的 JSON 或不超过 100 MB 的迁移 ZIP。')
}

export async function planLegacyMigration(file: File, namespace: string, send = request) {
  migrationFile(file, namespace)
  return send<MigrationPlan>('/migrations/legacy/plan?namespace=' + encodeURIComponent(namespace), {
    method: 'POST',
    headers: { 'Content-Type': 'application/octet-stream' },
    body: file,
  })
}

export async function importLegacyMigration(
  file: File,
  namespace: string,
  token: string,
  send = request,
) {
  migrationFile(file, namespace)
  if (!token) throw new Error('请先检查文件。')
  return send<MigrationResult>(
    '/migrations/legacy/import?namespace=' + encodeURIComponent(namespace),
    {
      method: 'POST',
      headers: {
        'Content-Type': 'application/octet-stream',
        'X-Migration-Plan': token,
        'X-Migration-Confirm': 'import',
      },
      body: file,
    },
  )
}

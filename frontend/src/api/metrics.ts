/** 看板指标管线共享类型，与后端 services/metrics.py 的返回结构对齐。 */

export interface Caliber {
  version: string
  label: string
  status: string
  published_at: string
  window_days: number
  range_rule: string
  include_inactive: boolean
  description: string
}

export interface OverviewItem {
  metric: string
  metric_code: string
  value: number
  unit: string
  caliber_version?: string
  stat_window?: string
  stat_time?: string
}

export interface TrendPoint {
  date: string
  value: number
  caliber_version: string
}

export interface TrendItem {
  metric_id: string
  metric: string
  metric_code: string
  module: string
  module_label: string
  group: string
  series: TrendPoint[]
  source_scope: string
  source_row_count: number
  explanation: string
  caliber_version: string
  stat_window: string
  stat_time: string
}

export interface GroupSummary extends OverviewItem {
  source_modules: string[]
}

export interface TrendGroup {
  group_code: string
  group_name: string
  modules: string[]
  summary: GroupSummary[]
  metrics: TrendItem[]
}

export interface PanelTrend {
  panel_id: number
  panel_code: string
  panel_name: string
  caliber_version: string
  caliber_label: string
  range_rule: string
  stat_window: string
  generated_at: string
  overview: OverviewItem[]
  groups: TrendGroup[]
  trend: TrendItem[]
}

export interface ConflictInfo {
  detected: boolean
  requested_caliber?: string
  current_caliber?: string
  resolution?: string
  switched_at?: string | null
}

export interface Snapshot {
  id: number
  snapshot_no: string
  panel_id: number
  panel_code: string
  panel_name: string
  caliber_version: string
  caliber_label: string
  storage_tier: string
  review_status: string
  range_rule: string
  stat_window: string
  generated_at: string
  source: string
  conflict: ConflictInfo
  overview: OverviewItem[]
  groups: { group_name: string; modules: string[]; summary: GroupSummary[] }[]
  trend: TrendItem[]
}

export interface RefreshResult {
  ok: boolean
  message: string
  reused: boolean
  conflict: ConflictInfo | null
  switched_at?: string | null
  trend: PanelTrend | null
  snapshot?: Snapshot | null
}

export interface ChecklistItem {
  id: number
  metric_id: string
  metric: string
  metric_code: string
  module: string
  module_label: string
  group: string
  value: number
  unit: string
  aggregation: string
  source_scope: string
  source_modules: string
  source_row_count: number
  explanation: string
  caliber_version: string
  stat_window: string
  stat_time: string
}

export interface RefreshLog {
  id: number
  panel_id: number
  panel_code: string
  requested_caliber: string | null
  effective_caliber: string | null
  result: string
  reason: string
  message: string
  source: string
  stat_time: string
  conflict?: ConflictInfo
  snapshot_no?: string
}

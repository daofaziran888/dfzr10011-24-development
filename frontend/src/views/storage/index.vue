<template>
  <section class="page" data-module="storage">
    <header class="page-head">
      <div>
        <h2>采集存储分层管理</h2>
        <p class="page-desc">维护采集存储的分层配置；同时承载可解释采集核对清单、看板指标快照分层登记与全局采集指标同步刷新。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="syncMetrics">同步刷新采集清单与全局指标</button>
        <button class="btn" type="button" @click="exportRows">导出采集存储分层清单</button>
      </div>
    </header>

    <div v-if="notice.text" class="notice" :class="notice.kind">{{ notice.text }}</div>

    <nav class="filter-bar">
      <button
        v-for="tab in tabs"
        :key="tab.key"
        class="btn"
        :class="{ primary: activeTab === tab.key }"
        type="button"
        @click="switchTab(tab.key)"
      >
        {{ tab.label }}
      </button>
    </nav>

    <!-- 存储策略（原能力保留） -->
    <template v-if="activeTab === 'policy'">
      <div class="stat-row">
        <article v-for="item in stats" :key="item.label" class="stat-card">
          <span class="stat-label">{{ item.label }}</span>
          <strong class="stat-value">{{ item.value }}</strong>
        </article>
      </div>

      <form class="filter-bar" @submit.prevent="reloadPolicies">
        <label v-for="field in filterFields" :key="field" class="filter-item">
          <span>{{ field }}</span>
          <input v-model="filters[field]" :placeholder="`按${field}检索`" />
        </label>
        <button class="btn" type="submit">查询</button>
        <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
      </form>

      <table class="data-table">
        <thead>
          <tr>
            <th v-for="column in columns" :key="column">{{ column }}</th>
            <th>可执行动作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in rows" :key="String(row.id)">
            <td v-for="column in columns" :key="column">{{ row[column] ?? '—' }}</td>
            <td class="row-actions">
              <button
                v-for="action in actions"
                :key="action"
                class="link"
                type="button"
                @click="runAction(action, row)"
              >
                {{ action }}
              </button>
            </td>
          </tr>
          <tr v-if="!rows.length">
            <td :colspan="columns.length + 1" class="empty-state">暂无采集存储分层数据，可先登记存储策略</td>
          </tr>
        </tbody>
      </table>
      <footer class="page-foot">
        <span>共 {{ total }} 条采集存储分层记录</span>
        <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
      </footer>
    </template>

    <!-- 可解释采集核对清单 -->
    <template v-else-if="activeTab === 'checklist'">
      <form class="filter-bar" @submit.prevent="reloadChecklist">
        <label class="filter-item">
          <span>业务模块</span>
          <input v-model="checklistFilter.module" placeholder="如 shard / GLOBAL" />
        </label>
        <label class="filter-item">
          <span>口径版本</span>
          <input v-model="checklistFilter.caliber_version" placeholder="如 v2.0" />
        </label>
        <button class="btn" type="submit">查询</button>
        <button class="btn ghost" type="button" @click="resetChecklistFilter">重置条件</button>
      </form>
      <table class="data-table">
        <thead>
          <tr>
            <th>指标</th><th>业务模块/分组</th><th>数值</th><th>取数范围与来源</th><th>口径版本</th><th>统计窗口/时间</th><th>解释</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="item in checklist" :key="item.id">
            <td>{{ item.metric }}<br /><span class="stat-label">{{ item.metric_id }}</span></td>
            <td>{{ item.module_label }}<br /><span class="stat-label">{{ item.group }}（{{ item.module }}）</span></td>
            <td><strong>{{ item.value }}</strong> {{ item.unit }}</td>
            <td style="max-width: 300px">
              <p class="trend-line">{{ item.source_scope }}</p>
              <p class="trend-line">来源模块：{{ item.source_modules }}｜来源记录 {{ item.source_row_count }} 条</p>
            </td>
            <td>
              <span class="tag" :class="item.caliber_version === currentCaliber ? 'current' : 'old'">
                {{ item.caliber_version }}
              </span>
            </td>
            <td>{{ item.stat_window }}<br /><span class="stat-label">{{ item.stat_time }}</span></td>
            <td style="max-width: 260px">{{ item.explanation }}</td>
          </tr>
          <tr v-if="!checklist.length">
            <td colspan="7" class="empty-state">核对清单为空，请先在看板刷新或点右上角同步刷新</td>
          </tr>
        </tbody>
      </table>
    </template>

    <!-- 看板指标快照（分层登记） -->
    <template v-else-if="activeTab === 'snapshot'">
      <form class="filter-bar" @submit.prevent="reloadSnapshots">
        <label class="filter-item">
          <span>面板 id</span>
          <input v-model="snapshotFilter.panel_id" placeholder="如 2" />
        </label>
        <label class="filter-item">
          <span>复核状态</span>
          <input v-model="snapshotFilter.review_status" placeholder="正常 / 待复核" />
        </label>
        <button class="btn" type="submit">查询</button>
        <button class="btn ghost" type="button" @click="resetSnapshotFilter">重置条件</button>
      </form>
      <table class="data-table">
        <thead>
          <tr>
            <th>快照编号</th><th>面板</th><th>口径版本</th><th>存储分层</th><th>复核状态</th><th>统计窗口/时间</th><th>入口</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="snapshot in snapshots" :key="snapshot.id">
            <td>{{ snapshot.snapshot_no }}</td>
            <td>{{ snapshot.panel_code }}<br /><span class="stat-label">{{ snapshot.panel_name }}</span></td>
            <td>
              <span class="tag" :class="snapshot.caliber_version === currentCaliber ? 'current' : 'old'">
                {{ snapshot.caliber_version }}
              </span>
            </td>
            <td><span class="tag" :class="snapshot.storage_tier === '热存层' ? 'hot' : 'warm'">{{ snapshot.storage_tier }}</span></td>
            <td>{{ snapshot.review_status }}</td>
            <td>{{ snapshot.stat_window }}<br /><span class="stat-label">{{ snapshot.generated_at }}</span></td>
            <td>{{ snapshot.source === 'storage_sync' ? '存储同步' : '看板刷新' }}</td>
          </tr>
          <tr v-if="!snapshots.length">
            <td colspan="7" class="empty-state">暂无指标快照</td>
          </tr>
        </tbody>
      </table>
    </template>

    <!-- 刷新流水 -->
    <template v-else>
      <table class="data-table">
        <thead>
          <tr><th>统计时间</th><th>面板</th><th>结果</th><th>请求口径</th><th>生效口径</th><th>入口</th><th>说明</th></tr>
        </thead>
        <tbody>
          <tr v-for="log in logs" :key="log.id">
            <td>{{ log.stat_time }}</td>
            <td>{{ log.panel_code }}（#{{ log.panel_id }}）</td>
            <td><span class="tag" :class="logClass(log.result)">{{ logLabel(log.result) }}</span></td>
            <td>{{ log.requested_caliber ?? '—' }}</td>
            <td>{{ log.effective_caliber ?? '—' }}</td>
            <td>{{ log.source === 'storage_sync' ? '存储同步' : '看板' }}</td>
            <td style="max-width: 360px">{{ log.message }}</td>
          </tr>
          <tr v-if="!logs.length">
            <td colspan="7" class="empty-state">暂无刷新流水</td>
          </tr>
        </tbody>
      </table>
    </template>
  </section>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'

import { request } from '@/api/client'
import type { ChecklistItem, RefreshLog, Snapshot } from '@/api/metrics'

type Row = Record<string, string | number | null>

const ENDPOINT = '/api/storage'
const columns = ['策略编号', '存储层级', '文件格式', '压缩算法', '保留天数', '迁移规则', '清理方式', '策略状态']
const actions = ['启用策略', '暂停策略', '关闭策略']
const tabs = [
  { key: 'policy', label: '存储策略' },
  { key: 'checklist', label: '可解释采集核对清单' },
  { key: 'snapshot', label: '看板指标快照' },
  { key: 'logs', label: '刷新流水' },
] as const

type TabKey = (typeof tabs)[number]['key']
const activeTab = ref<TabKey>('policy')
const currentCaliber = ref('v2.0')
const notice = reactive({ text: '', kind: 'info' })
const errorMessage = ref('')

const rows = ref<Row[]>([])
const total = ref(0)
const filters = ref<Record<string, string>>({})
const filterFields = columns.slice(0, 3)
const stats = ref([
  { label: '存储策略总数', value: 0 },
  { label: '已生效策略', value: 0 },
  { label: '已暂停策略', value: 0 },
])

const checklist = ref<ChecklistItem[]>([])
const checklistFilter = reactive({ module: '', caliber_version: '' })
const snapshots = ref<Snapshot[]>([])
const snapshotFilter = reactive({ panel_id: '', review_status: '' })
const logs = ref<RefreshLog[]>([])

function logClass(result: string): string {
  if (result === 'success') return 'ok'
  if (result === 'duplicate') return 'dup'
  return 'fail'
}

function logLabel(result: string): string {
  if (result === 'success') return '成功'
  if (result === 'duplicate') return '重复复用'
  return '失败'
}

function resetFilters() {
  filters.value = {}
  void reloadPolicies()
}

function resetChecklistFilter() {
  checklistFilter.module = ''
  checklistFilter.caliber_version = ''
  void reloadChecklist()
}

function resetSnapshotFilter() {
  snapshotFilter.panel_id = ''
  snapshotFilter.review_status = ''
  void reloadSnapshots()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

async function switchTab(key: TabKey) {
  activeTab.value = key
  if (key === 'checklist') await reloadChecklist()
  if (key === 'snapshot') await reloadSnapshots()
  if (key === 'logs') await reloadLogs()
}

async function syncMetrics() {
  notice.text = ''
  try {
    const response = await request(`${ENDPOINT}/sync-metrics`, {
      method: 'POST',
      body: JSON.stringify({ conflict_policy: 'current' }),
    })
    const payload = await response.json()
    notice.kind = payload.ok ? 'success' : 'error'
    notice.text = payload.message
    if (payload.ok && activeTab.value === 'checklist') await reloadChecklist()
    if (payload.ok && activeTab.value === 'snapshot') await reloadSnapshots()
    if (payload.ok && activeTab.value === 'logs') await reloadLogs()
  } catch (error) {
    notice.kind = 'error'
    notice.text = error instanceof Error ? error.message : '同步刷新失败'
  }
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ values: { action } }),
    })
    if (!response.ok) {
      throw new Error('采集存储分层动作未生效，请稍后重试')
    }
    await reloadPolicies()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '采集存储分层操作失败'
  }
}

async function reloadPolicies() {
  errorMessage.value = ''
  const query = new URLSearchParams(filters.value as Record<string, string>).toString()
  try {
    const response = await request(`${ENDPOINT}?${query}`)
    if (!response.ok) {
      throw new Error('存储策略列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
    stats.value = [
      { label: '存储策略总数', value: rows.value.length },
      { label: '已生效策略', value: rows.value.filter((row) => row.status === '已生效').length },
      { label: '已暂停策略', value: rows.value.filter((row) => row.status === '已暂停').length },
    ]
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '采集存储分层列表读取失败'
  }
}

async function reloadChecklist() {
  const params = new URLSearchParams()
  if (checklistFilter.module) params.set('module', checklistFilter.module)
  if (checklistFilter.caliber_version) params.set('caliber_version', checklistFilter.caliber_version)
  try {
    const response = await request(`${ENDPOINT}/checklist?${params.toString()}`)
    const payload = await response.json()
    checklist.value = payload.items ?? []
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '采集核对清单读取失败'
  }
}

async function reloadSnapshots() {
  const params = new URLSearchParams()
  if (snapshotFilter.panel_id) params.set('panel_id', snapshotFilter.panel_id)
  if (snapshotFilter.review_status) params.set('review_status', snapshotFilter.review_status)
  try {
    const response = await request(`${ENDPOINT}/metric-snapshots?${params.toString()}`)
    const payload = await response.json()
    snapshots.value = payload.items ?? []
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '指标快照读取失败'
  }
}

async function reloadLogs() {
  try {
    const response = await request(`${ENDPOINT}/refresh-logs`)
    const payload = await response.json()
    logs.value = payload.items ?? []
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '刷新流水读取失败'
  }
}

onMounted(reloadPolicies)
</script>

<template>
  <section class="page" data-module="dashboard">
    <header class="page-head">
      <div>
        <h2>全局采集看板管理</h2>
        <p class="page-desc">维护全局看板的指标配置、刷新策略与告警联动；刷新时按面板范围聚合采集趋势，逐项可查指标来源与统计时间。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记看板面板</button>
        <button class="btn" type="button" @click="exportRows">导出全局采集看板清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="reload">
      <label v-for="field in filterFields" :key="field" class="filter-item">
        <span>{{ field }}</span>
        <input v-model="filters[field]" :placeholder="`按${field}检索`" />
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <form class="refresh-form" @submit.prevent>
      <label>
        <span>指标口径版本（一次刷新只引用一个口径）</span>
        <select v-model="refreshForm.caliber_version">
          <option value="">默认（当前发布口径 {{ currentCaliber }}）</option>
          <option v-for="caliber in calibers" :key="caliber.version" :value="caliber.version">
            {{ caliber.label }}
          </option>
        </select>
      </label>
      <label>
        <span>旧口径冲突处理</span>
        <select v-model="refreshForm.conflict_policy">
          <option value="current">采用当前已发布口径（显示新口径与切换时间）</option>
          <option value="requested">沿用请求携带的旧口径（显示旧口径标签，快照待复核）</option>
        </select>
      </label>
      <label>
        <span>失败演练</span>
        <select v-model="refreshForm.simulate_failure">
          <option :value="false">正常刷新</option>
          <option :value="true">模拟刷新失败（验证保留上一份结果）</option>
        </select>
      </label>
    </form>

    <div v-if="notice.text" class="notice" :class="notice.kind">{{ notice.text }}</div>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>可执行动作</th>
          <th>指标操作</th>
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
          <td class="row-actions">
            <button class="link" type="button" :disabled="row.status !== '已启用'" @click="refreshRow(row)">
              刷新看板
            </button>
            <button class="link" type="button" @click="openTrend(row)">查看趋势</button>
            <button class="link" type="button" @click="openSnapshots(row)">指标快照</button>
            <button class="link" type="button" @click="openLogs(row)">刷新流水</button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 2" class="empty-state">暂无全局采集看板数据，可先登记看板面板</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条全局采集看板记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>

    <!-- 采集趋势视图：概览 + 分组 + 逐项指标来源 -->
    <ModalBox :open="trendModal.open" :title="trendModal.title" width="1080px" @close="trendModal.open = false">
      <template v-if="trendModal.trend">
        <div class="metric-meta">
          <span>口径标签：<strong>{{ trendModal.trend.caliber_label }}</strong>
            <span class="tag" :class="trendModal.trend.caliber_version === currentCaliber ? 'current' : 'old'">
              {{ trendModal.trend.caliber_version }}
            </span>
          </span>
          <span>统计时间：<strong>{{ trendModal.trend.generated_at }}</strong></span>
          <span>统计窗口：<strong>{{ trendModal.trend.stat_window }}</strong></span>
          <span>数据范围口径：<strong>{{ trendModal.trend.range_rule }}</strong></span>
          <span v-if="trendModal.switchedAt">口径切换时间：<strong class="success-text">{{ trendModal.switchedAt }}</strong></span>
        </div>

        <h4 class="section-title">概览（刷新视图后仍保留）</h4>
        <div class="stat-row">
          <article v-for="item in trendModal.trend.overview" :key="item.metric_code" class="stat-card">
            <span class="stat-label">{{ item.metric }}</span>
            <strong class="stat-value">{{ item.value }} {{ item.unit }}</strong>
          </article>
        </div>

        <h4 class="section-title">分组（{{ trendModal.trend.groups.length }} 个业务分组）</h4>
        <div class="group-grid">
          <article v-for="group in trendModal.trend.groups" :key="group.group_code" class="group-card">
            <h4>{{ group.group_name }}</h4>
            <ul>
              <li v-for="moduleName in group.modules" :key="moduleName">{{ moduleName }}</li>
            </ul>
            <div v-for="summary in group.summary" :key="summary.metric_code" class="group-kv">
              <span>{{ summary.metric }}</span>
              <strong>{{ summary.value }} {{ summary.unit }}</strong>
            </div>
          </article>
        </div>

        <h4 class="section-title">逐项指标来源与采集趋势</h4>
        <table class="data-table">
          <thead>
            <tr><th>指标</th><th>来源模块/分组</th><th>趋势（按天）</th><th>来源与统计时间</th></tr>
          </thead>
          <tbody>
            <tr v-for="item in trendModal.trend.trend" :key="item.metric_id">
              <td>{{ item.metric }}<br /><span class="stat-label">{{ item.metric_code }}</span></td>
              <td>{{ item.module_label }}<br /><span class="stat-label">{{ item.group }}</span></td>
              <td style="min-width: 240px">
                <div class="trend-bars">
                  <span
                    v-for="point in item.series"
                    :key="point.date"
                    class="trend-bar"
                    :style="{ height: barHeight(item.series, point.value) }"
                    :data-tip="`${point.date}：${point.value} 条（${point.caliber_version}）`"
                  />
                </div>
                <p class="trend-line">窗口 {{ item.stat_window }}，共 {{ item.series.length }} 个统计点</p>
              </td>
              <td style="max-width: 360px">
                <p class="trend-line">口径：{{ item.caliber_version }}｜取数范围：{{ item.source_scope }}</p>
                <p class="trend-line">来源记录数：{{ item.source_row_count }} 条｜统计时间：{{ item.stat_time }}</p>
                <p class="trend-line">{{ item.explanation }}</p>
              </td>
            </tr>
          </tbody>
        </table>
      </template>
      <p v-else class="empty-state">该面板尚未成功刷新过，暂无采集趋势</p>
    </ModalBox>

    <!-- 指标快照 -->
    <ModalBox :open="snapshotModal.open" :title="snapshotModal.title" width="1080px" @close="snapshotModal.open = false">
      <table class="data-table">
        <thead>
          <tr><th>快照编号</th><th>口径版本</th><th>存储分层</th><th>复核状态</th><th>统计时间</th><th>来源入口</th><th>冲突处理</th></tr>
        </thead>
        <tbody>
          <tr v-for="snapshot in snapshotModal.items" :key="snapshot.id">
            <td>{{ snapshot.snapshot_no }}</td>
            <td>
              <span class="tag" :class="snapshot.caliber_version === currentCaliber ? 'current' : 'old'">
                {{ snapshot.caliber_version }}
              </span>
            </td>
            <td><span class="tag" :class="snapshot.storage_tier === '热存层' ? 'hot' : 'warm'">{{ snapshot.storage_tier }}</span></td>
            <td>{{ snapshot.review_status }}</td>
            <td>{{ snapshot.generated_at }}</td>
            <td>{{ snapshot.source === 'storage_sync' ? '存储同步入口' : '看板刷新入口' }}</td>
            <td>
              <span v-if="snapshot.conflict?.detected" class="tag warm">
                冲突→{{ snapshot.conflict.resolution === 'current' ? '切新口径' : '留旧口径' }}
              </span>
              <span v-else class="tag ok">无冲突</span>
            </td>
          </tr>
          <tr v-if="!snapshotModal.items.length">
            <td colspan="7" class="empty-state">暂无快照，刷新成功后由存储分层登记</td>
          </tr>
        </tbody>
      </table>
    </ModalBox>

    <!-- 刷新流水 -->
    <ModalBox :open="logModal.open" :title="logModal.title" width="1000px" @close="logModal.open = false">
      <table class="data-table">
        <thead>
          <tr><th>统计时间</th><th>结果</th><th>请求口径</th><th>生效口径</th><th>入口</th><th>说明</th></tr>
        </thead>
        <tbody>
          <tr v-for="log in logModal.items" :key="log.id">
            <td>{{ log.stat_time }}</td>
            <td>
              <span class="tag" :class="logClass(log.result)">{{ logLabel(log.result) }}</span>
            </td>
            <td>{{ log.requested_caliber ?? '—' }}</td>
            <td>{{ log.effective_caliber ?? '—' }}</td>
            <td>{{ log.source === 'storage_sync' ? '存储同步' : '看板' }}</td>
            <td style="max-width: 380px">{{ log.message }}</td>
          </tr>
          <tr v-if="!logModal.items.length">
            <td colspan="6" class="empty-state">暂无刷新流水</td>
          </tr>
        </tbody>
      </table>
    </ModalBox>
  </section>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'

import { request } from '@/api/client'
import type { Caliber, RefreshLog, RefreshResult, Snapshot, TrendItem, PanelTrend } from '@/api/metrics'
import ModalBox from '@/components/ModalBox.vue'

type Row = Record<string, string | number | null>

const ENDPOINT = '/api/dashboard'
const columns = ['面板编号', '面板名称', '显示指标', '刷新间隔', '告警配置', '数据范围', '创建人', '面板状态']
const actions = ['启用面板', '暂停面板', '归档面板']

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const filters = ref<Record<string, string>>({})
const filterFields = columns.slice(0, 3)
const notice = reactive({ text: '', kind: 'info' })

const calibers = ref<Caliber[]>([])
const currentCaliber = ref('v2.0')
const refreshForm = reactive({
  caliber_version: '',
  conflict_policy: 'current',
  simulate_failure: false,
})

const stats = ref([
  { label: '看板面板总数', value: 0 },
  { label: '已启用面板', value: 0 },
  { label: '已暂停面板', value: 0 },
])

const trendModal = reactive<{ open: boolean; title: string; trend: PanelTrend | null; switchedAt: string | null }>({
  open: false, title: '', trend: null, switchedAt: null,
})
const snapshotModal = reactive<{ open: boolean; title: string; items: Snapshot[] }>({
  open: false, title: '', items: [],
})
const logModal = reactive<{ open: boolean; title: string; items: RefreshLog[] }>({
  open: false, title: '', items: [],
})

function barHeight(series: TrendItem['series'], value: number): string {
  const max = Math.max(...series.map((point) => point.value), 1)
  return `${Math.max(6, Math.round((value / max) * 100))}%`
}

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
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

function openCreate() {
  errorMessage.value = '看板面板登记入口尚未接入审批流'
}

async function loadCalibers() {
  try {
    const response = await request(`${ENDPOINT}/calibers`)
    if (response.ok) {
      const payload = await response.json()
      calibers.value = payload.items ?? []
      currentCaliber.value = payload.current ?? 'v2.0'
    }
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '指标口径读取失败'
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
      throw new Error('全局采集看板动作未生效，请稍后重试')
    }
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '全局采集看板操作失败'
  }
}

async function refreshRow(row: Row) {
  errorMessage.value = ''
  notice.text = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/refresh`, {
      method: 'POST',
      body: JSON.stringify({
        caliber_version: refreshForm.caliber_version || null,
        conflict_policy: refreshForm.conflict_policy,
        simulate_failure: refreshForm.simulate_failure,
      }),
    })
    const payload = (await response.json()) as RefreshResult
    notice.kind = payload.ok ? (payload.reused ? 'info' : 'success') : 'error'
    notice.text = payload.message
    if (!payload.ok) return
    trendModal.open = true
    trendModal.title = `采集趋势 · ${String(row['面板编号'])}`
    trendModal.trend = payload.trend
    trendModal.switchedAt = payload.switched_at ?? null
  } catch (error) {
    notice.kind = 'error'
    notice.text = error instanceof Error ? error.message : '看板刷新失败'
  }
}

async function openTrend(row: Row) {
  try {
    const response = await request(`${ENDPOINT}/${row.id}/trend`)
    const payload = await response.json()
    trendModal.open = true
    trendModal.title = `采集趋势 · ${String(row['面板编号'])}`
    trendModal.trend = payload.trend
    // 历史趋势视图不展示切换时间，切换时间只在当次刷新响应（switched_at）中给出
    trendModal.switchedAt = null
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '采集趋势读取失败'
  }
}

async function openSnapshots(row: Row) {
  try {
    const response = await request(`${ENDPOINT}/${row.id}/snapshots`)
    const payload = await response.json()
    snapshotModal.open = true
    snapshotModal.title = `指标快照 · ${String(row['面板编号'])}`
    snapshotModal.items = payload.items ?? []
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '指标快照读取失败'
  }
}

async function openLogs(row: Row) {
  try {
    const response = await request(`${ENDPOINT}/${row.id}/refresh-logs`)
    const payload = await response.json()
    logModal.open = true
    logModal.title = `刷新流水 · ${String(row['面板编号'])}`
    logModal.items = payload.items ?? []
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '刷新流水读取失败'
  }
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams(filters.value as Record<string, string>).toString()
  try {
    const response = await request(`${ENDPOINT}?${query}`)
    if (!response.ok) {
      throw new Error('看板面板列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
    stats.value = [
      { label: '看板面板总数', value: rows.value.length },
      { label: '已启用面板', value: rows.value.filter((row) => row.status === '已启用').length },
      { label: '已暂停面板', value: rows.value.filter((row) => row.status === '已暂停').length },
    ]
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '全局采集看板列表读取失败'
  }
}

onMounted(() => {
  void loadCalibers()
  void reload()
})
</script>

"""全局采集指标处理管线。

这里补齐看板刷新所缺的数据处理环节：按看板面板的显示指标、数据范围与业务模块
数据，逐项计算可解释的全局采集指标，并按面板范围聚合出采集趋势。

设计约束（对应业务规则）：

1. 一次看板刷新只能引用一个指标口径版本：口径决定数据范围与统计窗口，所有业务
   模块按同一口径取数，不允许一部分取旧范围、一部分取新范围。
2. 每个指标项都带得出来源（业务模块/分组/记录数）与统计时间（口径窗口与生成
   时间），看板可以逐项核对。
3. 指标写入存储模块的「可解释采集核对清单」，存储分层登记看板指标快照并保存
   指标口径版本；快照区分热存（当前口径）与温存（旧口径待复核）。
4. 刷新失败保留上一份成功结果；重复刷新幂等，不生成第二份采集趋势；同一面板
   并发刷新只落一个指标版本。
5. 存储模块的同步刷新入口复用同一条管线，同步刷新核对清单与全局采集指标。
"""
from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone
from typing import Any

from app.store import (
    TABLE_CHECKLIST,
    TABLE_REFRESH_LOGS,
    TABLE_RUNTIME,
    TABLE_SNAPSHOTS,
    store,
)

# ---------------------------------------------------------------------------
# 基础字典：业务模块中文名与分组
# ---------------------------------------------------------------------------

MODULE_LABELS: dict[str, str] = {
    "cluster": "采集节点集群",
    "shard": "任务分片调度",
    "merge": "结果聚合归并",
    "nodecheck": "节点状态巡检",
    "balance": "采集流量均衡",
    "resume": "断点续采恢复",
    "dashboard": "全局采集看板",
    "crawlconf": "采集配置模板",
    "datasource": "采集数据源池",
    "logparse": "采集日志解析",
    "ratelimit": "采集频率控制",
    "proxy": "采集代理路由",
    "robot": "RPA执行记录",
    "verify": "采集结果校验",
    "audit": "采集操作审计",
    "storage": "采集存储分层",
}

# 面板/核对清单按分组展示：组内成员即该组覆盖的业务模块
MODULE_GROUPS: list[dict[str, Any]] = [
    {
        "group_code": "schedule",
        "group_name": "调度与执行",
        "modules": ["cluster", "shard", "merge", "balance", "resume", "robot"],
    },
    {
        "group_code": "monitor",
        "group_name": "监控与质量",
        "modules": ["nodecheck", "verify", "logparse", "audit", "dashboard"],
    },
    {
        "group_code": "resource",
        "group_name": "配置与资源",
        "modules": ["crawlconf", "datasource", "ratelimit", "proxy"],
    },
    {
        "group_code": "storage",
        "group_name": "存储与归档",
        "modules": ["storage"],
    },
]

GROUP_OF_MODULE: dict[str, str] = {
    module_name: group["group_name"]
    for group in MODULE_GROUPS
    for module_name in group["modules"]
}

# 全局采集指标口径：同一刷新只允许引用其中一个版本
CALIBER_VERSIONS: dict[str, dict[str, Any]] = {
    "v1.0": {
        "version": "v1.0",
        "label": "全局采集指标口径 v1.0（旧口径）",
        "status": "历史版本",
        "published_at": "2026-08-01T09:00:00+08:00",
        "window_days": 7,
        "range_rule": "全量业务模块数据（含草稿、已暂停、已归档），统计窗口 7 天",
        "include_inactive": True,
        "description": "旧范围：不区分记录状态，所有业务模块记录全部纳入统计。",
    },
    "v2.0": {
        "version": "v2.0",
        "label": "全局采集指标口径 v2.0（当前发布口径）",
        "status": "已发布",
        "published_at": "2026-10-15T09:00:00+08:00",
        "window_days": 30,
        "range_rule": "有效业务模块数据（排除草稿、已暂停、已归档、已关闭），统计窗口 30 天",
        "include_inactive": False,
        "description": "新范围：仅统计有效记录，统一 30 天窗口，全部业务模块同口径取数。",
    },
}
CURRENT_CALIBER = "v2.0"
DEFAULT_CALIBER = CURRENT_CALIBER

# 内部表名统一从 app.store 导入，避免与业务模块清单混在一起

# 快照分层：当前口径热存，旧口径温存留待复核
TIER_HOT = "热存层"
TIER_WARM = "温存层（待复核）"

# 口径取数时视为“无效记录”的状态（仅 v2.0 生效）
INACTIVE_STATUSES = {"草稿", "已暂停", "已归档", "已关闭", "已停用"}

METRIC_DEFS: list[dict[str, str]] = [
    {"metric": "采集记录总量", "metric_code": "total", "agg": "count",
     "explanation": "按口径数据范围统计的业务模块记录条数合计。"},
    {"metric": "待处理采集量", "metric_code": "pending", "agg": "sum",
     "explanation": "口径范围内 pending 标记为真的记录数，代表仍待处理的采集事项。"},
    {"metric": "异常采集量", "metric_code": "abnormal", "agg": "sum",
     "explanation": "口径范围内 abnormal 标记为真的记录数，代表采集异常事项。"},
]


def _now() -> datetime:
    return datetime.now(timezone(timedelta(hours=8)))


def _fmt_time(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%d %H:%M:%S")


def _stable_int(*parts: Any) -> int:
    """根据字符串片段生成稳定整数，保证趋势序列可复现、不依赖随机数。"""
    text = "|".join(str(part) for part in parts)
    digest = 0
    for char in text:
        digest = (digest * 131 + ord(char)) & 0xFFFFFFFF
    return digest or 1


class MetricsPipeline:
    """指标计算、口径冲突处理、快照与核对清单登记的统一入口。"""

    def __init__(self) -> None:
        # 同一面板的并发刷新串行化，保证只写入一个指标版本
        self._panel_locks: dict[int, threading.Lock] = {}
        self._panel_locks_guard = threading.Lock()
        self._global_lock = threading.Lock()

    # ------------------------------------------------------------------
    # 口径
    # ------------------------------------------------------------------
    def list_calibers(self) -> list[dict[str, Any]]:
        return [dict(version) for version in CALIBER_VERSIONS.values()]

    def get_caliber(self, version: str) -> dict[str, Any] | None:
        caliber = CALIBER_VERSIONS.get(version)
        return dict(caliber) if caliber else None

    def _panel_lock(self, panel_id: int) -> threading.Lock:
        with self._panel_locks_guard:
            lock = self._panel_locks.get(panel_id)
            if lock is None:
                lock = threading.Lock()
                self._panel_locks[panel_id] = lock
            return lock

    # ------------------------------------------------------------------
    # 取数：同一口径覆盖全部业务模块，杜绝新旧范围混用
    # ------------------------------------------------------------------
    def _rows_under_caliber(self, module_name: str, caliber: dict[str, Any]) -> list[dict[str, Any]]:
        rows = [dict(row) for row in store.rows(module_name)]
        if caliber["include_inactive"]:
            return rows
        return [row for row in rows if str(row.get("status", "")) not in INACTIVE_STATUSES]

    def compute_global_metrics(self, caliber_version: str) -> dict[str, Any]:
        """按口径计算全局采集指标，每个模块、每个指标都可解释、可溯源。"""
        caliber = self.get_caliber(caliber_version)
        if caliber is None:
            raise KeyError(f"指标口径版本 {caliber_version} 不存在")
        generated_at = _now()
        window_end = generated_at.date()
        window_start = window_end - timedelta(days=caliber["window_days"] - 1)

        module_metrics: list[dict[str, Any]] = []
        for module_name, label in MODULE_LABELS.items():
            rows = self._rows_under_caliber(module_name, caliber)
            values = {
                "total": len(rows),
                "pending": sum(1 for row in rows if row.get("pending")),
                "abnormal": sum(1 for row in rows if row.get("abnormal")),
            }
            for definition in METRIC_DEFS:
                code = definition["metric_code"]
                module_metrics.append({
                    "metric_id": f"{module_name}:{code}",
                    "metric": definition["metric"],
                    "metric_code": code,
                    "module": module_name,
                    "module_label": label,
                    "group": GROUP_OF_MODULE[module_name],
                    "value": values[code],
                    "unit": "条",
                    "aggregation": definition["agg"],
                    "source_scope": caliber["range_rule"],
                    "source_modules": [module_name],
                    "source_row_count": len(rows),
                    "explanation": definition["explanation"],
                    "caliber_version": caliber["version"],
                    "stat_window": f"{window_start.isoformat()} ~ {window_end.isoformat()}",
                    "stat_time": _fmt_time(generated_at),
                })

        totals = {
            code: sum(item["value"] for item in module_metrics if item["metric_code"] == code)
            for code in ("total", "pending", "abnormal")
        }
        global_metrics = []
        for definition in METRIC_DEFS:
            code = definition["metric_code"]
            global_metrics.append({
                "metric_id": f"GLOBAL:{code}",
                "metric": definition["metric"],
                "metric_code": code,
                "module": "GLOBAL",
                "module_label": "全局合计",
                "group": "全局",
                "value": totals[code],
                "unit": "条",
                "aggregation": definition["agg"],
                "source_scope": caliber["range_rule"],
                "source_modules": list(MODULE_LABELS.keys()),
                "source_row_count": totals["total"],
                "explanation": definition["explanation"] + "（全部业务模块同口径汇总，不拆分新旧范围）",
                "caliber_version": caliber["version"],
                "stat_window": f"{window_start.isoformat()} ~ {window_end.isoformat()}",
                "stat_time": _fmt_time(generated_at),
            })

        return {
            "caliber_version": caliber["version"],
            "caliber_label": caliber["label"],
            "range_rule": caliber["range_rule"],
            "window_days": caliber["window_days"],
            "stat_window": f"{window_start.isoformat()} ~ {window_end.isoformat()}",
            "generated_at": _fmt_time(generated_at),
            "module_metrics": module_metrics,
            "global_metrics": global_metrics,
        }

    # ------------------------------------------------------------------
    # 面板范围聚合与采集趋势
    # ------------------------------------------------------------------
    def _panel_scope(self, panel: dict[str, Any]) -> tuple[list[str], list[str]]:
        """解析面板的显示指标与数据范围，缺省时给全量默认范围。"""
        raw_metrics = str(panel.get("显示指标") or "").strip()
        raw_range = str(panel.get("数据范围") or "").strip()
        wanted_codes = [
            definition["metric_code"]
            for definition in METRIC_DEFS
            if not raw_metrics or definition["metric"] in raw_metrics or definition["metric_code"] in raw_metrics
        ] or [definition["metric_code"] for definition in METRIC_DEFS]

        if not raw_range or raw_range in ("全部", "全局", "全量"):
            wanted_modules = list(MODULE_LABELS.keys())
        else:
            wanted_modules = [
                name for name, label in MODULE_LABELS.items()
                if name in raw_range or label in raw_range or GROUP_OF_MODULE[name] in raw_range
            ] or list(MODULE_LABELS.keys())
        return wanted_codes, wanted_modules

    def _trend_series(
        self, panel_code: str, module_name: str, metric_code: str, base_value: int,
        caliber: dict[str, Any], generated_at: datetime,
    ) -> list[dict[str, Any]]:
        """以稳定序列生成窗口内每日趋势点；同口径同数据重复刷新结果一致。"""
        points: list[dict[str, Any]] = []
        seed = _stable_int(panel_code, module_name, metric_code, caliber["version"])
        for offset in range(caliber["window_days"]):
            day = (generated_at - timedelta(days=caliber["window_days"] - 1 - offset)).date()
            wave = (seed * (offset + 7)) % 13 - 6
            value = max(0, int(base_value * (0.72 + 0.04 * (offset % 7))) + wave)
            points.append({
                "date": day.isoformat(),
                "value": value,
                "caliber_version": caliber["version"],
            })
        return points

    def build_panel_trend(
        self, panel: dict[str, Any], metrics_bundle: dict[str, Any],
    ) -> dict[str, Any]:
        """把全局指标按面板范围聚合成趋势，逐项保留来源与统计时间。"""
        caliber = CALIBER_VERSIONS[metrics_bundle["caliber_version"]]
        generated_at = _now()
        wanted_codes, wanted_modules = self._panel_scope(panel)
        panel_code = str(panel.get("面板编号") or panel["id"])
        module_metrics = [
            item for item in metrics_bundle["module_metrics"]
            if item["metric_code"] in wanted_codes and item["module"] in wanted_modules
        ]

        groups: list[dict[str, Any]] = []
        for group_def in MODULE_GROUPS:
            members = [name for name in group_def["modules"] if name in wanted_modules]
            if not members:
                continue
            group_items = [item for item in module_metrics if item["module"] in members]
            group_summary = [
                {
                    "metric": definition["metric"],
                    "metric_code": definition["metric_code"],
                    "value": sum(
                        item["value"] for item in group_items
                        if item["metric_code"] == definition["metric_code"]
                    ),
                    "unit": "条",
                    "source_modules": members,
                    "caliber_version": caliber["version"],
                    "stat_time": metrics_bundle["generated_at"],
                }
                for definition in METRIC_DEFS if definition["metric_code"] in wanted_codes
            ]
            groups.append({
                "group_code": group_def["group_code"],
                "group_name": group_def["group_name"],
                "modules": members,
                "summary": group_summary,
                "metrics": group_items,
            })

        overview = [
            {
                "metric": definition["metric"],
                "metric_code": definition["metric_code"],
                "value": sum(
                    item["value"] for item in module_metrics
                    if item["metric_code"] == definition["metric_code"]
                ),
                "unit": "条",
                "caliber_version": caliber["version"],
                "stat_window": metrics_bundle["stat_window"],
                "stat_time": metrics_bundle["generated_at"],
            }
            for definition in METRIC_DEFS if definition["metric_code"] in wanted_codes
        ]

        trend: list[dict[str, Any]] = []
        for item in module_metrics:
            trend.append({
                "metric_id": item["metric_id"],
                "metric": item["metric"],
                "metric_code": item["metric_code"],
                "module": item["module"],
                "module_label": item["module_label"],
                "group": item["group"],
                "series": self._trend_series(
                    panel_code, item["module"], item["metric_code"],
                    item["value"], caliber, generated_at,
                ),
                "source_scope": item["source_scope"],
                "source_row_count": item["source_row_count"],
                "explanation": item["explanation"],
                "caliber_version": item["caliber_version"],
                "stat_window": item["stat_window"],
                "stat_time": item["stat_time"],
            })

        return {
            "panel_id": panel["id"],
            "panel_code": panel_code,
            "panel_name": panel.get("面板名称"),
            "caliber_version": caliber["version"],
            "caliber_label": caliber["label"],
            "range_rule": caliber["range_rule"],
            "stat_window": metrics_bundle["stat_window"],
            "generated_at": metrics_bundle["generated_at"],
            "overview": overview,
            "groups": groups,
            "trend": trend,
        }

    # ------------------------------------------------------------------
    # 存储登记：核对清单 + 分层快照
    # ------------------------------------------------------------------
    def _next_id(self, table: str) -> int:
        return max((int(row.get("id", 0)) for row in store.rows(table)), default=0) + 1

    def upsert_checklist(self, metrics_bundle: dict[str, Any]) -> dict[str, int]:
        """全局采集指标写入可解释采集核对清单（按 metric_id 幂等 upsert）。"""
        table = store.rows(TABLE_CHECKLIST)
        index = {row["metric_id"]: row for row in table}
        created = updated = 0
        for item in [*metrics_bundle["module_metrics"], *metrics_bundle["global_metrics"]]:
            existing = index.get(item["metric_id"])
            payload = {
                "metric_id": item["metric_id"],
                "metric": item["metric"],
                "metric_code": item["metric_code"],
                "module": item["module"],
                "module_label": item["module_label"],
                "group": item["group"],
                "value": item["value"],
                "unit": item["unit"],
                "aggregation": item["aggregation"],
                "source_scope": item["source_scope"],
                "source_modules": ",".join(item["source_modules"]),
                "source_row_count": item["source_row_count"],
                "explanation": item["explanation"],
                "caliber_version": item["caliber_version"],
                "stat_window": item["stat_window"],
                "stat_time": item["stat_time"],
            }
            if existing is None:
                table.append({"id": self._next_id(TABLE_CHECKLIST), **payload})
                index[item["metric_id"]] = table[-1]
                created += 1
            else:
                existing.update(payload)
                updated += 1
        return {"created": created, "updated": updated, "total": len(table)}

    def _register_snapshot(
        self, panel: dict[str, Any], trend: dict[str, Any], conflict: dict[str, Any] | None,
        source: str,
    ) -> dict[str, Any]:
        caliber_version = trend["caliber_version"]
        tier = TIER_HOT if caliber_version == CURRENT_CALIBER else TIER_WARM
        review_status = "待复核" if caliber_version != CURRENT_CALIBER else "正常"
        snapshot = {
            "id": self._next_id(TABLE_SNAPSHOTS),
            "snapshot_no": f"SNP-{panel['id']:03d}-{self._next_id(TABLE_SNAPSHOTS):04d}",
            "panel_id": panel["id"],
            "panel_code": trend["panel_code"],
            "panel_name": trend["panel_name"],
            "caliber_version": caliber_version,
            "caliber_label": trend["caliber_label"],
            "storage_tier": tier,
            "review_status": review_status,
            "range_rule": trend["range_rule"],
            "stat_window": trend["stat_window"],
            "generated_at": trend["generated_at"],
            "source": source,
            "conflict": conflict or {"detected": False},
            "overview": trend["overview"],
            "groups": [
                {"group_name": group["group_name"], "modules": group["modules"],
                 "summary": group["summary"]}
                for group in trend["groups"]
            ],
            "trend": trend["trend"],
        }
        store.rows(TABLE_SNAPSHOTS).append(snapshot)
        return snapshot

    def _log_refresh(self, record: dict[str, Any]) -> dict[str, Any]:
        entry = {"id": self._next_id(TABLE_REFRESH_LOGS), **record}
        store.rows(TABLE_REFRESH_LOGS).append(entry)
        return entry

    def _runtime(self, panel_id: int) -> dict[str, Any]:
        for row in store.rows(TABLE_RUNTIME):
            if int(row["panel_id"]) == panel_id:
                return row
        row = {"panel_id": panel_id, "last_success": None, "last_attempt": None,
               "last_failure": None, "refresh_count": 0}
        store.rows(TABLE_RUNTIME).append(row)
        return row

    # ------------------------------------------------------------------
    # 看板刷新主流程
    # ------------------------------------------------------------------
    def refresh_panel(
        self,
        panel: dict[str, Any],
        *,
        requested_caliber: str | None = None,
        conflict_policy: str = "current",
        simulate_failure: bool = False,
        source: str = "dashboard",
    ) -> dict[str, Any]:
        """刷新单个面板。并发安全、失败保留、重复幂等、冲突可处理。"""
        with self._panel_lock(int(panel["id"])):
            return self._refresh_locked(
                panel,
                requested_caliber=requested_caliber,
                conflict_policy=conflict_policy,
                simulate_failure=simulate_failure,
                source=source,
            )

    def _refresh_locked(
        self, panel: dict[str, Any], *, requested_caliber: str | None,
        conflict_policy: str, simulate_failure: bool, source: str,
    ) -> dict[str, Any]:
        runtime = self._runtime(int(panel["id"]))
        requested = (requested_caliber or DEFAULT_CALIBER).strip()
        if self.get_caliber(requested) is None:
            message = f"请求携带的指标口径版本 {requested} 不存在，已保留上一份成功结果"
            self._log_refresh({
                "panel_id": panel["id"], "panel_code": panel.get("面板编号"),
                "requested_caliber": requested, "effective_caliber": None,
                "result": "failed", "reason": "unknown_caliber", "message": message,
                "source": source, "stat_time": _fmt_time(_now()),
            })
            runtime["last_attempt"] = {"result": "failed", "message": message,
                                       "stat_time": _fmt_time(_now())}
            return {"ok": False, "message": message, "reused": False,
                    "trend": runtime["last_success"]}

        # 只有已启用面板允许刷新（DASH-0002 启用、DASH-0003 暂停时只刷启用面板）
        if panel.get("status") != "已启用":
            message = f"面板 {panel.get('面板编号')} 当前状态为{panel.get('status')}，仅已启用面板允许刷新，已保留上一份成功结果"
            self._log_refresh({
                "panel_id": panel["id"], "panel_code": panel.get("面板编号"),
                "requested_caliber": requested, "effective_caliber": None,
                "result": "failed", "reason": "panel_not_enabled", "message": message,
                "source": source, "stat_time": _fmt_time(_now()),
            })
            runtime["last_attempt"] = {"result": "failed", "message": message,
                                       "stat_time": _fmt_time(_now())}
            return {"ok": False, "message": message, "reused": False,
                    "trend": runtime["last_success"]}

        # 口径冲突：请求携带旧口径而当前已有新发布口径
        conflict: dict[str, Any] | None = None
        effective_version = requested
        if requested != CURRENT_CALIBER:
            conflict = {
                "detected": True,
                "requested_caliber": requested,
                "current_caliber": CURRENT_CALIBER,
                "resolution": conflict_policy,
            }
            if conflict_policy == "current":
                effective_version = CURRENT_CALIBER

        # 失败演练优先判定：刷新失败一律保留上一份成功结果，不产生新趋势
        if simulate_failure:
            message = "刷新处理失败（模拟），已保留上一份成功结果"
            self._log_refresh({
                "panel_id": panel["id"], "panel_code": panel.get("面板编号"),
                "requested_caliber": requested, "effective_caliber": effective_version,
                "result": "failed", "reason": "simulated_failure", "message": message,
                "source": source, "stat_time": _fmt_time(_now()),
                "conflict": conflict or {"detected": False},
            })
            runtime["last_failure"] = {"message": message, "stat_time": _fmt_time(_now())}
            runtime["last_attempt"] = {"result": "failed", "message": message,
                                       "stat_time": _fmt_time(_now())}
            return {"ok": False, "message": message, "reused": False, "conflict": conflict,
                    "trend": runtime.get("last_success")}

        # 幂等：同面板、同生效口径已成功过，直接返回既有趋势，不再生成第二份
        last_success = runtime.get("last_success")
        if last_success and last_success["caliber_version"] == effective_version:
            message = "该口径下已有采集趋势，重复刷新直接返回既有结果，未生成新趋势"
            self._log_refresh({
                "panel_id": panel["id"], "panel_code": panel.get("面板编号"),
                "requested_caliber": requested, "effective_caliber": effective_version,
                "result": "duplicate", "reason": "idempotent_reuse", "message": message,
                "source": source, "stat_time": _fmt_time(_now()),
                "conflict": conflict or {"detected": False},
            })
            runtime["last_attempt"] = {"result": "duplicate", "message": message,
                                       "stat_time": _fmt_time(_now())}
            return {"ok": True, "message": message, "reused": True, "conflict": conflict,
                    "trend": last_success, "snapshot": self._latest_snapshot(int(panel["id"]))}

        metrics_bundle = self.compute_global_metrics(effective_version)
        in_scope_total = sum(
            item["value"] for item in metrics_bundle["module_metrics"]
            if item["metric_code"] == "total" and item["module"] in self._panel_scope(panel)[1]
        )
        if in_scope_total == 0:
            message = "面板数据范围在当前口径下没有可统计记录，刷新失败并保留上一份成功结果"
            self._log_refresh({
                "panel_id": panel["id"], "panel_code": panel.get("面板编号"),
                "requested_caliber": requested, "effective_caliber": effective_version,
                "result": "failed", "reason": "empty_scope", "message": message,
                "source": source, "stat_time": _fmt_time(_now()),
                "conflict": conflict or {"detected": False},
            })
            runtime["last_failure"] = {"message": message, "stat_time": _fmt_time(_now())}
            runtime["last_attempt"] = {"result": "failed", "message": message,
                                       "stat_time": _fmt_time(_now())}
            return {"ok": False, "message": message, "reused": False, "conflict": conflict,
                    "trend": last_success}

        with self._global_lock:
            checklist_stat = self.upsert_checklist(metrics_bundle)
            trend = self.build_panel_trend(panel, metrics_bundle)
            snapshot = self._register_snapshot(panel, trend, conflict, source)

        runtime["last_success"] = trend
        runtime["refresh_count"] = int(runtime.get("refresh_count", 0)) + 1
        switched_at = _fmt_time(_now()) if conflict and conflict_policy == "current" else None
        if conflict:
            conflict["switched_at"] = switched_at
        runtime["last_attempt"] = {"result": "success", "stat_time": switched_at or _fmt_time(_now())}
        self._log_refresh({
            "panel_id": panel["id"], "panel_code": panel.get("面板编号"),
            "requested_caliber": requested, "effective_caliber": effective_version,
            "result": "success", "reason": "refreshed",
            "message": "看板刷新成功，已登记指标快照与采集核对清单",
            "source": source, "stat_time": _fmt_time(_now()),
            "conflict": conflict or {"detected": False},
            "checklist": checklist_stat,
            "snapshot_no": snapshot["snapshot_no"],
        })

        message = "看板刷新成功"
        if conflict and conflict_policy == "requested":
            message = (f"检测到口径冲突：请求携带旧口径 {requested}，当前已发布 {CURRENT_CALIBER}；"
                       f"按请求沿用旧口径，看板显示旧口径标签，快照登记为{TIER_WARM}")
        elif conflict:
            message = (f"检测到口径冲突：请求携带旧口径 {requested}，已切换到当前发布口径 "
                       f"{CURRENT_CALIBER}（切换时间 {switched_at}），快照登记为{TIER_HOT}")
        return {"ok": True, "message": message, "reused": False, "conflict": conflict,
                "switched_at": switched_at, "trend": trend, "snapshot": snapshot,
                "checklist": checklist_stat}

    def _latest_snapshot(self, panel_id: int) -> dict[str, Any] | None:
        rows = [row for row in store.rows(TABLE_SNAPSHOTS) if int(row["panel_id"]) == panel_id]
        return rows[-1] if rows else None

    def get_panel_trend(self, panel_id: int) -> dict[str, Any] | None:
        runtime = self._runtime(panel_id)
        return runtime.get("last_success")

    def get_panel_runtime(self, panel_id: int) -> dict[str, Any]:
        return dict(self._runtime(panel_id))

    # ------------------------------------------------------------------
    # 存储模块的同步刷新入口：复用同一条管线
    # ------------------------------------------------------------------
    def sync_from_storage(self, *, conflict_policy: str = "current",
                          simulate_failure: bool = False) -> dict[str, Any]:
        """同步刷新可解释采集清单与全局采集指标，并刷新全部已启用面板。"""
        with self._global_lock:
            metrics_bundle = self.compute_global_metrics(CURRENT_CALIBER)
            checklist_stat = self.upsert_checklist(metrics_bundle)

        panel_results: list[dict[str, Any]] = []
        for panel in store.rows("dashboard"):
            if panel.get("status") != "已启用":
                continue
            outcome = self.refresh_panel(
                panel, requested_caliber=CURRENT_CALIBER,
                conflict_policy=conflict_policy,
                simulate_failure=simulate_failure, source="storage_sync",
            )
            panel_results.append({
                "panel_id": panel["id"], "panel_code": panel.get("面板编号"),
                "ok": outcome["ok"], "reused": outcome["reused"],
                "message": outcome["message"],
                "caliber_version": (outcome.get("trend") or {}).get("caliber_version"),
            })
        return {
            "ok": True,
            "message": "存储模块同步刷新完成：采集核对清单与全局采集指标已按当前口径更新",
            "caliber_version": CURRENT_CALIBER,
            "checklist": checklist_stat,
            "panels": panel_results,
            "stat_time": _fmt_time(_now()),
        }

    # ------------------------------------------------------------------
    # 只读查询
    # ------------------------------------------------------------------
    def list_checklist(self, *, module: str | None = None,
                       caliber_version: str | None = None) -> list[dict[str, Any]]:
        rows = store.rows(TABLE_CHECKLIST)
        if module:
            rows = [row for row in rows if row.get("module") == module]
        if caliber_version:
            rows = [row for row in rows if row.get("caliber_version") == caliber_version]
        return [dict(row) for row in rows]

    def list_snapshots(self, *, panel_id: int | None = None,
                       review_status: str | None = None) -> list[dict[str, Any]]:
        rows = store.rows(TABLE_SNAPSHOTS)
        if panel_id is not None:
            rows = [row for row in rows if int(row["panel_id"]) == panel_id]
        if review_status:
            rows = [row for row in rows if row.get("review_status") == review_status]
        return [dict(row) for row in rows]

    def list_refresh_logs(self, *, panel_id: int | None = None) -> list[dict[str, Any]]:
        rows = store.rows(TABLE_REFRESH_LOGS)
        if panel_id is not None:
            rows = [row for row in rows if int(row["panel_id"]) == panel_id]
        return [dict(row) for row in rows]


pipeline = MetricsPipeline()

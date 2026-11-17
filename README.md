# 分布式采集调度引擎

面向大规模数据采集团队的分布式采集调度引擎。覆盖采集节点集群管理、任务分片调度、结果聚合归并、节点健康监控、采集流量均衡、断点续采恢复与全局采集看板的完整分布式采集链路。

这是一个前后端分离的管理平台：前端 Vue 3 + Vite + TypeScript，后端 FastAPI（Python）。
两边各自独立启动，前端 dev server 已关掉自动打开页面，启动后按终端打印的地址手工打开。

## 目录结构

```text
.
├── frontend/                 Vue 3 + Vite + TypeScript 前端
│   ├── src/views/            每个业务模块一个页面
│   ├── src/api/              统一请求封装
│   ├── src/stores/           会话与筛选状态
│   └── vite.config.ts        dev server 配置（open: false）
├── backend/                  FastAPI（Python） 后端
│   ├── app/routers/          每个业务模块一组接口
│   ├── app/services/         业务规则与状态流转
│   └── app/store.py          内存数据仓库与示例数据
├── .gitignore
└── docker-compose.yml
```

## 启动

### 后端

```bash
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
./run.sh
```

健康检查：`curl http://127.0.0.1:8000/api/health`

### 前端

```bash
cd frontend
npm install
npm run dev
```

前端默认监听 `http://127.0.0.1:5173/`，dev server 不会自动打开浏览器，
需要自己访问。`/api` 由 vite 代理到后端 `http://127.0.0.1:8000`。

## 业务模块

| 模块 | 目录 | 业务对象 | 主要字段 |
| --- | --- | --- | --- |
| 采集节点集群 | `cluster` | 集群节点 | 节点编号、节点地址、节点角色 |
| 任务分片调度 | `shard` | 分片任务 | 分片编号、采集计划、分片范围 |
| 结果聚合归并 | `merge` | 聚合任务 | 任务编号、采集计划、分片总数 |
| 节点状态巡检 | `nodecheck` | 巡检记录 | 记录编号、节点编号、CPU使用率 |
| 采集流量均衡 | `balance` | 均衡策略 | 策略编号、负载算法、权重因子 |
| 断点续采恢复 | `resume` | 断点记录 | 断点编号、采集计划、断点位置 |
| 全局采集看板 | `dashboard` | 看板面板 | 面板编号、面板名称、显示指标 |
| 采集配置模板 | `crawlconf` | 配置模板 | 模板编号、模板名称、目标类型 |
| 采集数据源池 | `datasource` | 数据源条目 | 条目编号、数据源名称、源类型 |
| 采集日志解析 | `logparse` | 日志模板 | 模板编号、日志类型、解析规则 |
| 采集频率控制 | `ratelimit` | 频率规则 | 规则编号、目标站点、请求间隔 |
| 采集代理路由 | `proxy` | 代理配置 | 配置编号、代理地址、代理协议 |
| RPA执行记录 | `robot` | 执行记录 | 记录编号、流程模板、执行节点 |
| 采集结果校验 | `verify` | 校验规则 | 规则编号、采集计划、校验类型 |
| 采集操作审计 | `audit` | 审计记录 | 记录编号、操作类型、操作对象 |
| 采集存储分层 | `storage` | 存储策略 | 策略编号、存储层级、文件格式 |

## 约定

- 每个模块的前端页面在 `frontend/src/views/<模块>/index.vue`，后端接口在
  `backend/app/routers/<模块>.py`，业务规则在 `backend/app/services/<模块>.py`。
- 列表接口统一返回 `{ items, total, page, size }`，动作接口统一返回 `{ ok, message }`。
- 状态流转只允许在 `app/services` 里改，路由层不做业务判断。

## 全局采集指标处理（看板刷新管线）

看板刷新所需的指标计算与按面板范围聚合统一收在
`backend/app/services/metrics.py`，前后端入口如下：

| 能力 | 入口 |
| --- | --- |
| 指标口径版本清单 | `GET /api/dashboard/calibers` |
| 刷新看板面板（可携带旧口径与冲突策略） | `POST /api/dashboard/{id}/refresh` |
| 读取采集趋势（概览、分组、逐项来源与统计时间） | `GET /api/dashboard/{id}/trend` |
| 面板指标快照与刷新流水 | `GET /api/dashboard/{id}/snapshots`、`/refresh-logs` |
| 可解释采集核对清单 | `GET /api/storage/checklist` |
| 分层登记的看板指标快照 | `GET /api/storage/metric-snapshots` |
| 存储模块同步刷新清单与全局指标 | `POST /api/storage/sync-metrics` |

规则要点：

1. 一次刷新只引用一个指标口径版本，全部业务模块按同一口径取数，不允许新旧范围混用。
2. 请求携带旧口径而当前已有新发布口径时：`conflict_policy=requested` 沿用旧口径，
   看板显示旧口径标签、快照登记到温存层并标记「待复核」；`conflict_policy=current`
   切换到当前发布口径，看板显示新口径与切换时间、快照登记到热存层。
3. 重复刷新幂等返回既有趋势，不生成第二份采集趋势；刷新失败保留上一份成功结果；
   同一面板并发刷新经面板级锁串行化，只写入一个指标版本。
4. 仅「已启用」面板允许刷新（如 DASH-0002 启用、DASH-0003 暂停时只刷启用面板）。
5. 存储模块的同步刷新与看板刷新复用同一条管线，核对清单按 `metric_id` 幂等更新，
   快照保存口径版本、统计窗口与冲突处理记录。


领域：数据工程
子方向：数据采集、爬虫与RPA

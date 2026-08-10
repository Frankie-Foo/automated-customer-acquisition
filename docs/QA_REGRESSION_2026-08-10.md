# QA 发布回归：2026-08-10

基线：`bee388f116cf76737dfef23f29015fd0b90e597a`（包含 `a18ddfb`、`692c6ec`、`bee388f`）

工作树：`codex/test-deploy`

## 通过项

- `python -m pytest -q`：`287 passed`。
- 前端 `npm run check`、`npm run build`：通过。
- Python wheel 构建：通过；React 静态入口、JS、legacy controller、邮件图片 `outreach_01.png`、`outreach_05.png` 均包含在 package data 和 Docker 镜像中。
- Docker CI 栈：构建通过，容器 `healthy`，`/api/live` 通过。
- 全新 PostgreSQL 迁移：`37/37`；重复执行迁移两次均返回 `applied=[]`。
- 严格 readiness：隔离 dummy QA 配置返回 `ready=true`。
- 真实 RLS：runtime role 下匿名读取为 0；销售用户只能读取本人 private + public；他人 private 插入/更新拒绝；public 仅允许受控 claim、return、所属来源任务富化/入池；管理员可见全部并可写他人 private。
- 获客计划配额与归属：public plan `run_due` 为 `completed=1/failed=0`，3 条 contact 均为 `pool_type=public、owner_user_id=NULL`，global `source_count=3`，run error 为 NULL；private plan 按用户配额 2 完成，2 条 contact 为 `pool_type=private、owner_user_id=2`，用户 source_count=2、全局 source_count=5。
- CSV 正常导入：真实 Docker API `/api/import/csv` 返回 `200`，且 `metrics_refreshed=true`。
- CSV metrics 降级：既有端到端回归中人为触发刷新异常仍返回 `200`，明确返回 `metrics_refreshed=false` 与 `campaign_metrics_refresh_failed` warning，contact、lead、task 写入保留。
- CSV 重试幂等：相同 CSV 重试返回 `200`，数据库保持 1 个 contact、1 个 lead、1 个 open follow-up task，无重复数据。
- 网络安全：Base 首跳、编码 URL、重定向拦截、共享 `urlopen` gate 共 8 个 targeted tests 通过。
- QA Docker 容器与卷已清理。

## 历史缺陷复验

此前 P1：`Repository.refresh_campaign_metrics()` 中未转义的 `LIKE 'negative%'` 曾导致 CSV 导入 500。`af62d6e` 修复后，正常路径和 metrics 失败降级路径均已通过端到端复验。

## 历史阻断复验

`bee388f` 修复了 `Repository.consume_global_quota()` 漏返回导致 public plan 报 `NoneType` 的问题。修复后真实 PostgreSQL public/private acquisition plan、配额计数、入池归属和 run 状态均已通过复验。

## 凭据边界

未读取或提交任何 `.env` 文件。readiness 使用仅限 QA 的 dummy 环境变量，未使用真实 API、邮箱或生产数据库。

## 发布结论

**PASS**：全量测试、Docker、37/37 迁移及幂等、readiness、真实 RLS、获客计划配额/归属、CSV、网络安全均通过，QA 发布门禁放行。

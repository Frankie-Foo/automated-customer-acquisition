# QA 发布回归：2026-08-10

基线：`af62d6ec803809ea4becdf23140949a15e004d25`（`main` 从 `367a885` 快进合并）

工作树：`codex/test-deploy`

## 通过项

- `python -m pytest -q`：`265 passed`；metrics 刷新失败降级用例通过。
- 前端 `npm run check`、`npm run build`：通过。
- Python wheel 构建：通过；React 静态入口、JS、邮件图片 `outreach_01.png`、`outreach_05.png` 均包含在 package data 和 Docker 镜像中。
- Docker CI 栈：构建通过，容器 `healthy`，`/api/live` 通过。
- 全新数据库迁移：`35/35`；重复执行迁移两次均返回 `applied=[]`。
- 严格 readiness：使用隔离的 dummy QA 配置通过；CI 默认无生产凭据时 strict 正确返回未就绪。
- CSV 正常导入：真实 Docker API `/api/import/csv` 返回 `200`，且 `metrics_refreshed=true`。
- CSV metrics 降级：人为触发 `campaign_metrics` 刷新异常时，真实 API 仍返回 `200`，并明确返回 `metrics_refreshed=false`、`warnings=["campaign_metrics_refresh_failed"]`；contact、lead、task 写入保留。
- CSV 重试幂等：相同 CSV 重试返回 `200`，数据库保持 1 个 contact、1 个 lead、1 个 open follow-up task，无重复数据。
- 权限隔离：销售用户只能读取自己的 private contact；跨用户详情为空；跨用户 Profile/Draft 返回 `403 claim_required`；销售用户访问 Flywheel/Migrate/管理员操作返回 `403 admin_required`；管理员 Flywheel、Flywheel run、Migrate 通过。
- QA Docker 容器与卷已清理。

## 历史缺陷复验

此前 P1：`Repository.refresh_campaign_metrics()` 中未转义的 `LIKE 'negative%'` 曾导致 CSV 导入 500。`af62d6e` 修复后，正常路径和 metrics 失败降级路径均已通过端到端复验。

## 凭据边界

未读取或提交任何 `.env` 文件。readiness 使用仅限 QA 的 dummy 环境变量，未使用真实 API、邮箱或生产数据库。

## 发布结论

**PASS**：本次全量回归、CSV 全链路、幂等、迁移、readiness、Docker 和权限隔离均通过，QA 不再阻断发布。

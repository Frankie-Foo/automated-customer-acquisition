# QA 发布回归：2026-08-10

基线：`2f2a6393460e210c58e232c991d15b954d1fc10c`

工作树：`codex/test-deploy`

## 通过项

- `python -m pytest -q`：`256 passed`。
- 前端 `npm run check`、`npm run build`：通过。
- Python wheel 构建：通过；React 静态入口、JS、邮件图片 `outreach_01.png`、`outreach_05.png` 均包含在 package data 和 Docker 镜像中。
- Docker CI 栈：构建通过，容器 `healthy`，`/api/live` 通过。
- 全新数据库迁移：`34/34`；重复执行迁移两次均返回 `applied=[]`。
- 严格 readiness：使用隔离的 dummy QA 配置通过；CI 默认无生产凭据时 strict 正确返回未就绪。
- 权限隔离：销售用户只能读取自己的 private contact；跨用户详情为空；跨用户 Profile/Draft 返回 `403 claim_required`；销售用户访问 Flywheel/Migrate/管理员操作返回 `403 admin_required`；管理员 Flywheel 读取通过。
- QA Docker 容器与卷已清理。

## 发布阻断缺陷

**P1：CSV 导入返回 500，且已部分提交数据。**

复现：隔离 Docker CI 栈中，销售用户登录后向 `/api/import/csv` 上传一条新 CSV。

精确错误：

```text
only '%s', '%b', '%t' are allowed as placeholders, got '%'
```

根因：`src/sales_automation/db.py` 的 `Repository.refresh_campaign_metrics()` 查询包含未转义的 `LIKE 'negative%'`。psycopg 3 将 `%` 解析为参数占位符。导入已写入 campaign、contact、lead，随后刷新 metrics 失败，接口返回 500，形成部分提交。

责任模块：客户生命周期与飞轮 / DB。已发送给对应模块与项目总控。修复后必须重跑 CSV 导入、全量 pytest、Docker 迁移与权限回归。

## 凭据边界

未读取或提交任何 `.env` 文件。readiness 使用仅限 QA 的 dummy 环境变量，未使用真实 API、邮箱或生产数据库。

## 发布结论

**BLOCKED**：基础构建、迁移、readiness、权限通过；CSV 导入 P1 未修复，不批准发布。

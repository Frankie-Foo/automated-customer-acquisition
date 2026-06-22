# 生产数据库迁移说明：016 Resend 邮件回流事件

## 背景

本次不是更换数据库，也不是重建数据库。

这是在现有生产 PostgreSQL 数据库上追加一个小迁移，用于支持 Resend 新回流事件类型。  
如果不执行该迁移，Resend 回传 `delivered`、`complained` 等事件时，数据库 enum 不认识这些值，会导致 webhook 写入失败，邮件状态无法完整闭环。

## 本次迁移文件

```text
migrations/016_resend_delivery_events.sql
```

## 迁移做了什么

给 `email_event_type` enum 追加以下事件：

```text
delivered
delivery_delayed
failed
suppressed
complained
```

这些事件用于记录：

- `delivered`：邮件已送达
- `delivery_delayed`：邮件送达延迟
- `failed`：邮件发送失败
- `suppressed`：被 Resend 抑制发送
- `complained`：用户投诉/标记垃圾邮件

## 运维执行步骤

### 1. 拉取最新代码

```bash
git pull origin main
```

确认最新 commit 至少包含：

```text
d1c353e Prepare production rollout updates
```

### 2. 确认当前连接的是生产数据库

检查生产 `.env` 中的 PostgreSQL 配置，例如：

```env
POSTGRES_HOST=
POSTGRES_PORT=
POSTGRES_DB=
POSTGRES_USER=
POSTGRES_PASSWORD=
```

注意：不要创建新库，不要清空旧库，不要重新导入数据。

### 3. 执行数据库迁移

如果项目是 Docker Compose 部署，优先执行：

```bash
docker compose exec app salesbot migrate
```

如果服务名不是 `app`，先查看服务名：

```bash
docker compose ps
```

然后替换成实际服务名，例如：

```bash
docker compose exec salesbot salesbot migrate
```

如果不是 Docker，可以在项目虚拟环境中执行：

```bash
salesbot migrate
```

或：

```bash
python -m sales_automation.cli migrate
```

### 4. 重启应用服务

```bash
docker compose restart app
```

如果服务名不是 `app`，替换为实际服务名。

## 验证方式

### 1. 检查健康接口

访问：

```text
https://global-autoleads.vertu.cn/api/health
```

至少确认：

```text
database OK
resend OK
sender_email OK
public_url OK
```

### 2. 检查首页

访问：

```text
https://global-autoleads.vertu.cn/
```

应能正常打开登录页，而不是 404。

### 3. 检查 Resend webhook

Resend 后台 Webhook 地址应为：

```text
https://global-autoleads.vertu.cn/webhooks/resend
```

需要监听邮件事件，尤其是：

```text
email.sent
email.delivered
email.opened
email.clicked
email.bounced
email.complained
```

### 4. 业务验证

发一封测试邮件后，在系统里确认：

- 发送后状态进入 `sent_1`
- Resend 显示 Delivered 后，系统邮件反馈出现“已送达”
- 如果退信，系统状态变成 `bounced`
- 如果投诉/退订，系统进入不可继续触达状态

## 如果迁移报错

### 情况 1：提示 enum value already exists

说明部分值已经被加过。  
通常不是严重问题，可以继续确认服务是否正常启动。

### 情况 2：数据库连不上

检查 `.env` 中 PostgreSQL 连接信息是否为当前生产库：

```env
POSTGRES_HOST
POSTGRES_PORT
POSTGRES_DB
POSTGRES_USER
POSTGRES_PASSWORD
```

同时检查数据库安全组、防火墙、Docker 网络。

### 情况 3：首页 404

说明镜像可能不是最新代码，或者 Python 包没有包含前端静态文件。  
请确认已经拉到最新代码并重新构建镜像：

```bash
docker compose build --no-cache
docker compose up -d
```

最新代码的 `pyproject.toml` 已经包含：

```toml
[tool.setuptools.package-data]
sales_automation = ["web_static/*", "web_static/assets/*"]
```

## 回滚说明

PostgreSQL enum 追加值通常不建议回滚删除。  
如果迁移后应用异常，优先回滚应用镜像到上一个版本；数据库中新增的 enum 值可以保留，不会影响旧代码读取已有事件。

## 结论

本次迁移是生产上线邮件状态闭环必须步骤。  
它不会删除数据，不会重建数据库，只是在现有数据库里追加 Resend 回流事件类型。

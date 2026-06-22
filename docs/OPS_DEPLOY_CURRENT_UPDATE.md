# 自动化获客系统当前版本上线说明

## 目标

把本地已经跑通的版本部署到线上网页，使线上也具备：

- 导入公司种子 CSV
- 通过 Tavily 搜索公开 LinkedIn 主页
- 生成联系人与邮箱候选
- 保存导入表里的客户背景
- 根据客户背景和职位生成/发送邮件
- 通过 Resend 使用 `vertuMay@mail.frelys.xyz` 真实发信
- 记录邮件 `sent` 状态

## 代码版本

请先拉取 GitHub 最新 `main`：

```bash
git pull origin main
```

至少需要包含以下提交：

```text
8d98fd1 Prefer Tavily for public LinkedIn search
b18304b Use imported account context in outreach emails
```

仓库地址：

```text
https://github.com/Frankie-Foo/automated-customer-acquisition
```

## 为什么需要迁移数据库

这次不是换数据库，也不是清库。

本地为了让“导入表里的客户背景”进入邮件生成，新增了联系人字段：

```text
contacts.source_context
```

这个字段保存：

- 原始公司名
- 官网
- 类别
- 地区
- 背调/匹配 Vertu 的原因
- 目标职位

对应迁移文件：

```text
migrations/015_contact_source_context.sql
```

迁移内容是给 `contacts` 表加一列：

```sql
ALTER TABLE contacts
  ADD COLUMN IF NOT EXISTS source_context JSONB NOT NULL DEFAULT '{}'::jsonb;
```

该迁移不会删除数据，不会重建表，不会清空客户。

## 线上必须配置的环境变量

线上 `.env` 至少需要：

```env
DB_HOST=线上 PostgreSQL 地址
DB_PORT=5432
DB_USER=postgres
DB_PASSWORD=线上数据库密码
DB_NAME=线上数据库名
DB_CONNECT_TIMEOUT=10

PUBLIC_BASE_URL=https://线上访问域名

RESEND_API_KEY=当前 Resend 发信 key
RESEND_WEBHOOK_SECRET=Resend webhook secret

TAVILY_API_KEY=当前 Tavily key
DEEPSEEK_API_KEY=当前 DeepSeek key

SALESBOT_ADMIN_PASSWORD=强密码
```

如果继续使用当前发件身份，`config.yaml` 中应保持：

```yaml
sender:
  name: vertuMay
  email: vertuMay@mail.frelys.xyz
  provider: resend
  daily_limit: 100
  dry_run: false
```

## Resend 状态

当前本地已验证：

- `RESEND_API_KEY` 可用
- `vertuMay@mail.frelys.xyz` 可真实发信
- `mail.frelys.xyz` 已在 Resend 中显示 `Domain verified`

线上必须使用同一个已验证域名对应的 Resend API key，否则会报：

```text
The mail.frelys.xyz domain is not verified.
```

## 部署步骤

### 1. 拉最新代码

```bash
git pull origin main
```

### 2. 更新线上 `.env`

确认线上 `.env` 包含：

```env
TAVILY_API_KEY=...
RESEND_API_KEY=...
DEEPSEEK_API_KEY=...
PUBLIC_BASE_URL=https://线上访问域名
```

不要提交 `.env` 到 Git。

### 3. 执行数据库迁移

Docker 环境按实际服务名执行，例如：

```bash
docker compose exec salesbot salesbot migrate --config config.yaml
```

如果不是 Docker：

```bash
salesbot migrate --config config.yaml
```

成功后应看到类似：

```text
migrate.completed
```

如果已经迁移过，返回空 applied 也正常。

### 4. 重启服务

Docker：

```bash
docker compose up -d --build
```

或：

```bash
docker compose restart salesbot
```

### 5. 健康检查

访问：

```text
https://线上访问域名/api/health
```

重点检查：

```text
database OK
lead_source OK
resend OK
sender_email OK
dry_run OK
public_url OK
llm OK
```

本地 `public_url` 可能因为 `127.0.0.1` 不通过，线上必须是 HTTPS 域名。

## 功能验收

### 1. 导入公司种子 CSV

页面入口：获客操作 / CSV 或公司种子导入。

CSV 推荐列：

```csv
company_name,category,reason,website,job_titles,industry,location,phone,email
```

其中 `reason` 会进入客户背景，用于邮件个性化。

### 2. LinkedIn 公网搜索

线上应优先使用 Tavily：

```text
tavily -> google_cse
```

Google CSE key 即使不可用，也不应阻断流程。

### 3. 发信验证

先发 1 封测试，再批量发。

确认 Resend Logs 中可看到：

```text
sent / delivered
```

系统客户列表中对应联系人应变为：

```text
sent_1
```

## 常见问题

### Q1：为什么要迁移数据库？

因为新增了 `contacts.source_context` 字段，用来保存导入表里的客户背景。没有这个字段，线上无法把客户背景带入邮件生成。

### Q2：迁移会不会删数据？

不会。迁移只加字段：

```sql
ADD COLUMN IF NOT EXISTS source_context
```

### Q3：Resend 报 domain not verified 怎么办？

说明线上使用的 `RESEND_API_KEY` 对应账号没有验证 `mail.frelys.xyz`。需要使用已验证该域名的 Resend API key。

### Q4：Google CSE 报错怎么办？

当前版本优先 Tavily。只要 `TAVILY_API_KEY` 配好，Google CSE 报错不应阻断 LinkedIn 公网搜索。

### Q5：邮箱没验证能不能发？

可以发，但风险更高。当前本地第一批 10 封是用“姓名 + 官网域名”猜测邮箱发送成功的。生产建议小批量 warmup，并观察退信率。


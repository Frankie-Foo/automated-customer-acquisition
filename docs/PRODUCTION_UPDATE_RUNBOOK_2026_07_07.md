# 公网版本更新运维手册 2026-07-07

本文用于把公网环境 `https://global-autoleads.vertu.cn/` 更新到最新版本。

本次更新包含：

- 邮件发送后客户打开、退信、点击等 Resend webhook 回流能力。
- 登录销售专属邮件签名：
  `Best regards, 登录人 You / BD Manager Of Media East Region | VERTU`
- 客户回复邮件回到登录销售自己的邮箱，也就是用户表里的 `reply_to_email`。
- 生产销售账号 reply-to 一键同步脚本。
- 最新前端 UI 静态文件。

## 1. 需要确认或修改的 `.env`

生产服务器 `.env` 建议放在：

```bash
/opt/salesbot/.env
```

Frank 会提供本地整理好的生产 env 文件，路径通常是：

```text
C:\Users\frank\Desktop\salesbot_production_env_for_ops.env
```

运维需要把这个文件内容复制到服务器 `/opt/salesbot/.env`，并确认下面几项。

### 必须正确

```bash
PUBLIC_BASE_URL=https://global-autoleads.vertu.cn

DB_HOST=你的PostgreSQL地址
DB_PORT=5432
DB_USER=postgres
DB_PASSWORD=你的数据库密码
DB_NAME=autoleads
DB_CONNECT_TIMEOUT=10

RESEND_API_KEY=你的Resend API Key
RESEND_WEBHOOK_SECRET=Resend Webhook Signing Secret

DEEPSEEK_API_KEY=你的DeepSeek Key
PROSPEO_API_KEY=你的Prospeo Key
HUNTER_KEY=你的Hunter Key

SALESBOT_ADMIN_USERNAME=admin
SALESBOT_ADMIN_PASSWORD=强密码
SALESBOT_ADMIN_NAME=Admin

SALESBOT_REQUIRE_PRODUCTION_READY=false
SALESBOT_DB_WAIT_TIMEOUT=180
```

### 可选

```bash
BRAVE_SEARCH_API_KEY=Brave Search Key
NINJAPEAR_API_KEY=
PEOPLEDB_API_KEY=
PDL_API_KEY=
PROXYCURL_KEY=
GOOGLE_CSE_API_KEY=
GOOGLE_CSE_ID=
TAVILY_API_KEY=
SENDGRID_API_KEY=
SLACK_WEBHOOK_URL=
```

### 本次额度目标

本次生产配置按“每个销售每天最多 200 封邮件”设置：

```yaml
quotas:
  global_daily_send_limit: 6000
  default_user_daily_send: 200

sender:
  daily_limit: 6000
```

说明：系统上限放开到 30 人 × 200 封/天；Resend 实际可发量仍以 Resend 后台账户额度和域名信誉为准。

说明：

- `PUBLIC_BASE_URL` 必须是公网 HTTPS 地址，否则退订链接、打开追踪、Webhook 关联会不稳定。
- `RESEND_WEBHOOK_SECRET` 必须填 Resend Webhook 页面里的 `Signing Secret`，不是 Resend API Key。
- `SALESBOT_REQUIRE_PRODUCTION_READY=false` 建议先保留，避免某个可选 API 缺失导致容器启动失败。上线稳定后再考虑改成 `true`。
- `SALESBOT_DB_WAIT_TIMEOUT=180` 可以降低数据库启动慢时的失败概率。

## 2. 更新代码

进入服务器项目目录：

```bash
cd /opt/salesbot
```

拉取最新代码：

```bash
git pull origin main
```

确认最新 commit 至少包含：

```bash
git log -3 --oneline
```

应该看到类似：

```text
6aabfd3 Add sales account reply-to setup tool
cf9fd01 Use sales account identity for outbound replies
```

## 3. 更新 `.env`

把 Frank 提供的 env 文件复制成生产 `.env`：

```bash
cp salesbot_production_env_for_ops.env /opt/salesbot/.env
chmod 600 /opt/salesbot/.env
```

如果文件名或位置不同，就按实际路径复制。

快速检查关键变量是否存在：

```bash
grep -E '^(PUBLIC_BASE_URL|RESEND_API_KEY|RESEND_WEBHOOK_SECRET|DB_HOST|DB_NAME|DEEPSEEK_API_KEY|PROSPEO_API_KEY|HUNTER_KEY)=' /opt/salesbot/.env
```

不要把完整 `.env` 发到群里或提交 Git。

## 4. 重建并启动 Docker

```bash
cd /opt/salesbot

docker compose down
docker compose build --no-cache
docker compose up -d
```

看容器状态：

```bash
docker compose ps
```

看日志：

```bash
docker compose logs -f salesbot
```

如果日志一直出现：

```text
salesbot: waiting for PostgreSQL
salesbot: database did not become ready within 90s
```

优先检查：

- `.env` 里的 `DB_HOST / DB_PORT / DB_USER / DB_PASSWORD / DB_NAME` 是否正确。
- 云服务器能否访问数据库端口。
- 数据库白名单是否放行该服务器内网或公网 IP。
- `SALESBOT_DB_WAIT_TIMEOUT` 是否已经改到 `180`。

## 5. 执行数据库迁移

容器启动后执行：

```bash
docker compose exec salesbot salesbot migrate --config config.yaml
```

如果 `salesbot` 命令不可用，可用：

```bash
docker compose exec salesbot python -m sales_automation.cli migrate --config config.yaml
```

## 6. 同步销售账号 reply-to

本次必须执行一次：

```bash
docker compose exec salesbot python tools/configure_sales_accounts.py --output sales_account_reply_to_update.txt
```

这个脚本会：

- 更新已有销售账号的显示名、角色、配额、`reply_to_email`。
- 把销售账号每日发信上限同步为 `200`。
- 不会重置已有账号密码。
- 如果缺少 `Safae`，会自动创建并输出临时密码。

当前会配置这些账号：

| 账号 | 回复邮箱 |
| --- | --- |
| Frank | frank.fu@vertu.com |
| Vivi | vivien.wang@vertu.cn |
| Viki | Viki.you@vertu.cn |
| Chen | Tony.Santoso@vertu.cn |
| April | april.yang@vertu.cn |
| Gao | mark.gao@vertu.cn |
| Henry | henry.li@vertu.cn |
| Haiwen | Haiwen.he@vertu.cn |
| Ivan | ivan.yu@vertu.com |
| Yubing | ivan.yu@vertu.com |
| Safae | safae@vertu.com |

## 7. Resend Webhook 配置

Resend 后台 Webhook URL 必须是：

```text
https://global-autoleads.vertu.cn/webhooks/resend
```

需要监听的事件建议全选邮件相关事件，至少包括：

```text
email.sent
email.delivered
email.opened
email.clicked
email.bounced
email.complained
email.delivery_delayed
```

Webhook 页面里的 `Signing Secret` 要填进 `.env`：

```bash
RESEND_WEBHOOK_SECRET=whsec_xxx
```

修改 `.env` 后需要重启：

```bash
docker compose restart salesbot
```

## 8. 健康检查

公网检查：

```bash
curl https://global-autoleads.vertu.cn/api/live
curl https://global-autoleads.vertu.cn/api/health
```

容器内检查：

```bash
docker compose exec salesbot salesbot doctor --config config.yaml
```

如果要严格检查：

```bash
docker compose exec salesbot salesbot doctor --config config.yaml --strict
```

重点看：

- `database` OK
- `resend` OK
- `sender_email` OK
- `public_url` OK
- `quotas` OK
- `llm` OK

## 9. 业务验收

上线后做一次小流量真实测试：

1. 用销售账号登录公网。
2. 找一条有 valid 邮箱的测试客户。
3. 生成邮件草稿，确认签名是当前登录人。
4. 发送 1 封测试邮件。
5. 在收件箱确认：
   - 发件显示正常；
   - 邮件签名正常；
   - 退订链接是 `https://global-autoleads.vertu.cn/...`；
   - 回复邮件时收件人是登录销售自己的邮箱。
6. 打开邮件后，在系统邮件中心或客户列表确认打开事件回流。

## 10. 回滚方式

如果新版异常，先查看上一版 commit：

```bash
git log --oneline
```

回到上一个稳定 commit，例如：

```bash
git checkout 30f0066
docker compose down
docker compose build --no-cache
docker compose up -d
```

确认恢复后，再联系 Frank/Codex 修复问题。

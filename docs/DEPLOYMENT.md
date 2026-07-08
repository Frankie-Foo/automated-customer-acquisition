# 部署上线清单

## 目标

先让 3-5 个同事真实使用，再扩大到 30 人。当前版本重点是：

- 登录账号
- 每账号每日获客/发信配额
- 销售账号只看自己名下客户，管理员可看全局
- 多来源邮箱挖掘
- 邮件发送、打开追踪、退订、生命周期和 AI 画像
- 管理员控制台、团队运营日报、Provider 统计

## 必要资源

- 一台稳定服务器，30 人建议 4C8G / 100GB SSD 起步
- PostgreSQL 正式库
- 一个正式访问域名，例如 `https://global-autoleads.vertu.cn`
- Resend 已验证发件域名
- DeepSeek API Key
- 至少一个邮箱发现 API Key，建议 Prospeo + Hunter

免费额度只适合测试。稳定挖新邮箱需要购买邮箱发现和验证 credits。

## 环境变量

部署时不要上传本地 `.env`。在服务器上创建 `.env`，至少包含：

```bash
DB_HOST=
DB_PORT=5432
DB_USER=
DB_PASSWORD=
DB_NAME=
DB_CONNECT_TIMEOUT=10
PUBLIC_BASE_URL=https://你的正式域名

SALESBOT_ADMIN_USERNAME=admin
SALESBOT_ADMIN_PASSWORD=改成强密码
SALESBOT_ADMIN_NAME=Admin

PROSPEO_API_KEY=
HUNTER_KEY=
NINJAPEAR_API_KEY=
PEOPLEDB_API_KEY=
GITHUB_TOKEN=
DEEPSEEK_API_KEY=
RESEND_API_KEY=
RESEND_WEBHOOK_SECRET=
```

可以从 `deployment/production.env.example` 复制生产环境变量模板。

## 推荐生产部署

```bash
cp deployment/production.env.example deployment/production.env
vim deployment/production.env
docker compose --env-file deployment/production.env -f deployment/docker-compose.production.yml up -d --build
```

这套 compose 会启动：

- `postgres`：同机 PostgreSQL
- `salesbot`：应用服务，启动时自动等待数据库并执行迁移
- `caddy`：自动申请 HTTPS 证书并反向代理到应用
- `postgres-backup`：每天自动备份数据库，默认保留 7 天

生产访问地址是 `PUBLIC_BASE_URL`，例如：

```text
https://global-autoleads.vertu.cn
```

查看状态：

```bash
docker compose --env-file deployment/production.env -f deployment/docker-compose.production.yml ps
docker compose --env-file deployment/production.env -f deployment/docker-compose.production.yml logs -f salesbot
```

上线自检：

```bash
docker compose --env-file deployment/production.env -f deployment/docker-compose.production.yml exec salesbot salesbot --config config.yaml doctor --strict
```

如果使用外部云数据库，不使用 compose 内置 PostgreSQL，可以保留 `salesbot` 和 `caddy`，把 `deployment/production.env` 里的 `DB_HOST/DB_USER/DB_PASSWORD/DB_NAME` 改为云数据库连接信息。

## 本地或单容器调试

```bash
cp docker-compose.example.yml docker-compose.yml
docker compose up -d --build
```

服务默认监听 `http://服务器IP:8765`。这个方式适合调试；正式上线优先用 `deployment/docker-compose.production.yml`。

## 创建销售账号

进入容器后创建账号：

```bash
docker compose exec salesbot salesbot --config config.yaml user-add \
  --username sales01 \
  --password "强密码" \
  --display-name "销售01" \
  --source-limit 100 \
  --send-limit 200
```

查看账号：

```bash
docker compose exec salesbot salesbot --config config.yaml user-list
```

本地或服务器上也可以批量创建 30 个销售账号：

```powershell
.\scripts\create_30_sales_users.ps1 -Config config.yaml -Prefix sales -Count 30
```

脚本会把账号和随机密码导出到 `outputs/sales_users_*.csv`。这个文件包含密码，不要提交到 Git，不要发到公共群。

管理员登录后可以在“管理员控制台”新增销售账号、调整每人配额、停用/启用账号、重置密码、查看发件账号池和 warmup 状态。

## 邮箱挖掘策略

当前单客户“邮箱”按钮会按顺序尝试：

1. 已有有效邮箱
2. Prospeo
3. NinjaPear
4. Hunter
5. GitHub commits
6. 邮箱格式推断 + Hunter 验证
7. SMTP 候选验证，默认关闭
8. Gravatar 候选验证
9. 公司官网公开邮箱

只有 `personal_work + valid` 邮箱才会进入可发送状态。公司官网公开邮箱只作为 `company_generic` 候选展示，不会写入正式联系人邮箱。

可在配置里调整瀑布流顺序：

```yaml
email_discovery:
  providers:
    - prospeo
    - ninjapear
    - hunter
    - github
    - pattern_guess
    - smtp_verify
    - gravatar
    - public_website
  max_candidates: 10
  smtp_verify_enabled: false
```

`smtp_verify` 默认关闭。开启前先确认服务器 IP 声誉和网络策略，避免对外 SMTP 探测影响投递信誉。

## 配额和发件账号

系统同时支持用户每日配额和全局每日配额：

```yaml
quotas:
  global_daily_send_limit: 6000
  global_daily_source_limit: 3000
  default_user_daily_send: 200
  default_user_daily_source: 100
```

发件账号默认兼容旧的 `sender` 配置，也可以配置账号池：

```yaml
sender_pool:
  strategy: round_robin
  accounts:
    - name: sales01
      email: sales01@mail.frelys.xyz
      provider: resend
      daily_limit: 200
      warmup_stage: warmup
      dry_run: false
```

30 人各自账号时，先把 30 个发件邮箱配置到 `sender_pool.accounts[]`。示例见 `deployment/sender_pool.30.example.yaml`。

Warmup 建议：

- 第 1-3 天：每账号 10-20 封/天
- 第 4-7 天：每账号 30-50 封/天
- 第 2 周：每账号 60-100 封/天
- 第 3 周：稳定到 200 封/天

系统会按 `warmup_stage=warmup` 和账号创建时间自动压低可发送上限；确认域名声誉稳定后再切到 `production`。

## 团队运营

首页“团队运营与日报”会展示：

- 今日新增线索
- 今日有效邮箱
- 今日发送/打开/回复/退信
- 今日需处理客户
- 每个销售的获客/发信配额使用
- 邮箱 Provider 调用、候选、valid、选中和错误数
- 失败原因统计

客户列表支持筛选：我的客户、待富化、有邮箱待发送、已打开未回复、已回复、退信需处理、第 2 次触达待发送、第 3 次触达待发送、等待池、已放弃。

普通销售只能看到自己名下客户；管理员可以看到全部客户和团队报表。

## 备份

生产 compose 已内置每日备份，文件在：

```text
deployment/backups/
```

手动备份：

```bash
docker compose --env-file deployment/production.env -f deployment/docker-compose.production.yml exec postgres \
  sh -c 'PGPASSWORD="$POSTGRES_PASSWORD" pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB"' \
  | gzip > "deployment/backups/manual_$(date +%F_%H%M%S).sql.gz"
```

恢复到空库前先停应用：

```bash
docker compose --env-file deployment/production.env -f deployment/docker-compose.production.yml stop salesbot
gzip -dc deployment/backups/你的备份.sql.gz | docker compose --env-file deployment/production.env -f deployment/docker-compose.production.yml exec -T postgres \
  sh -c 'psql -U "$POSTGRES_USER" "$POSTGRES_DB"'
docker compose --env-file deployment/production.env -f deployment/docker-compose.production.yml start salesbot
```

上线前需要做一次恢复演练，确认备份文件能恢复到临时库。

## 上线前必须确认

- `PUBLIC_BASE_URL` 是正式 HTTPS 域名
- Resend Webhook 配置到 `/webhooks/resend`
- 发件域名 DKIM/SPF/DMARC 正常
- 每日发送量先从小量开始预热
- 退订链接可打开
- 数据库有备份
- 管理员默认密码已更换
- 30 个销售账号可以登录
- 30 个发件账号已配置并处于 warmup
- 小流量真实发送能在 QQ/Gmail/企业邮箱收到

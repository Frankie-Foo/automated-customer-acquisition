# 部署上线清单

## 目标

先让 3-5 个同事真实使用，再扩大到 30 人。当前版本重点是：

- 登录账号
- 每账号每日获客/发信配额
- 多来源邮箱挖掘
- 邮件发送、打开追踪、退订、生命周期和 AI 画像

## 必要资源

- 一台稳定服务器，建议 2C4G 起步
- PostgreSQL 正式库
- 一个正式访问域名，例如 `https://sales.frelys.xyz`
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

## Docker 部署

```bash
cp docker-compose.example.yml docker-compose.yml
docker compose up -d --build
```

服务默认监听：

```text
http://服务器IP:8765
```

生产建议用 Nginx/Caddy 反向代理到 HTTPS 域名。

## 创建销售账号

进入容器后创建账号：

```bash
docker compose exec salesbot salesbot --config config.yaml user-add \
  --username sales01 \
  --password "强密码" \
  --display-name "销售01" \
  --source-limit 100 \
  --send-limit 100
```

查看账号：

```bash
docker compose exec salesbot salesbot --config config.yaml user-list
```

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
  global_daily_send_limit: 300
  global_daily_source_limit: 500
  default_user_daily_send: 80
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
      daily_limit: 100
      warmup_stage: production
      dry_run: false
```

## 上线前必须确认

- `PUBLIC_BASE_URL` 是正式 HTTPS 域名
- Resend Webhook 配置到 `/webhooks/resend`
- 发件域名 DKIM/SPF/DMARC 正常
- 每日发送量先从小量开始预热
- 退订链接可打开
- 数据库有备份
- 管理员默认密码已更换

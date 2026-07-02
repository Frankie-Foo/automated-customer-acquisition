# 自动化获客系统运维部署交接

## 目标

把系统部署为公网生产地址：

```text
https://global-autoleads.vertu.cn
```

系统包含：线索采集、LinkedIn 公网搜索、邮箱富化、客户生命周期、AI 画像、Resend 发信、打开/退信/退订回流、管理员账号和每日配额。

## 推荐交付方式

优先使用 Git 仓库部署，方便后续更新、回滚和查看变更记录。

如果暂时不方便接 Git，可以使用交付压缩包部署。压缩包不包含 `.env`、密钥、虚拟环境、Git 历史、备份文件。

## 服务器要求

建议：

```text
Ubuntu 22.04 / Debian 12
4C8G
100GB SSD
公网 IP
```

安全组/防火墙放行：

```text
22/tcp
80/tcp
443/tcp
```

## 域名解析

在 `frelys.xyz` 的 DNS 后台添加：

```text
类型：A
主机记录：sales
值：服务器公网 IP
```

确认解析：

```bash
nslookup global-autoleads.vertu.cn
```

## 安装 Docker

```bash
curl -fsSL https://get.docker.com | sh
sudo systemctl enable docker
sudo systemctl start docker
docker compose version
```

## 部署步骤

进入项目目录后执行：

```bash
cp deployment/production.env.example deployment/production.env
nano deployment/production.env
```

必须填写：

```env
SALES_DOMAIN=global-autoleads.vertu.cn
PUBLIC_BASE_URL=https://global-autoleads.vertu.cn

DB_USER=salesbot
DB_PASSWORD=请改成强密码
DB_NAME=overseaspdca

SALESBOT_ADMIN_USERNAME=admin
SALESBOT_ADMIN_PASSWORD=请改成至少12位强密码
SALESBOT_ADMIN_NAME=Admin

RESEND_API_KEY=
RESEND_WEBHOOK_SECRET=
DEEPSEEK_API_KEY=
PROSPEO_API_KEY=
PEOPLEDB_API_KEY=
HUNTER_KEY=
GOOGLE_CSE_API_KEY=
GOOGLE_CSE_ID=
```

如果使用 compose 内置 PostgreSQL，启动：

```bash
docker compose --env-file deployment/production.env -f deployment/docker-compose.production.yml up -d --build
```

如果已经有外部 PostgreSQL，不要启动内置 `postgres` 服务，改用：

```bash
docker compose --env-file deployment/production.env -f deployment/docker-compose.external-db.yml up -d --build
```

外部数据库模式下，`deployment/production.env` 里的数据库配置应指向外部 PostgreSQL：

```env
DB_HOST=外部数据库IP或域名
DB_PORT=5432
DB_USER=postgres
DB_PASSWORD=外部数据库密码
DB_NAME=外部数据库名
```

查看状态：

```bash
docker compose --env-file deployment/production.env -f deployment/docker-compose.external-db.yml ps
docker compose --env-file deployment/production.env -f deployment/docker-compose.external-db.yml logs -f salesbot
```

生产自检：

```bash
docker compose --env-file deployment/production.env -f deployment/docker-compose.external-db.yml exec salesbot salesbot --config config.yaml doctor --strict
```

通过后访问：

```text
https://global-autoleads.vertu.cn
```

## Resend Webhook

系统上线且 HTTPS 可访问后，在 Resend 后台配置 webhook：

```text
https://global-autoleads.vertu.cn/webhooks/resend
```

建议选择事件：

```text
email.sent
email.delivered
email.opened
email.clicked
email.bounced
email.complained
```

把 Resend webhook 的 signing secret 填入：

```env
RESEND_WEBHOOK_SECRET=
```

修改后重启：

```bash
docker compose --env-file deployment/production.env -f deployment/docker-compose.production.yml up -d
```

## 备份

生产 compose 已包含 `postgres-backup`，每日生成备份：

```text
deployment/backups/
```

手动备份：

```bash
docker compose --env-file deployment/production.env -f deployment/docker-compose.production.yml exec postgres \
  sh -c 'PGPASSWORD="$POSTGRES_PASSWORD" pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB"' \
  | gzip > "deployment/backups/manual_$(date +%F_%H%M%S).sql.gz"
```

## 更新版本

如果使用 Git：

```bash
git pull
docker compose --env-file deployment/production.env -f deployment/docker-compose.production.yml up -d --build
```

如果使用压缩包：

1. 备份旧目录的 `deployment/production.env`。
2. 解压新包覆盖代码。
3. 恢复 `deployment/production.env`。
4. 执行：

```bash
docker compose --env-file deployment/production.env -f deployment/docker-compose.production.yml up -d --build
```

## 注意事项

- 不要把 `.env` 或 `deployment/production.env` 提交到 Git。
- 不要把 API key 发到群里，建议用密码管理器或运维私密通道交接。
- 第一天不要直接满量发信，发件账号需要 warmup。
- Caddy 会自动申请 HTTPS 证书，前提是 80/443 已放行且域名解析正确。
- 如果 `doctor --strict` 不通过，不建议开放给销售使用。

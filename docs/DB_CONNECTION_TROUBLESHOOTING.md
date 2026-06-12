# PostgreSQL 连接故障排查

## 现象

部署时 `salesbot` 容器日志反复出现：

```text
salesbot: waiting for PostgreSQL
{"event": "doctor.database", "ok": false}
salesbot: database did not become ready within 90s
```

含义：应用容器无法连接 PostgreSQL。此问题通常与代码无关，优先排查部署命令、环境变量、网络、安全组、数据库账号和数据库授权。

## 1. 确认使用正确 Compose 文件

如果使用已有外部 PostgreSQL，例如：

```text
DB_HOST=10.140.3.125
DB_NAME=autoleads
```

必须使用外部数据库部署文件：

```bash
docker compose --env-file deployment/production.env -f deployment/docker-compose.external-db.yml up -d --build
```

不要使用内置数据库部署文件：

```bash
docker compose --env-file deployment/production.env -f deployment/docker-compose.production.yml up -d --build
```

`docker-compose.production.yml` 会启动内置 PostgreSQL；外部数据库场景应使用 `docker-compose.external-db.yml`。

## 2. 检查 production.env

服务器上的文件路径：

```text
deployment/production.env
```

外部数据库模式下必须类似：

```env
DB_HOST=10.140.3.125
DB_PORT=5432
DB_USER=postgres
DB_PASSWORD=数据库密码
DB_NAME=autoleads
DB_CONNECT_TIMEOUT=5
```

不能写：

```env
DB_HOST=postgres
```

`DB_HOST=postgres` 只适用于内置 PostgreSQL 服务。

确认应用读取到的环境变量：

```bash
docker compose --env-file deployment/production.env -f deployment/docker-compose.external-db.yml config | grep -E "DB_HOST|DB_PORT|DB_USER|DB_NAME"
```

不要在日志或群聊里输出 `DB_PASSWORD`。

## 3. 在服务器测试网络连通性

在部署服务器执行：

```bash
nc -vz 10.140.3.125 5432
```

如果没有 `nc`，使用 PostgreSQL 官方镜像测试：

```bash
docker run --rm postgres:16-alpine pg_isready -h 10.140.3.125 -p 5432 -U postgres -d autoleads
```

结果判断：

- 成功：网络层可达，继续检查账号密码。
- timeout / no route：应用服务器到数据库网络不通。
- connection refused：数据库未监听该地址/端口，或防火墙拒绝。

常见原因：

- 应用服务器和数据库不在同一 VPC/内网。
- 数据库安全组未放行应用服务器 IP。
- 防火墙未放行 `5432/tcp`。
- PostgreSQL 只监听 `localhost`。

## 4. 测试账号密码和库名

在部署服务器执行：

```bash
docker run --rm -e PGPASSWORD='数据库密码' postgres:16-alpine \
  psql -h 10.140.3.125 -p 5432 -U postgres -d autoleads -c 'select 1;'
```

结果判断：

```text
select 1
```

成功则数据库账号、密码、库名都正确。

常见错误：

### password authentication failed

密码错误，重新确认：

```env
DB_PASSWORD=
```

### database "autoleads" does not exist

库名错误，确认：

```env
DB_NAME=autoleads
```

### no pg_hba.conf entry

PostgreSQL 没授权当前应用服务器访问。

需要在数据库侧放行应用服务器 IP 或网段。

### connection timed out

网络或安全组问题，不是账号密码问题。

检查：

- 数据库安全组
- 应用服务器安全组
- VPC/内网连通
- 路由
- 防火墙

## 5. 绕过入口脚本单独跑数据库检查

如果 `salesbot` 容器一直重启，可以直接运行一次数据库检查：

```bash
docker compose --env-file deployment/production.env -f deployment/docker-compose.external-db.yml run --rm \
  --entrypoint salesbot salesbot --config config.yaml doctor --database-only
```

预期成功输出：

```json
{"event":"doctor.database","ok":true}
```

如果 `ok=false`，继续排查网络、账号、密码和库名。

## 6. 数据库连通后重新启动

数据库检查通过后执行：

```bash
docker compose --env-file deployment/production.env -f deployment/docker-compose.external-db.yml up -d --build
```

查看日志：

```bash
docker compose --env-file deployment/production.env -f deployment/docker-compose.external-db.yml logs -f salesbot
```

正常日志应包含：

```text
salesbot: waiting for PostgreSQL
salesbot: running migrations
Dashboard running at http://0.0.0.0:8765
```

## 7. 生产自检

服务启动后执行：

```bash
docker compose --env-file deployment/production.env -f deployment/docker-compose.external-db.yml exec salesbot \
  salesbot --config config.yaml doctor --strict
```

只有 `doctor --strict` 通过后，才建议开放给销售使用。

## 8. 安全提醒

- 不要把 `deployment/production.env` 提交到 Git。
- 不要在群聊或截图里暴露 `DB_PASSWORD`。
- 如果数据库密码已经在聊天截图中出现过，建议部署成功后重置数据库密码，并同步更新 `deployment/production.env`。

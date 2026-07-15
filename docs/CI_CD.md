# CI/CD 发布流程

## 发布原则

代码必须依次通过：本地 Docker 验收、GitHub CI、production 环境批准、生产容器健康检查。任何一层失败都不发布。

不要暴露未加密的 Docker TCP `2375`。Docker 官方建议通过 SSH context 或双向 TLS 保护远程 Docker；本项目的自动部署只使用 SSH，不需要开放 Docker API 端口。

## 开发人员本地验收

Windows 项目根目录执行：

```powershell
.\scripts\test-local-docker.ps1
```

脚本会：

1. 运行 Python 测试和前端构建检查。
2. 构建与生产相同的 Dockerfile。
3. 启动隔离的临时 PostgreSQL 和应用容器。
4. 自动执行数据库迁移。
5. 检查首页及 `/api/live`。
6. 无论成功或失败，都删除临时容器和测试数据卷。

本地访问端口固定为 `18765`，不会占用日常开发使用的 `8765`。

## GitHub CI

`.github/workflows/ci.yml` 在 pull request 以及 `main` 分支 push 时执行：

- Python 单元/集成测试
- React 前端构建
- 完整 Docker 构建
- PostgreSQL + 应用容器冒烟测试

建议只允许通过 pull request 合并到 `main`，并把 `CI / test-and-docker-smoke` 设置为分支保护的必需检查。

## 生产部署

CI 在 `main` 成功后触发 `.github/workflows/deploy-production.yml`。部署 job 使用 GitHub `production` Environment；建议设置 required reviewer，由运维批准后再执行。

GitHub production Environment 需要以下 Secrets：

| 名称 | 内容 |
| --- | --- |
| `PROD_HOST` | 云服务器地址 |
| `PROD_USER` | 专用部署用户，例如 `salesbot-deploy` |
| `PROD_SSH_PORT` | SSH 端口，默认 `22` |
| `PROD_SSH_PRIVATE_KEY` | 专用部署私钥 |
| `PROD_SSH_KNOWN_HOSTS` | 预先核验过的服务器 host key |

Environment Variables：

| 名称 | 建议值 |
| --- | --- |
| `PROD_PATH` | `/opt/salesbot` |
| `PROD_COMPOSE_FILE` | `deployment/docker-compose.external-db.yml`；使用内置 PostgreSQL 时改为 `deployment/docker-compose.production.yml` |

服务器需要提前准备：

```text
/opt/salesbot/
  deployment/production.env
  Git 仓库工作树
```

`production.env` 只保存在服务器，不写入 GitHub 仓库，也不会被部署覆盖。

建议运维创建独立部署用户，只授予该项目目录、Git 拉取和 Docker 运行权限：

```bash
sudo useradd -m -s /bin/bash salesbot-deploy
sudo usermod -aG docker salesbot-deploy
sudo install -d -o salesbot-deploy -g salesbot-deploy /opt/salesbot
```

把 CI 公钥写入 `/home/salesbot-deploy/.ssh/authorized_keys`。在一台可信设备上核验服务器指纹后，把以下命令的输出保存为 `PROD_SSH_KNOWN_HOSTS`：

```bash
ssh-keyscan -H 10.100.0.176
```

首次部署前由运维完成仓库 clone、`deployment/production.env` 配置及一次人工启动。后续更新不再复制 `.env`，CI 只切换代码和应用镜像。

生产脚本会用 commit SHA 生成不可变镜像标签，例如 `salesbot:1a2b3c4d5e6f`。新容器未通过 Docker healthcheck 和公网 `/api/live` 时，脚本自动恢复上一镜像。

## 远程 Docker 的安全使用

人工排查时可使用 SSH context：

```bash
docker context create remote-prod --docker "host=ssh://salesbot-deploy@10.100.0.176"
docker --context remote-prod ps
```

不要使用：

```bash
docker -H tcp://10.100.0.176:2375 ps
```

如果历史上已经开放 `2375`，上线 CI/CD 后应关闭该监听并在防火墙删除对应放行规则。

生产数据库仍需保持每日备份和恢复演练。发布前迁移应保持向后兼容，否则即使镜像自动回滚，旧代码也可能无法读取已变更的表结构。

## 推荐分支流程

```text
功能分支 -> 本地 Docker 验收 -> Pull Request -> GitHub CI -> 合并 main
         -> production 人工批准 -> SSH 部署 -> 健康检查 -> 成功或自动回滚
```

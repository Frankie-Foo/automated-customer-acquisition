# 生产服务器 GitHub Runner

GitHub 托管 Runner 无法访问公司内网地址。生产部署使用安装在公司内网的专用 Self-hosted Runner。

## 1. 注册

仓库管理员打开：

`Settings → Actions → Runners → New self-hosted runner → Linux → x64`

在生产服务器创建非 root 用户，然后执行 GitHub 页面生成的下载和注册命令。注册时使用标签：

```text
salesbot-prod
```

Runner 名称建议：

```text
salesbot-production-01
```

不要把注册令牌写入 Git、聊天记录或长期环境文件；令牌使用后即失效。

## 2. 安装为服务

在 Runner 目录执行：

```bash
sudo ./svc.sh install
sudo ./svc.sh start
sudo ./svc.sh status
```

GitHub 页面应显示 Runner 状态 `Idle`，标签包含：

```text
self-hosted, linux, x64, salesbot-prod
```

## 3. 权限和网络

- Runner 用户只需读取仓库、执行 SSH 客户端和访问生产服务器内网地址。
- 不要给 Runner 用户直接修改数据库的权限。
- 仓库 `main` 分支和 `production` Environment 必须保留保护规则。
- 仅允许可信仓库和受保护分支使用生产 Runner，不运行外部 Fork PR。
- 生产机当前开放的 Docker TCP 2375 应限制在公司内网，后续改为 TLS 2376 或关闭远程端口。

## 4. 验收

1. 在 GitHub Actions 手动运行 `Deploy production`。
2. 指定一个已通过 CI 的完整 commit SHA。
3. 确认任务由 `salesbot-production-01` 执行。
4. 确认迁移、镜像构建、健康检查和公网 `/api/live` 全部通过。
5. 确认失败时回滚到 `deployment/.deployed-image` 中记录的上一镜像。

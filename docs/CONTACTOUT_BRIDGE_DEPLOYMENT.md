# ContactOut Bridge 部署

Bridge 仅用于已获授权、额度独立且允许自动化访问的 ContactOut 账号。不得用于注册账号、绕过验证码、规避平台限制或共享未授权会话。

## 1. 准备服务器密钥

在生产服务器项目目录执行：

```bash
cd /opt/salesbot
install -m 700 -d deployment/secrets
install -m 600 contactout-accounts.json deployment/secrets/contactout-accounts.json
```

文件结构：

```json
{
  "accounts": {
    "contactout-account-01": {
      "email": "authorized-account@example.com",
      "password": "account-password"
    }
  }
}
```

`credential_ref` 必须与管理后台中的 ContactOut 账号引用一致。文件不得进入 Git。

## 2. 配置环境变量

在生产 `.env` 中配置：

```env
CONTACTOUT_BRIDGE_URL=http://contactout-bridge:8790
CONTACTOUT_BRIDGE_KEY=<32字符以上随机密钥>
CONTACTOUT_CREDENTIALS_FILE_HOST=./secrets/contactout-accounts.json
CONTACTOUT_POLL_INTERVAL_SECONDS=60
```

Bridge 端口只在 Docker 网络内暴露，不映射到公网。

## 3. 构建与启动

```bash
cd /opt/salesbot
docker compose --profile contactout \
  -f deployment/docker-compose.external-db.yml \
  up -d --build contactout-bridge contactout-worker
```

## 4. 验收

```bash
docker compose --profile contactout \
  -f deployment/docker-compose.external-db.yml \
  ps contactout-bridge contactout-worker

docker compose --profile contactout \
  -f deployment/docker-compose.external-db.yml \
  exec contactout-bridge \
  python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8790/ready').read().decode())"
```

预期 `/ready` 返回 `ok=true` 和已加载账号数。先用一个授权账号、一个精确 LinkedIn 线索验收；确认结果、额度和幂等记录正确后再启用其他账号。

若返回 `challenge_required` 或 `reauth_required`，暂停该账号并人工完成平台要求的验证，不自动绕过。

## 5. 首次登录并保存会话

在可显示浏览器的受信任电脑执行。命令会自动填写账号密码；平台要求验证码时，在打开的窗口完成一次验证。此步骤不搜索联系人、不消耗 Reveal 额度。

```powershell
python -m contactout_bridge.bootstrap_sessions `
  --credentials "C:\secure\contactout-accounts.txt" `
  --session-dir "$env:LOCALAPPDATA\salesbot\contactout-sessions" `
  --timeout 300
```

凭据文件可使用上方 JSON 格式，或按“邮箱一行、密码一行”交替排列。密码不放命令行。生成的会话文件不得进入 Git；复制到生产 `contactout-bridge` 的 `/data/sessions` 后权限设为 `0600`。会话失效时重新执行，不绕过验证码或平台风控。

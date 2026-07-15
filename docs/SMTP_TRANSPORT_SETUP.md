# SMTP 发信与 IMAP 回复回流

系统支持用同一个企业邮箱完成真实发信和自动收取客户回复：

- SMTP 负责发送。
- IMAP 只读扫描收件箱，不改变 Foxmail 的已读状态。
- 系统优先用 `In-Reply-To` / `Message-ID` 匹配原始邮件，找不到时再按客户邮箱匹配。
- 回复自动写入邮件反馈，并把客户推进到已回复/C 阶段。

## 当前生产邮箱

使用 [deployment/env.global-vertu.example](../deployment/env.global-vertu.example) 作为模板。核心配置：

```dotenv
MAIL_PROVIDER=smtp
MAIL_FROM_EMAIL=global@vertu.com
SMTP_HOST=smtp.exmail.qq.com
SMTP_PORT=465
SMTP_USER=global@vertu.com
SMTP_PASSWORD=客户端专用密码
SMTP_SECURITY=ssl
SMTP_ENVELOPE_FROM=global@vertu.com
SMTP_ALLOW_FROM_ALIAS=false

IMAP_HOST=imap.exmail.qq.com
IMAP_PORT=993
IMAP_USER=global@vertu.com
IMAP_PASSWORD=同一个客户端专用密码
IMAP_FOLDER=INBOX
IMAP_LOOKBACK_DAYS=14
MAILBOX_POLL_INTERVAL_SECONDS=60

OUTBOUND_IDENTITY_MODE=legacy
```

不要填写网页登录密码，应使用企业邮箱生成的客户端专用密码。

## 邮箱别名

Foxmail 只能修改显示名称，例如：

```text
From: May | VERTU <global@vertu.com>
```

它不能授权一个新的真实发件地址。要发送：

```text
From: May | VERTU <may@global.vertu.com>
```

企业邮箱管理员必须先完成：

1. 为邮箱创建 `may@global.vertu.com` 别名，或创建独立邮箱。
2. 确认该别名的来信会投递到统一收件箱。
3. 为 SMTP 登录账号授予该地址的 Send As 权限。
4. 实测服务端不会拒绝或改写 From。

全部通过后才能设置：

```dotenv
SMTP_ALLOW_FROM_ALIAS=true
OUTBOUND_IDENTITY_MODE=centralized_alias
OUTBOUND_SENDING_DOMAIN=global.vertu.com
```

未授权时保持 `SMTP_ALLOW_FROM_ALIAS=false`。系统仍会显示当前登录销售的姓名，但实际发件地址使用 SMTP 登录邮箱。

## Docker 运行

生产 Compose 已包含 `mailbox-worker`，默认每 60 秒轮询一次：

```bash
docker compose -f deployment/docker-compose.production.yml up -d --build
docker compose -f deployment/docker-compose.production.yml logs -f mailbox-worker
```

单次手动检查：

```bash
docker compose -f deployment/docker-compose.production.yml exec salesbot \
  salesbot mailbox-poll --config config.yaml --limit 50
```

## 验收

1. 从网站向公司测试邮箱发送一封邮件。
2. 确认 From、显示名称、正文和签名正确。
3. 在收件端直接回复该邮件。
4. 等待最多 60 秒。
5. 网站邮件中心出现回复，客户进入已回复/C 阶段。
6. 再运行一次轮询，确认同一回复不会重复入库。

上线前先小流量测试，不要第一天直接发送 200 封。

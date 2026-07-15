# global.vertu.com 上线清单

## 目标身份

| 登录账号 | 客户看到的发件地址 |
| --- | --- |
| May | `may@global.vertu.com` |
| Viki | `viki@global.vertu.com` |
| April | `april@global.vertu.com` |
| Ivan | `ivan@global.vertu.com` |

其他账号按相同规则自动生成。管理员也可以在用户管理中单独修改“发件别名”。员工不需要提供个人 SMTP 密码，但企业邮箱管理员必须授权统一 SMTP 账号以这些别名 Send As。

## IT 与 Resend 配置

1. 使用已创建的 `global@vertu.com` 企业邮箱，开启 SMTP 和 IMAP，并生成客户端专用密码。
2. 创建 `may@`、`viki@`、`april@` 等别名，并授权专用账号 Send As。
3. 配置 `global.vertu.com` 的 SPF、DKIM、DMARC。
4. 在 Resend 添加回信域 `reply.global.vertu.com` 并开启 Receiving。
5. 把 Resend 显示的 MX 记录原样添加到 `vertu.com` DNS。
6. Webhook 地址使用 `https://global-autoleads.vertu.cn/webhooks/resend`。
7. Webhook 至少勾选 `email.received`；Resend 仅承担收件回流。
8. 将 Webhook Signing Secret 填入生产 `.env` 的 `RESEND_WEBHOOK_SECRET`。

不要修改 `vertu.com` 根域 MX，不会影响现有 `@vertu.com` 企业邮箱。

## DNS 验收

```powershell
Resolve-DnsName global.vertu.com -Type TXT
Resolve-DnsName reply.global.vertu.com -Type MX
```

第二条必须返回 Resend 提供的 MX 地址。没有 MX 时禁止真实发送，否则客户回复会退信。

## 应用切换

将 `deployment/env.global-vertu.example` 中的变量合并到服务器 `/opt/salesbot/.env`，补齐真实密钥后执行：

```bash
cd /opt/salesbot
docker compose -f deployment/docker-compose.production.yml up -d --build
docker compose -f deployment/docker-compose.production.yml logs -f --tail=100 salesbot
```

## 真实验收

1. May 登录并给内部测试邮箱发一封邮件；先确认企业邮箱已授予 May 别名 Send As 权限。
2. From 应为 `May <may@global.vertu.com>`。
3. Reply-To 应为签名地址 `reply+...@reply.global.vertu.com`。
4. 打开邮件后，网站显示“已打开”。
5. 回复邮件后，网站显示“已回复”，客户归属 May，并进入 SABCD 的 C 阶段。
6. 确认无退信后再逐步提高每日发送量。

## 回滚

如果 DNS 或 Receiving 未通过，设置 `OUTBOUND_IDENTITY_MODE=legacy` 并重启。系统会恢复使用底层发件账号和用户真实回复邮箱。

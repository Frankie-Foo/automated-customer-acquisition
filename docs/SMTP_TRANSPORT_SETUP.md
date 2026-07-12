# SMTP 发信切换手册

## 推荐架构

- 发信：统一企业邮箱通过 SMTP 发送。
- 发件显示名：使用当前登录销售姓名。
- From 地址：默认使用 SMTP 登录邮箱，避免未经授权的别名被服务器拒绝。
- Reply-To：继续使用系统签名回复地址。
- 收件回流：继续使用 Resend Receiving 和 `/webhooks/resend`，自动匹配客户与销售。

这意味着不需要收集十个销售的邮箱密码，只需要一个专门用于获客的企业邮箱客户端专用密码。

## 腾讯企业邮箱参数

腾讯企业邮箱常用配置为：

```dotenv
MAIL_PROVIDER=smtp
SMTP_HOST=smtp.exmail.qq.com
SMTP_PORT=465
SMTP_USER=partnerships@outreach.vertu.cn
SMTP_PASSWORD=客户端专用密码
SMTP_SECURITY=ssl
SMTP_ENVELOPE_FROM=partnerships@outreach.vertu.cn
SMTP_ALLOW_FROM_ALIAS=false
```

不要填写网页登录密码。启用安全登录后，应在企业邮箱设置中生成客户端专用密码。

## config.yaml

`sender.email` 必须与 SMTP 登录账号或已授权 Send As 地址一致：

```yaml
sender:
  name: VERTU Partnerships
  email: partnerships@outreach.vertu.cn
  provider: ${MAIL_PROVIDER}
  daily_limit: 200
  dry_run: false
```

如果使用 `sender_pool.accounts[]`，里面每个启用账号的 `provider` 也必须改成 `smtp`，否则账号级配置会覆盖 `sender.provider`。

## 销售身份

默认 `SMTP_ALLOW_FROM_ALIAS=false`：

```text
From: Viki <partnerships@outreach.vertu.cn>
Reply-To: reply+v1.<contact>.<user>.<step>.<signature>@reply.outreach.vertu.cn
```

只有邮箱管理员明确为该 SMTP 账号配置了域内别名或 Send As 权限，才能设为：

```dotenv
SMTP_ALLOW_FROM_ALIAS=true
```

此时系统会尝试发送：

```text
From: Viki <viki@outreach.vertu.cn>
```

未授权时不要开启，否则可能出现 SMTP 拒绝、From 被重写或进入垃圾箱。

## 上线步骤

1. 创建专用获客邮箱，不使用员工个人邮箱。
2. 开启 SMTP 服务并生成客户端专用密码。
3. 把环境变量交给运维写入服务器 `.env`，权限设为仅部署用户可读。
4. 更新代码并重启容器。
5. 执行：

   ```bash
   salesbot doctor --config config.yaml
   ```

6. 先只向公司测试邮箱发送一封，检查 From、Reply-To、正文和签名。
7. 回复测试邮件，确认回复正文进入网站并归属正确销售。
8. 小流量 warmup，不要第一天直接发送 200 封。

## 失败处理

- SMTP 明确拒绝：记录为发送失败，可以修复后人工重试。
- SMTP 网络超时：系统不会自动重发，因为服务端可能已经接收；先查发件箱再决定是否重试。
- Resend Receiving 异常：不影响 SMTP 发信，但客户回复不会自动进入客户池，应暂停批量发送并修复回流。

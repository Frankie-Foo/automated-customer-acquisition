# Resend 邮件回流闭环上线说明

## 目标

让 Resend 后台看到的邮件状态同步到获客系统：

- `Delivered` -> 系统记录 `delivered`
- `Bounced` -> 系统记录 `bounced`，客户状态改为退信，停止后续触达
- `Opened` -> 系统记录 `opened`
- `Clicked` -> 系统记录 `clicked`
- `Complained` -> 系统记录 `complained`，客户状态改为退订，停止后续触达
- `Failed / Suppressed` -> 系统记录失败状态，客户不再继续发送

## 本次代码改动

需要部署包含以下改动的版本：

- 新增数据库迁移：`migrations/016_resend_delivery_events.sql`
- `WebhookService` 支持 `email.delivered / email.complained / email.failed / email.suppressed / email.delivery_delayed`
- 如果 webhook 里没有 `contact_id`，会用 Resend 邮件 `id` 匹配系统发送时保存的 `message_id`
- 客户列表增加 `delivered_count`
- 运营日报增加 `delivered_today`
- 前端展示“已送达”

## 部署步骤

1. 拉取最新代码。
2. 重新构建镜像或重新安装 Python 包。
3. 启动应用，让程序自动执行数据库迁移。
4. 确认迁移已执行：

```sql
SELECT unnest(enum_range(NULL::email_event_type));
```

结果里应该包含：

```text
delivered
delivery_delayed
failed
suppressed
complained
```

5. 访问健康检查：

```text
https://global-autoleads.vertu.cn/api/health
```

数据库必须为 `OK`。

## Resend Webhook 配置

Resend 后台进入 `Webhooks`，配置：

```text
Endpoint URL:
https://global-autoleads.vertu.cn/webhooks/resend
```

至少勾选：

```text
email.delivered
email.bounced
email.opened
email.clicked
email.complained
email.failed
email.suppressed
email.delivery_delayed
```

如果要接收客户直接回信，还需要配置 inbound/receiving，并监听：

```text
email.received
```

当前系统的“真实回复”自动识别，依赖 Resend 是否能把回信打到 webhook；如果没有开启 inbound，只能人工查看邮箱收件箱。

## Webhook Secret

线上 `.env` 必须配置 Resend webhook signing secret：

```text
RESEND_WEBHOOK_SECRET=whsec_xxx
```

注意：这是 Webhook 页面里的 signing secret，不是 Resend API key。

## 验证方式

部署后，在 Resend 后台对最近一封邮件点 `Replay` webhook，或者发一封测试邮件，然后查看系统：

1. 客户列表的“邮件反馈”出现 `已送达` 或 `已退信`
2. 运营日报出现 `今日送达`
3. 退信客户进入 `bounced`，后续不会继续发送

## 历史数据回填

部署前已经发生的 Delivered/Bounced 不一定会自动进系统。两种处理方式：

1. 在 Resend 后台对历史 webhook 点击 `Replay`。
2. 如果 Resend API key 有读取日志权限，可写脚本按 Resend email id 回填。

当前看到的截图里，`n.bourdais@samsung.com` 已经按截图手动回填为 `bounced`；其它 Delivered 需要部署新版本和执行迁移后再回填或 Replay。

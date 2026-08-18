# ContactOut 自动队列

## 目标

系统会从销售自己的私有客户池中筛选有 LinkedIn URL、但没有有效邮箱的联系人，自动分配到该销售已授权且当前可用的 ContactOut 账号。

账号选择按“今日已用 + 已排队”最少优先。每个账号的最终消耗由数据库事务锁定，不能超过 `daily_limit`；当前 8 个账号、每个每天 5 条时，理论上最多 40 条/天。全局额度仍以 `contactout.global_daily_limit` 为准。

## 管理员配置

在管理员控制台逐个添加授权账号：

- 账户标识：内部唯一名称，例如 `contactout-01`
- 显示名称：管理员可识别的名称
- 脱敏身份：例如 `k4***@vertu.cn`
- 凭据引用：例如 `contactout/account-01`
- 分配销售：绑定到具体销售账号
- 每日额度：按 ContactOut 实际授权额度填写，默认 5
- 状态：授权正常时设为 `active`

密码、验证码、浏览器会话和原始账号文件只放在受控的 bridge/凭据库，不放入 PostgreSQL、`.env`、CSV、日志或 Git。

## 自动运行

调度器会按以下顺序执行：

1. 自动入队最多 `contactout.auto_queue_limit` 条候选。
2. 处理最多 `contactout.scheduler_limit` 条队列任务。
3. 账号状态、账号日额度、全局额度、联系人归属在执行前再次校验。
4. 成功、无匹配、需要复核、登录失效和限流都会写入任务状态。

示例配置：

```yaml
contactout:
  bridge_url: ${CONTACTOUT_BRIDGE_URL}
  global_daily_limit: 50
  auto_queue_limit: 40
  scheduler_limit: 40
```

如果使用 Docker 定时任务，运行现有调度器即可；不需要销售逐条选择账号。管理员也可以点击 ContactOut 队列处理按钮，按钮会先自动入队，再处理队列。

## 销售端

销售可以调用 `POST /api/contactout/auto-queue`，系统只处理当前销售自己的私有客户。单条入队 `POST /api/contactout/jobs` 不再强制填写 `account_id`；省略时自动选择绑定账号。原有手动指定账号方式仍兼容。

系统不会自动处理公共池、其他销售私池、没有 LinkedIn URL 的联系人，也不会把未验证邮箱直接标记为可发送。

## 运行前检查

```powershell
python -m sales_automation.cli doctor --config config.yaml --strict
python -m sales_automation.cli contactout-run --config config.yaml --limit 40
```

`contactout-run` 只会在 `CONTACTOUT_BRIDGE_URL` 和 `CONTACTOUT_BRIDGE_KEY` 都配置时调用 bridge。bridge 未配置时安全跳过，不会读取或尝试登录原始账号文件。

## 失败处理

- `contactout_account_unavailable`：该销售没有可用授权账号或今日额度已满。
- `daily_quota_exhausted`：全局或账号额度已满，任务等待下一日。
- `challenge_required` / `reauth_required`：账号暂停，需在授权 bridge 中人工恢复。
- `review`：匹配结果不够确定，只进入复核，不自动写入正式邮箱。

不要通过注册更多账号、轮换账号规避平台限制。只使用已获授权的账号及其实际订阅额度。

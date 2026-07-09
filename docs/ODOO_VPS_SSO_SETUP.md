# Odoo / VPS 单点登录接入说明

目标：同事先登录 Odoo / VPS，然后从菜单或工作台卡片打开自动化获客系统，不再输入获客系统账号密码。

## 1. Odoo / VPS 入口 URL

Odoo / VPS 打开系统时，把当前登录态带到获客系统：

```text
https://global-autoleads.vertu.cn/?session_id=<odoo_session_id>&user_id=<odoo_res_users_id>#dashboard
```

字段说明：

- `session_id`：当前 Odoo 登录 session。
- `user_id`：Odoo `res.users.id`，必须是数字。
- `#dashboard` 可以换成 `#source`、`#research`、`#outreach`、`#followup`。

## 2. 获客系统后端流程

1. 前端发现 URL 带 `session_id + user_id`。
2. 前端调用 `POST /api/auth/vps-login`。
3. 后端用 `session_id` 请求 Odoo：

```text
POST {ODOO_BASE_URL}/web/session/get_session_info
Cookie: session_id=<odoo_session_id>
```

4. 后端强制校验 Odoo 返回的 `uid == user_id`。
5. 校验通过后，后端读取员工信息：
   - 姓名
   - 工号 `barcode`
   - 工作邮箱
   - 部门
6. 系统按以下顺序匹配本地销售账号：
   - `odoo_user_id`
   - `vps_barcode`
   - `reply_to_email`
   - `username`
7. 找不到本地账号时，如果 `VPS_SSO_AUTO_CREATE_USERS=true`，自动创建销售账号。
8. 获客系统写入自己的 `salesbot_session` cookie，后续接口只认获客系统 cookie。
9. 前端清理地址栏里的 `session_id` 和 `user_id`。

## 3. 生产 env 必填

```env
PUBLIC_BASE_URL=https://global-autoleads.vertu.cn
VPS_SSO_ENABLED=true
ODOO_BASE_URL=https://admin.vertu.cn
VPS_SSO_AUTO_CREATE_USERS=true
VPS_SSO_IFRAME_COOKIE=true
```

如果不是 iframe 嵌入，而是普通新页面跳转，也可以保留 `VPS_SSO_IFRAME_COOKIE=true`，生产 HTTPS 下没有副作用。

## 4. 数据库迁移

上线前执行一次迁移：

```bash
python -m sales_automation.web --config config.yaml
```

或登录管理员后点“初始化/迁移数据库”。

新增字段：

- `sales_users.odoo_user_id`
- `sales_users.vps_barcode`
- `sales_users.department`
- `sales_users.auth_provider`

## 5. 自测

1. 从 Odoo / VPS 打开带参数 URL。
2. 页面不应出现账号密码登录页。
3. 登录后地址栏不应再保留 `session_id`、`user_id`。
4. 打开浏览器开发者工具，确认接口带 `salesbot_session` cookie。
5. 用销售账号进入系统，只能看到自己的客户。
6. 禁用本地用户后，再从 Odoo 打开应返回“账号已被禁用”。
7. 修改 URL 里的 `user_id`，应返回“登录已过期，请从 VPS 重新打开”。

## 6. 安全注意

- 必须 HTTPS。
- 不要在日志里打印完整 `session_id`。
- 不要只信任 URL 参数，后端必须回查 Odoo。
- Odoo session 只用于一次登录交换，后续业务接口只认获客系统自己的 session。

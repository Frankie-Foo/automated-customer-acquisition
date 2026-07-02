# MCP 接入说明

本项目已增加一个 MCP Server，用于让 Claude Code、Codex、桌面 Agent 或内部自动化系统调用获客系统能力。

第一版定位是“受控操作层”：可以查客户、导入公司种子并获客、生成邮件草稿、生成客户画像、更新客户阶段；暂不开放直接发送邮件工具，避免 Agent 误发。

## 可用工具

| Tool | 作用 | 是否会发邮件 |
| --- | --- | --- |
| `search_customers` | 按关键词、状态、筛选器查询客户 | 否 |
| `get_customer_detail` | 查看单个客户详情、邮箱候选、事件、画像 | 否 |
| `import_and_source_leads` | 输入 CSV 文本，执行公司种子获客和 LinkedIn 公网搜索 | 否 |
| `generate_outreach_email` | 基于客户资料生成个性化邮件草稿 | 否 |
| `update_customer_stage` | 更新客户生命周期、SABCD、跟进备注 | 否 |
| `generate_customer_profile` | 生成或刷新 AI 客户画像 | 否 |

## 本地启动

```powershell
$env:PYTHONPATH="C:\Users\frank\Documents\Codex\2026-06-03\files-mentioned-by-the-user-linkedin\src"
$env:SALESBOT_MCP_USERNAME="sales_test"
C:\Users\frank\Documents\Codex\2026-06-03\files-mentioned-by-the-user-linkedin\.venv\Scripts\python.exe -m sales_automation.mcp_server --config C:\Users\frank\Documents\Codex\2026-06-03\files-mentioned-by-the-user-linkedin\config.yaml
```

`SALESBOT_MCP_USERNAME` 决定 MCP 默认代表哪个系统用户执行操作。普通销售只能看到自己的客户；管理员可以看全局数据。

## Claude Desktop / Codex 配置示例

```json
{
  "mcpServers": {
    "sales-automation": {
      "command": "C:\\Users\\frank\\Documents\\Codex\\2026-06-03\\files-mentioned-by-the-user-linkedin\\.venv\\Scripts\\python.exe",
      "args": [
        "-m",
        "sales_automation.mcp_server",
        "--config",
        "C:\\Users\\frank\\Documents\\Codex\\2026-06-03\\files-mentioned-by-the-user-linkedin\\config.yaml"
      ],
      "env": {
        "PYTHONPATH": "C:\\Users\\frank\\Documents\\Codex\\2026-06-03\\files-mentioned-by-the-user-linkedin\\src",
        "SALESBOT_MCP_USERNAME": "sales_test"
      }
    }
  }
}
```

管理员模式把 `SALESBOT_MCP_USERNAME` 改成 `admin`。生产环境建议给每个外部 Agent 单独创建一个低权限账号，不共用管理员账号。

## CSV 获客输入格式

`import_and_source_leads` 接收 CSV 文本，推荐表头：

```csv
company_name,category,reason,website,job_titles,industry,location,phone,email
Luxepolis,二手奢侈品平台,印度首屈一指的二手奢侈品电商,luxepolis.com,"founder,owner,partner,director",luxury resale,India,,
```

系统会按公司种子搜索公开 LinkedIn 主页，补公司域名，生成邮箱候选，并在配额允许时做少量验证。

## 生产注意事项

- MCP Server 直接复用现有数据库、配额、客户权限和获客逻辑。
- `import_and_source_leads` 会消耗获客配额，但不会自动发邮件。
- 第一版没有开放 `send_email`，发送仍在网页端或后台批处理里执行。
- `.env`、API Key、数据库密码不要写入 MCP 配置文件，只放到服务器环境变量或 `.env`。
- 如果要给 Agent 开放发信，建议另做二次确认机制：草稿预览、收件人白名单、每日上限、管理员审批。

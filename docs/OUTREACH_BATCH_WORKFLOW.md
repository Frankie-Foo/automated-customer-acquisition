# 可复用客户触达批次

## 数据与入口

PostgreSQL 是唯一业务数据源。网页“发邮件 / 已发送邮件 / 外联批次”按负责销售和来源批次展示客户名单、调研摘要、邮件主题正文、发送及回复计数、当前生命周期和待办。点击客户可查看原有客户工作台及完整沟通记录。

销售只查看自己的批次和当前名下客户；管理员可查看全局。客户转移后，原销售不能从批次详情绕过私池隔离。

普通名单导入仍使用现有导入入口和 campaigns/leads，不需要另建一套客户库。已有客户可以调用 `POST /api/outreach-batches`，提交 `name`、`contact_ids`、`source_ref`。这里只允许本销售的私池客户，不会发送或自动改派其他人的客户。

## 状态和归因

- 登记批次不等于已背调、已生成草稿或已发送。
- 原名单、原始行和登记时的客户信息保存在 leads.raw_data；重复登记相同批次不覆盖原快照。
- 公司调研来源、查询时间和限制保存在 contact_research。已有规则画像不标作已完成背调。
- 用现有 PersonalizedEmailService 保存草稿、审核、发送。指定 `campaign_id` 时校验批次、联系人和销售归属。
- 邮件发送后保留原批次、写信时的证据及生成来源；保存实际收件邮箱、最终正文、附件文件名、发件邮箱和 Message-ID。
- 重新打开已有草稿时沿用草稿的批次归属；后台写信归属实际销售，审核状态从草稿记录读取，不覆盖已发送或失败状态。
- 批次计数按 outreach_messages.campaign_id 和 sent_at 计算，排除 dry_run 和非邮件渠道；历史发送及历史客户回复不自动计入新批次。
- 生命周期和待办读取现有客户/跟进表，不复制另一份状态。邮件“已发送”表示服务商接受，不代表送达；打开是图片加载事件，不等于人工阅读。

## 模型接入

保留现有 `llm` / `llm.gateway` 和 LLMGateway，配置 provider、base_url、model 及服务端密钥；沿用预算、缓存和调用记录。网页统计和生命周期不依赖模型。

人工稿、模板回退、真实模型结果分别记录为 custom、template、provider:model。模型不可用不能冒充已完成 AI 背调。公司事实、联系人职责、合作假设和待核事项必须分开，不能推断私人喜好或虚构新闻。

此变更是记录链和可视化，不新增“自动背调全部客户并自动批准群发”的常驻 worker。模型接入后仍需复验端到端任务调度、事实证据、审核、每日额度、附件和真实投递。

## 运维工具

`tools/register_outreach_batch.py` 接收含 contact_id（或客户ID）列的既有客户 XLSX。默认只检查；`--apply` 登记，`--claim-public` 显式将公共池客户分给所选销售，其他销售客户排除。每个销售/名单使用自己的参数，不硬编码 April 或地区。

`tools/prepare_outreach_batch.py` 将已审阅的来源、摘要、主题和正文保存为待审核草稿，不批准、不发送。

`tools/preview_outreach_client.py` 在本机提供业务只读预览，允许正常登录、退出和本人修改密码，禁止客户修改和发信，也禁止读取时会同步发件配置的管理接口，不启动迁移或后台任务。它不是公网发布。

## 2026-09-22 样例

生产批次 #10：April India 500 - 2026-09-22。输入500人，499人登记给April，1位Frank客户未改派。原文件哈希保存在批次 metadata 中。

Aza Fashions、Confidential Couture 两位联系人已保存官网业务核对和个性化草稿，质量检查 ready。仅为合作匹配初筛，人员在职/权限和信用合规尚未完整核实。当前没有向这批客户发送邮件，不得将500人全部标成已背调或已触达。

官网依据：[Aza](https://www.azafashions.com/en-gb/about-aza)、[Confidential Couture](https://confidentialcouture.com/pages/about-us)。

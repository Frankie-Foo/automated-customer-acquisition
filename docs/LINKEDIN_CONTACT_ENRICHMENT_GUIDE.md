# LinkedIn 主页链接匹配联系方式方案说明

更新日期：2026-07-08

## 结论

系统里已经有这一环，但不是单纯“拿 LinkedIn URL 直接换邮箱”的单点方案，而是多 Provider 串联：

1. 线索里保存 LinkedIn URL、姓名、公司、官网域名。
2. 社媒富化：优先用 PeopleDB / People Data Labs 按 LinkedIn URL 或公开标识补社媒主页、个人资料。
3. 邮箱富化：用 Prospeo、NinjaPear、Hunter、GitHub commits、Gravatar、邮箱规则猜测、官网公开邮箱等多个来源找候选邮箱。
4. 只把 `personal_work + valid` 的邮箱写入正式联系人邮箱。
5. `info@ / sales@ / contact@ / support@` 等公司通用邮箱只放候选，不自动作为正式发信邮箱。

## 当前系统接入点

配置项在 `config.example.yaml`：

- `PROSPEO_API_KEY`
- `HUNTER_KEY`
- `NINJAPEAR_API_KEY`
- `PEOPLEDB_API_KEY`
- `PDL_API_KEY`
- `PROXYCURL_KEY`
- `GOOGLE_CSE_API_KEY`
- `BRAVE_SEARCH_API_KEY`

核心代码：

- `src/sales_automation/email_discovery.py`：邮箱发现 Provider 编排。
- `src/sales_automation/services/enrichment.py`：邮箱富化入口。
- `src/sales_automation/services/social_enrichment.py`：社媒富化入口。
- `src/sales_automation/clients.py`：Prospeo / Hunter / PeopleDB / PDL / Proxycurl 客户端。

前端操作：

- 单个客户行的“邮箱”：找邮箱。
- 单个客户行的“社媒”：根据 LinkedIn URL / GitHub 等补社媒。
- 批量处理里的“富化邮箱（最多 25 条）”：批量找邮箱。
- LinkedIn 公网搜索：先找公开 LinkedIn 主页，再进入候选池。

## Provider 对比

| Provider | 系统用途 | LinkedIn URL 支持 | 价格/额度参考 | 准确度判断 |
|---|---|---:|---|---|
| Prospeo | 主力邮箱富化，找工作邮箱 | 支持，`linkedin_url` 可作为匹配字段 | Free 100 credits/月；Starter 2,000/user/月；Growth 5,000/user/月；Pro 15,000/user/月，具体价格以官网为准 | 当前最适合“姓名 + 公司域名 + LinkedIn URL”的组合；命中率受地区和行业影响很大 |
| Hunter | 邮箱查找和验证 | 支持姓名+域名，也支持 LinkedIn handle；系统当前主要用姓名+域名和验证 | Free 50 credits/月；Starter $49/月 2,000 credits；Growth $149/月 10,000 credits；Scale $299/月 25,000 credits | 适合验证和已知公司域名下的邮箱查找；对中东、俄语、小公司命中不稳定 |
| PeopleDB | 社媒资料补全 | 支持 LinkedIn public identifier | 以 PeopleDB 实际后台为准 | 更适合补 LinkedIn/GitHub/Twitter 等社媒，不保证邮箱 |
| People Data Labs | 个人资料/社媒富化 | 支持 `profile` 参数传 LinkedIn URL | 官方价格按用量/方案，通常需账户审核 | 适合画像和社媒资料，邮箱仍需二次验证 |
| NinjaPear / Nubela 类接口 | 公司员工搜索、工作邮箱 | 取决于接口能力 | 以供应商后台为准 | 适合从公司网站+职位找员工，但稳定性要看区域 |
| GitHub commits | 免费补充候选 | 不直接支持 LinkedIn，需要已有 GitHub | 免费 | 只适合技术人群；非技术行业价值有限 |
| Pattern Guess + Hunter Verify | 低成本候选生成 | 不依赖 LinkedIn | Hunter 验证会消耗额度 | 对英文姓名和标准企业邮箱格式有用；不能盲发 |
| Public Website | 官网公开邮箱 | 不依赖 LinkedIn | 免费 | 多为公司通用邮箱，只做候选，不自动发信 |

## 价格和额度说明

价格会变，以下只用于采购评估：

- Prospeo：官网/帮助中心显示按用户给 credits，Free 100/月，Starter 2,000/user/月，Growth 5,000/user/月，Pro 15,000/user/月。Prospeo API 的 Enrich Person 文档说明，找到邮箱通常消耗 1 credit；找 mobile 成本更高；没结果不扣或少扣，需以后台账单为准。
- Hunter：官网显示 Free 50 credits/月，Starter $49/月 2,000 credits，Growth $149/月 10,000 credits，Scale $299/月 25,000 credits。Hunter 计费规则里，Email Finder 找到邮箱通常 1 credit，Email Verifier 验证 0.5 credit。
- People Data Labs：用于个人资料和社媒 enrichment，价格通常更偏数据平台/用量型，适合做画像和社媒补全，不建议作为第一邮箱来源。

## 成功率预期

这个环节的成功率不取决于“有没有 LinkedIn URL”一个字段，而取决于数据完整度：

| 输入质量 | 预期结果 |
|---|---|
| LinkedIn URL + 英文姓名 + 公司官网域名 + 明确职位 | 最高，Prospeo/Hunter 都有机会命中 |
| 姓名 + 公司官网域名，无 LinkedIn URL | 中等，主要靠 Prospeo/Hunter/邮箱格式 |
| 只有公司名和官网，没有负责人姓名 | 低，只能找员工或公司通用邮箱 |
| 中东/俄语/阿语网站/小型门店 | 偏低，需要官网、电话、WhatsApp、Instagram、Google/Brave 搜索辅助 |
| KOL/Instagram 博主 | B2B 邮箱 API 命中偏低，通常要抓 bio、Linktree、商务邮箱 |

从我们最近实跑情况看，中东、俄语、小型门店/酒店类线索，Prospeo/Hunter 命中率不会像欧美 SaaS/B2B 那么高。系统已经做了“中东加强模式”的思路：先补官网/电话/WhatsApp/社媒，再尝试找负责人和邮箱。

## 数据准确度规则

系统采用保守写入：

- `valid + personal_work`：写入主邮箱，可进入发送。
- `unverified + personal_work`：保留候选，建议人工确认或继续验证。
- `company_generic`：只做公司候选邮箱，不自动作为主邮箱。
- `risky / unknown`：保留但不建议直接发。
- 退信后要回写状态，后续不再继续触达。

## 推荐采购组合

当前性价比建议：

1. Prospeo 做主力找邮箱。
2. Hunter 做验证和补充查找。
3. Brave / Google CSE 做公开搜索，负责找 LinkedIn 主页、官网、社媒和公司线索。
4. PeopleDB / PDL 只用于高价值客户画像和社媒补全，不作为每日大批量邮箱主来源。

如果目标是 30 人上线，每人每天 100 个有效邮箱，免费额度不现实。比较务实的方式是：

- 先按销售真实线索质量做 1-2 周统计。
- 看每个 Provider 的 `调用数 / 候选数 / valid 数 / 退信率`。
- 再决定 Prospeo 和 Hunter 的套餐大小。

## 系统还可以继续优化的点

1. 增加“LinkedIn URL 专用富化”按钮：优先 Prospeo bulk enrich，再 Hunter verify。
2. 增加 Provider 命中率面板：按销售、地区、Provider 统计命中率和成本。
3. 对中东线索自动进入增强模式：官网 + WhatsApp + Instagram + 电话 + 公司通用邮箱一起找。
4. 对 KOL/Instagram 线索单独走 KOL 模式：bio/link-in-bio/商务邮箱，而不是 B2B 公司邮箱逻辑。
5. 把候选邮箱分层展示：可发送、待验证、公司邮箱、退信风险。

## 官方参考

- Prospeo Pricing: https://prospeo.io/pricing
- Prospeo Enrich Person API: https://prospeo.io/api-docs/enrich-person
- Prospeo Bulk Enrich Person API: https://prospeo.io/api-docs/bulk-enrich-person
- Hunter Pricing: https://hunter.io/pricing
- Hunter Email Finder API: https://hunter.io/api/email-finder
- Hunter Email Verifier API: https://hunter.io/api/email-verifier
- People Data Labs Person Enrichment API: https://docs.peopledatalabs.com/docs/person-enrichment-api

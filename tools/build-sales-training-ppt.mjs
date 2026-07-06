import fs from "node:fs/promises";
import path from "node:path";
import { Presentation, PresentationFile } from "@oai/artifact-tool";

const outputDir = path.resolve("outputs", "sales-enable");
const outputPath = path.join(outputDir, "自动化获客系统_销售培训.pptx");

const deck = Presentation.create({
  slideSize: { width: 1280, height: 720 },
});

const theme = {
  bg: "#F7FBFA",
  panel: "#FFFFFF",
  ink: "#0F172A",
  sub: "#475569",
  teal: "#0F766E",
  tealSoft: "#DFF7F4",
  gold: "#D97706",
  goldSoft: "#FEF3C7",
  line: "#D7E5E3",
  mint: "#ECFDF5",
  blueSoft: "#E8F1FF",
  lilacSoft: "#F3F0FF",
  roseSoft: "#FFF1F2",
};

const page = { left: 64, top: 54, width: 1152, height: 612 };

function addBackground(slide, tag = "") {
  slide.background.fill = theme.bg;
  slide.shapes.add({
    name: `top-band-${tag}`,
    geometry: "rect",
    position: { left: 0, top: 0, width: 1280, height: 12 },
    fill: theme.teal,
    line: { style: "solid", fill: "none", width: 0 },
  });
  slide.shapes.add({
    name: `accent-blob-${tag}`,
    geometry: "roundRect",
    position: { left: 950, top: 36, width: 210, height: 96 },
    fill: theme.tealSoft,
    line: { style: "solid", fill: "none", width: 0 },
    borderRadius: "rounded-full",
  });
}

function addTitle(slide, eyebrow, title, subtitle) {
  const eye = slide.shapes.add({
    geometry: "textbox",
    position: { left: page.left, top: page.top, width: 360, height: 28 },
    fill: "none",
    line: { style: "solid", fill: "none", width: 0 },
  });
  eye.text = eyebrow;
  eye.text.style = { fontSize: 13, bold: true, color: theme.teal, fontFace: "Microsoft YaHei" };

  const head = slide.shapes.add({
    geometry: "textbox",
    position: { left: page.left, top: page.top + 36, width: 760, height: 84 },
    fill: "none",
    line: { style: "solid", fill: "none", width: 0 },
  });
  head.text = title;
  head.text.style = { fontSize: 34, bold: true, color: theme.ink, fontFace: "Microsoft YaHei" };

  const sub = slide.shapes.add({
    geometry: "textbox",
    position: { left: page.left, top: page.top + 126, width: 880, height: 64 },
    fill: "none",
    line: { style: "solid", fill: "none", width: 0 },
  });
  sub.text = subtitle;
  sub.text.style = { fontSize: 18, color: theme.sub, fontFace: "Microsoft YaHei" };
}

function addCard(slide, { left, top, width, height, title, body, fill = theme.panel, accent = theme.teal }) {
  slide.shapes.add({
    geometry: "roundRect",
    position: { left, top, width, height },
    fill,
    line: { style: "solid", fill: theme.line, width: 1 },
    borderRadius: "rounded-2xl",
    shadow: "shadow-sm",
  });
  slide.shapes.add({
    geometry: "rect",
    position: { left: left + 18, top: top + 18, width: 6, height: 54 },
    fill: accent,
    line: { style: "solid", fill: "none", width: 0 },
  });
  const t = slide.shapes.add({
    geometry: "textbox",
    position: { left: left + 36, top: top + 18, width: width - 54, height: 30 },
    fill: "none",
    line: { style: "solid", fill: "none", width: 0 },
  });
  t.text = title;
  t.text.style = { fontSize: 20, bold: true, color: theme.ink, fontFace: "Microsoft YaHei" };

  const b = slide.shapes.add({
    geometry: "textbox",
    position: { left: left + 36, top: top + 56, width: width - 54, height: height - 72 },
    fill: "none",
    line: { style: "solid", fill: "none", width: 0 },
  });
  b.text = body;
  b.text.style = { fontSize: 16, color: theme.sub, fontFace: "Microsoft YaHei" };
}

function addStep(slide, { index, left, top, width, title, body, fill }) {
  slide.shapes.add({
    geometry: "roundRect",
    position: { left, top, width, height: 160 },
    fill,
    line: { style: "solid", fill: theme.line, width: 1 },
    borderRadius: "rounded-2xl",
  });
  const num = slide.shapes.add({
    geometry: "roundRect",
    position: { left: left + 20, top: top + 20, width: 46, height: 46 },
    fill: theme.teal,
    line: { style: "solid", fill: "none", width: 0 },
    borderRadius: "rounded-full",
  });
  num.text = String(index);
  num.text.style = {
    fontSize: 22,
    bold: true,
    color: "#FFFFFF",
    fontFace: "Microsoft YaHei",
    alignment: "center",
  };
  const t = slide.shapes.add({
    geometry: "textbox",
    position: { left: left + 80, top: top + 22, width: width - 96, height: 28 },
    fill: "none",
    line: { style: "solid", fill: "none", width: 0 },
  });
  t.text = title;
  t.text.style = { fontSize: 19, bold: true, color: theme.ink, fontFace: "Microsoft YaHei" };
  const b = slide.shapes.add({
    geometry: "textbox",
    position: { left: left + 22, top: top + 78, width: width - 40, height: 66 },
    fill: "none",
    line: { style: "solid", fill: "none", width: 0 },
  });
  b.text = body;
  b.text.style = { fontSize: 15, color: theme.sub, fontFace: "Microsoft YaHei" };
}

function addFooter(slide, text) {
  const box = slide.shapes.add({
    geometry: "textbox",
    position: { left: 920, top: 680, width: 290, height: 22 },
    fill: "none",
    line: { style: "solid", fill: "none", width: 0 },
  });
  box.text = text;
  box.text.style = { fontSize: 11, color: "#64748B", fontFace: "Microsoft YaHei", alignment: "right" };
}

{
  const slide = deck.slides.add();
  addBackground(slide, "cover");
  slide.shapes.add({
    geometry: "roundRect",
    position: { left: 64, top: 160, width: 540, height: 330 },
    fill: theme.panel,
    line: { style: "solid", fill: theme.line, width: 1 },
    borderRadius: "rounded-3xl",
    shadow: "shadow-sm",
  });
  const eye = slide.shapes.add({
    geometry: "textbox",
    position: { left: 104, top: 204, width: 260, height: 24 },
    fill: "none",
    line: { style: "solid", fill: "none", width: 0 },
  });
  eye.text = "OUTBOUND OPS TRAINING";
  eye.text.style = { fontSize: 14, bold: true, color: theme.teal, fontFace: "Microsoft YaHei" };
  const title = slide.shapes.add({
    geometry: "textbox",
    position: { left: 104, top: 240, width: 430, height: 140 },
    fill: "none",
    line: { style: "solid", fill: "none", width: 0 },
  });
  title.text = "自动化获客系统\n销售培训手册";
  title.text.style = { fontSize: 39, bold: true, color: theme.ink, fontFace: "Microsoft YaHei" };
  const body = slide.shapes.add({
    geometry: "textbox",
    position: { left: 104, top: 392, width: 420, height: 58 },
    fill: "none",
    line: { style: "solid", fill: "none", width: 0 },
  });
  body.text = "目标是让销售在 10 分钟内学会登录、获客、找邮箱、发邮件、跟进客户。";
  body.text.style = { fontSize: 18, color: theme.sub, fontFace: "Microsoft YaHei" };

  addCard(slide, {
    left: 670,
    top: 174,
    width: 470,
    height: 136,
    title: "系统网址",
    body: "https://global-autoleads.vertu.cn/\n每位销售使用自己的账号登录，页面只显示自己的客户。",
    fill: theme.tealSoft,
    accent: theme.teal,
  });
  addCard(slide, {
    left: 670,
    top: 334,
    width: 470,
    height: 156,
    title: "培训后的结果",
    body: "会做 4 件事：导入客户、富化邮箱、发送首封邮件、更新客户阶段。\n不会碰管理员配置，也不需要懂数据库或接口。",
    fill: theme.goldSoft,
    accent: theme.gold,
  });
  addFooter(slide, "Outbound Ops · 销售启用培训");
}

{
  const slide = deck.slides.add();
  addBackground(slide, "overview");
  addTitle(slide, "01 / 这套系统是做什么的", "销售每天只做 4 件事，系统负责把动作串起来", "从线索进入，到邮箱发现、邮件触达、反馈回流，再到 SABCD 客户推进。");
  addCard(slide, {
    left: 64, top: 250, width: 255, height: 170,
    title: "找客户",
    body: "支持自动获客、公司种子导入、LinkedIn 公网搜索、CSV 导入和手动新增。",
    fill: theme.panel, accent: theme.teal,
  });
  addCard(slide, {
    left: 343, top: 250, width: 255, height: 170,
    title: "找邮箱",
    body: "系统调用邮箱 Provider 富化客户，拿到 valid 邮箱后才能进入发信。",
    fill: theme.mint, accent: "#16A34A",
  });
  addCard(slide, {
    left: 622, top: 250, width: 255, height: 170,
    title: "发邮件",
    body: "可用 AI 生成个性化首封邮件，也可以手动改内容后再发送。",
    fill: theme.blueSoft, accent: "#2563EB",
  });
  addCard(slide, {
    left: 901, top: 250, width: 255, height: 170,
    title: "跟进客户",
    body: "打开、回复、退信、推进到 B/A/S，都会回到客户生命周期里继续跟进。",
    fill: theme.lilacSoft, accent: "#7C3AED",
  });
  addFooter(slide, "核心理解：销售点动作，系统记全流程");
}

{
  const slide = deck.slides.add();
  addBackground(slide, "daily");
  addTitle(slide, "02 / 每日使用流程", "正常销售每天照着这 5 步走就够了", "先看待办，再找客户，再找邮箱，再发信，最后更新客户阶段。");
  addStep(slide, {
    index: 1, left: 64, top: 236, width: 208,
    title: "先看待办",
    body: "优先处理“已打开未回复”“已回复”“退信需处理”。",
    fill: "#FFF7E8",
  });
  addStep(slide, {
    index: 2, left: 286, top: 236, width: 208,
    title: "补充线索",
    body: "用自动获客或导入表格，把今天要触达的新客户放进系统。",
    fill: "#ECFDF5",
  });
  addStep(slide, {
    index: 3, left: 508, top: 236, width: 208,
    title: "富化邮箱",
    body: "只对重点客户或批量客户做邮箱富化，先拿到可发送邮箱。",
    fill: "#EFF6FF",
  });
  addStep(slide, {
    index: 4, left: 730, top: 236, width: 208,
    title: "生成并发送",
    body: "看一眼 AI 生成的邮件，不合适就改，再发送给当前客户。",
    fill: "#F5F3FF",
  });
  addStep(slide, {
    index: 5, left: 952, top: 236, width: 208,
    title: "更新阶段",
    body: "客户有反馈就推进 SABCD 和生命周期，不要只发不记。",
    fill: "#FFF1F2",
  });
  addFooter(slide, "执行标准：每天至少先清待办，再做新触达");
}

{
  const slide = deck.slides.add();
  addBackground(slide, "login");
  addTitle(slide, "03 / 第一次登录怎么做", "首次登录必须改密码，之后只使用自己的销售账号", "管理员账号不给销售使用。销售页面只会看到自己的客户和自己的额度。");
  addCard(slide, {
    left: 64, top: 236, width: 340, height: 220,
    title: "登录信息",
    body: "网址：global-autoleads.vertu.cn\n输入管理员发的“登录账号 + 临时密码”。\n账号区分大小写时，以清单为准。",
    fill: theme.panel, accent: theme.teal,
  });
  addCard(slide, {
    left: 430, top: 236, width: 340, height: 220,
    title: "首次登录后",
    body: "系统会要求修改密码。\n新密码建议至少 12 位，自己保存，不要再用临时密码。",
    fill: theme.mint, accent: "#16A34A",
  });
  addCard(slide, {
    left: 796, top: 236, width: 360, height: 220,
    title: "登录后你会看到",
    body: "客户生命周期\n邮件中心\n自己的客户列表\n不会看到全员客户池、账号管理、配额总表等管理员内容。",
    fill: theme.blueSoft, accent: "#2563EB",
  });
  addFooter(slide, "登录失败先检查账号大小写，再找管理员重置");
}

{
  const slide = deck.slides.add();
  addBackground(slide, "leads");
  addTitle(slide, "04 / 怎么把客户放进系统", "系统支持 5 种进客户方式，销售只需要挑最适合自己的那一种", "客户进系统后先进入线索阶段，再去找邮箱和做首封触达。");
  addCard(slide, {
    left: 64, top: 236, width: 260, height: 180,
    title: "自动获客",
    body: "输入公司官网、职位、行业、地区和数量。\n适合每天快速补新客户。",
    fill: theme.panel, accent: theme.teal,
  });
  addCard(slide, {
    left: 344, top: 236, width: 260, height: 180,
    title: "公司种子导入",
    body: "给公司名单、官网、地区、职位偏好。\n系统按公司继续找公开联系人。",
    fill: theme.goldSoft, accent: theme.gold,
  });
  addCard(slide, {
    left: 624, top: 236, width: 260, height: 180,
    title: "CSV 导入",
    body: "已有客户表就直接导入。\n导入后可批量富化邮箱、批量入队。",
    fill: theme.blueSoft, accent: "#2563EB",
  });
  addCard(slide, {
    left: 904, top: 236, width: 260, height: 180,
    title: "手动新增",
    body: "重点客户或老板指定客户，直接手动建一条。\n适合高价值客户单独跟进。",
    fill: theme.lilacSoft, accent: "#7C3AED",
  });
  addCard(slide, {
    left: 220, top: 448, width: 788, height: 114,
    title: "导入后不要立刻乱发",
    body: "先看客户资料是否完整，再做邮箱富化。没有 valid 邮箱的客户，先停留在线索阶段，不要直接推进到发信。",
    fill: "#FFF7E8",
    accent: theme.gold,
  });
  addFooter(slide, "先把线索整理干净，再做下一步");
}

{
  const slide = deck.slides.add();
  addBackground(slide, "email");
  addTitle(slide, "05 / 找邮箱和发首封邮件", "有邮箱才能发；发之前看一眼邮件内容", "系统可以 AI 个性化生成，但高价值客户一定要人工看一遍。");
  addCard(slide, {
    left: 64, top: 236, width: 340, height: 250,
    title: "找邮箱",
    body: "点“邮箱”或批量“富化邮箱”。\n只对显示 valid 或明确可用的邮箱发信。\n官网公共邮箱、无效邮箱不要乱发。",
    fill: theme.mint, accent: "#16A34A",
  });
  addCard(slide, {
    left: 430, top: 236, width: 340, height: 250,
    title: "写邮件",
    body: "点“详情”进入客户页。\n可以直接用 AI 生成，也可以自己改主题和正文。\n重点是内容要像写给这个客户，不要像群发模板。",
    fill: theme.blueSoft, accent: "#2563EB",
  });
  addCard(slide, {
    left: 796, top: 236, width: 360, height: 250,
    title: "发送规则",
    body: "单个客户可直接发送。\n批量客户先入队再发。\n不要超额度，不要对退信客户重复发送，不要对明确拒绝客户继续骚扰。",
    fill: theme.roseSoft, accent: "#DC2626",
  });
  addFooter(slide, "发送不是终点，后续跟进才决定成交");
}

{
  const slide = deck.slides.add();
  addBackground(slide, "followup");
  addTitle(slide, "06 / 收到反馈后怎么推进", "客户是否打开、回复、推进合作，决定你在系统里怎么更新阶段", "SABCD 是销售动作的主线，生命周期是系统记录的过程线。");
  addStep(slide, {
    index: "D", left: 64, top: 236, width: 210,
    title: "未接触",
    body: "刚导入或刚找到的客户，准备找邮箱和首封邮件。",
    fill: theme.panel,
  });
  addStep(slide, {
    index: "C", left: 288, top: 236, width: 210,
    title: "已触达",
    body: "已经发送邮件，等待打开、回复或二次触达。",
    fill: theme.blueSoft,
  });
  addStep(slide, {
    index: "B", left: 512, top: 236, width: 210,
    title: "多轮沟通",
    body: "客户已回复或进入实质沟通，需要持续推进。",
    fill: theme.lilacSoft,
  });
  addStep(slide, {
    index: "A", left: 736, top: 236, width: 210,
    title: "商业计划 / 试订单",
    body: "开始谈计划、试订单、协议、到店等关键节点。",
    fill: theme.goldSoft,
  });
  addStep(slide, {
    index: "S", left: 960, top: 236, width: 210,
    title: "签约建店",
    body: "已经签约或进入持续维护阶段，后续重点在经营维护。",
    fill: theme.mint,
  });
  addCard(slide, {
    left: 170, top: 446, width: 940, height: 110,
    title: "实操原则",
    body: "打开未回复：继续跟进。 已回复：马上接手推进。 退信：停止发这个邮箱。 沟通有实质进展：及时把 SABCD 和阶段记录改掉。",
    fill: theme.panel,
    accent: theme.teal,
  });
  addFooter(slide, "客户没更新阶段，团队就看不到真实进展");
}

{
  const slide = deck.slides.add();
  addBackground(slide, "rules");
  addTitle(slide, "07 / 销售最容易犯的错", "系统能帮你做动作，但不能替你做判断", "下面这几件事一旦做错，最容易造成低回复、退信和客户混乱。");
  addCard(slide, {
    left: 64, top: 236, width: 340, height: 240,
    title: "错法 1：只会群发",
    body: "不看客户资料，不看职位，直接批量发。\n结果通常是回复低、退信高、老板也看不出价值。",
    fill: theme.roseSoft,
    accent: "#DC2626",
  });
  addCard(slide, {
    left: 430, top: 236, width: 340, height: 240,
    title: "错法 2：发了不跟",
    body: "客户打开了、回了、退信了，却没人改阶段和记录。\n这样系统再完整也没意义。",
    fill: theme.goldSoft,
    accent: theme.gold,
  });
  addCard(slide, {
    left: 796, top: 236, width: 360, height: 240,
    title: "对的做法",
    body: "每天先清待办，再补新客户；重点客户看一眼邮件内容；每次沟通后都要更新阶段和记录。",
    fill: theme.mint,
    accent: "#16A34A",
  });
  addFooter(slide, "标准动作比花哨动作更重要");
}

{
  const slide = deck.slides.add();
  addBackground(slide, "close");
  addTitle(slide, "08 / 培训后你要会的事情", "只要会这 6 件事，你就能独立开始用系统", "把系统用顺，重点不是多会点按钮，而是每天把同一套动作做扎实。");
  addCard(slide, {
    left: 64, top: 230, width: 510, height: 260,
    title: "你必须会",
    body: "1. 登录并修改密码\n2. 看今日待办\n3. 导入或获取新客户\n4. 富化邮箱\n5. 生成并发送首封邮件\n6. 根据反馈更新 SABCD 和阶段记录",
    fill: theme.panel,
    accent: theme.teal,
  });
  addCard(slide, {
    left: 614, top: 230, width: 542, height: 260,
    title: "遇到问题找谁",
    body: "账号登录不上：找管理员重置密码\n页面没有数据：先确认是不是自己的客户\n邮件退信：不要继续发这个邮箱\n不会判断客户阶段：先记沟通内容，再找主管确认推进到哪一步",
    fill: theme.blueSoft,
    accent: "#2563EB",
  });
  addFooter(slide, "目标：当天培训，当天可上手");
}

async function writeBlob(filePath, blob) {
  await fs.writeFile(filePath, new Uint8Array(await blob.arrayBuffer()));
}

await fs.mkdir(outputDir, { recursive: true });

for (const [index, slide] of deck.slides.items.entries()) {
  const png = await deck.export({ slide, format: "png", scale: 1 });
  await writeBlob(path.join(outputDir, `培训预览_${String(index + 1).padStart(2, "0")}.png`), png);
}

const montage = await deck.export({ format: "webp", montage: true, scale: 1 });
await writeBlob(path.join(outputDir, "培训总览.webp"), montage);

const pptx = await PresentationFile.exportPptx(deck);
await pptx.save(outputPath);

console.log(outputPath);

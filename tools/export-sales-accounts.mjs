import fs from "node:fs/promises";
import path from "node:path";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const outputDir = path.resolve("outputs", "sales-enable");
const outputPath = path.join(outputDir, "销售账号开通清单.xlsx");

const accounts = [
  { name: "Bianca", username: "Bianca", password: "CsUe21An5w7MO2uXEk" },
  { name: "Terry", username: "Terry", password: "pm27P9SxcLMzZg1TYN" },
  { name: "Henry", username: "Henry", password: "8fCH57GpCmpJJ04cJQ" },
  { name: "Gao", username: "Gao", password: "jcblO4cIiketYtKIDu" },
  { name: "vivi", username: "Vivi", password: "ysE8r8xwdRKjzRksZx" },
  { name: "Ivan", username: "Ivan", password: "ExuiSWGAxG4HQEf2CA" },
  { name: "April", username: "April", password: "oAD0G8y3jCBInZpc1G" },
  { name: "Lina", username: "Lina", password: "LAOw8va1ak3mqCvRyM" },
  { name: "Viki", username: "Viki", password: "AzlMDiBjQsPi8DFIEA" },
  { name: "Haiwen", username: "Haiwen", password: "7xth98m9kd3Ca5OhtH" },
  { name: "Chen", username: "Chen", password: "JzuVVSKTbqlPpCnCrz" },
];

const workbook = Workbook.create();
const sheet = workbook.worksheets.add("账号清单");
const guide = workbook.worksheets.add("发放说明");

sheet.showGridLines = false;
guide.showGridLines = false;

sheet.getRange("A1:H1").merge();
sheet.getRange("A1:H1").values = [["自动化获客系统销售账号开通清单"]];
sheet.getRange("A1:H1").format = {
  fill: "#0F766E",
  font: { bold: true, color: "#FFFFFF", size: 18, name: "Microsoft YaHei" },
  horizontalAlignment: "center",
  verticalAlignment: "center",
};
sheet.getRange("A1:H1").format.rowHeight = 30;

sheet.getRange("A2:H2").merge();
sheet.getRange("A2:H2").values = [[
  "临时密码仅用于首次登录，登录后必须立即修改；页面地址：https://global-autoleads.vertu.cn/",
]];
sheet.getRange("A2:H2").format = {
  fill: "#ECFDF5",
  font: { color: "#115E59", name: "Microsoft YaHei", size: 11 },
  horizontalAlignment: "left",
  verticalAlignment: "center",
  wrapText: true,
};
sheet.getRange("A2:H2").format.rowHeight = 34;

sheet.getRange("A4:H4").values = [[
  "姓名",
  "登录账号",
  "临时密码",
  "角色",
  "状态",
  "日获客额度",
  "日发信额度",
  "首次登录要求",
]];
sheet.getRange("A4:H4").format = {
  fill: "#CCFBF1",
  font: { bold: true, color: "#134E4A", name: "Microsoft YaHei", size: 11 },
  horizontalAlignment: "center",
  verticalAlignment: "center",
  borders: { preset: "all", style: "thin", color: "#99F6E4" },
};

const rows = accounts.map((item) => [
  item.name,
  item.username,
  item.password,
  "sales",
  "启用",
  100,
  80,
  "首次登录必须改密码",
]);
sheet.getRange(`A5:H${rows.length + 4}`).values = rows;
sheet.getRange(`A5:H${rows.length + 4}`).format = {
  font: { name: "Microsoft YaHei", size: 10, color: "#1F2937" },
  verticalAlignment: "center",
  borders: { preset: "all", style: "thin", color: "#D1FAE5" },
};
sheet.getRange(`F5:G${rows.length + 4}`).format.numberFormat = "0";
sheet.getRange(`E5:E${rows.length + 4}`).format = {
  fill: "#ECFDF5",
  font: { bold: true, color: "#065F46", name: "Microsoft YaHei", size: 10 },
  horizontalAlignment: "center",
  verticalAlignment: "center",
  borders: { preset: "all", style: "thin", color: "#A7F3D0" },
};
sheet.getRange(`H5:H${rows.length + 4}`).format.wrapText = true;

sheet.getRange("A16:H18").values = [
  ["发放提醒", "", "", "", "", "", "", ""],
  ["1", "不要把管理员账号发给销售。", "", "", "", "", "", ""],
  ["2", "发完账号后，让销售当天登录并修改密码。", "", "", "", "", "", ""],
];
sheet.getRange("A16:H16").merge();
sheet.getRange("A16:H16").format = {
  fill: "#FEF3C7",
  font: { bold: true, color: "#92400E", name: "Microsoft YaHei", size: 11 },
};
sheet.getRange("A17:H18").format = {
  fill: "#FFFBEB",
  font: { color: "#78350F", name: "Microsoft YaHei", size: 10 },
  borders: { preset: "all", style: "thin", color: "#FDE68A" },
};
sheet.getRange("B17:H17").merge();
sheet.getRange("B18:H18").merge();

sheet.freezePanes.freezeRows(4);
sheet.getRange("A:H").format.columnWidth = 20;
sheet.getRange("C:C").format.columnWidth = 26;
sheet.getRange("H:H").format.columnWidth = 24;

guide.getRange("A1:F1").merge();
guide.getRange("A1:F1").values = [["账号发放说明"]];
guide.getRange("A1:F1").format = {
  fill: "#0F172A",
  font: { bold: true, color: "#FFFFFF", size: 18, name: "Microsoft YaHei" },
  horizontalAlignment: "center",
  verticalAlignment: "center",
};

guide.getRange("A3:F8").values = [
  ["步骤", "操作说明", "", "", "", ""],
  ["1", "把“账号清单”页中的姓名、账号、临时密码发给对应销售。", "", "", "", ""],
  ["2", "让销售打开 https://global-autoleads.vertu.cn/ 登录。", "", "", "", ""],
  ["3", "首次登录后必须修改密码，否则后续无法安全上线。", "", "", "", ""],
  ["4", "销售只看自己的客户；管理员才看全局客户和配额。", "", "", "", ""],
  ["5", "如果登录失败，先核对大小写，再找管理员重置密码。", "", "", "", ""],
];
guide.getRange("A3:F3").format = {
  fill: "#CFFAFE",
  font: { bold: true, color: "#164E63", name: "Microsoft YaHei", size: 11 },
  borders: { preset: "all", style: "thin", color: "#A5F3FC" },
};
guide.getRange("A4:F8").format = {
  font: { name: "Microsoft YaHei", size: 10, color: "#1F2937" },
  wrapText: true,
  verticalAlignment: "center",
  borders: { preset: "all", style: "thin", color: "#E5E7EB" },
};
guide.getRange("B4:F8").merge(true);
guide.getRange("A:F").format.columnWidth = 24;
guide.getRange("B:F").format.columnWidth = 28;

await fs.mkdir(outputDir, { recursive: true });
const xlsx = await SpreadsheetFile.exportXlsx(workbook);
await xlsx.save(outputPath);

console.log(outputPath);

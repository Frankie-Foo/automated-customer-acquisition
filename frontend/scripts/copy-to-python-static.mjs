import { cp, rm } from "node:fs/promises";
import { resolve } from "node:path";

const repoRoot = resolve(import.meta.dirname, "../..");
const distDir = resolve(repoRoot, "frontend/dist");
const staticDir = resolve(repoRoot, "src/sales_automation/web_static");

await rm(staticDir, { recursive: true, force: true });
await cp(distDir, staticDir, { recursive: true });

console.log(`Copied React build to ${staticDir}`);

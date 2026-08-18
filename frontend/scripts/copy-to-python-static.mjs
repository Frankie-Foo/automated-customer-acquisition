import { cp, readFile, rm, writeFile } from "node:fs/promises";
import { resolve } from "node:path";

const repoRoot = resolve(import.meta.dirname, "../..");
const distDir = resolve(repoRoot, "frontend/dist");
const staticDir = resolve(repoRoot, "src/sales_automation/web_static");
const emailImagesDir = resolve(repoRoot, "assets/email_images");

await rm(staticDir, { recursive: true, force: true });
await cp(distDir, staticDir, { recursive: true });
await cp(emailImagesDir, resolve(staticDir, "assets/email_images"), { recursive: true });
const indexPath = resolve(staticDir, "index.html");
await writeFile(indexPath, (await readFile(indexPath, "utf8")).replace(/\r\n/g, "\n"));

console.log(`Copied React build to ${staticDir}`);

import fs from "node:fs/promises";
import path from "node:path";
import { chromium } from "playwright";

const ROOT = process.cwd();
const CAPTURE_DIR = path.join(ROOT, "captures");
const CHROME_PATH = "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe";

async function readText(filePath) {
  return fs.readFile(filePath, "utf8");
}

async function loadEnv() {
  const envPath = path.join(ROOT, ".env");
  const content = await readText(envPath);
  const lines = content.split(/\r?\n/);
  const env = {};

  for (const line of lines) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#")) continue;
    const idx = trimmed.indexOf("=");
    if (idx === -1) continue;
    const key = trimmed.slice(0, idx).trim();
    const value = trimmed.slice(idx + 1).trim();
    env[key] = value === "" ? undefined : value;
  }

  return env;
}

function buildCookies(env) {
  const cookies = [];

  for (const [name, value] of [
    ["__Secure-1PSID", env.GEMINI_SECURE_1PSID],
    ["__Secure-1PSIDTS", env.GEMINI_SECURE_1PSIDTS],
  ]) {
    if (!value) continue;
    cookies.push({
      name,
      value,
      domain: ".google.com",
      path: "/",
      httpOnly: true,
      secure: true,
      sameSite: "None",
    });
  }

  return cookies;
}

async function main() {
  await fs.mkdir(CAPTURE_DIR, { recursive: true });

  const env = await loadEnv();
  const cookies = buildCookies(env);
  if (cookies.length === 0) {
    throw new Error("Missing GEMINI_SECURE_1PSID / GEMINI_SECURE_1PSIDTS in .env");
  }

  const browser = await chromium.launch({
    headless: true,
    executablePath: CHROME_PATH,
  });

  const context = await browser.newContext({
    viewport: { width: 1600, height: 1400 },
    locale: "ru-RU",
  });

  await context.addCookies(cookies);
  const page = await context.newPage();
  page.setDefaultTimeout(30000);
  page.on("console", (message) => {
    if (message.type() === "error") {
      console.log(`[page-console] ${message.type()}: ${message.text()}`);
    }
  });
  page.on("pageerror", (error) => {
    console.log(`[pageerror] ${error.message}`);
  });

  await page.goto("https://gemini.google.com/app", {
    waitUntil: "domcontentloaded",
    timeout: 120000,
  });
  await page.waitForTimeout(15000);

  await page.screenshot({
    path: path.join(CAPTURE_DIR, "gemini_ui_inspect.png"),
    fullPage: true,
  });
  await fs.writeFile(
    path.join(CAPTURE_DIR, "gemini_ui_inspect.html"),
    await page.content(),
    "utf8",
  );

  const elements = await page.evaluate(() => {
    const candidates = Array.from(
      document.querySelectorAll(
        'button, [role="button"], textarea, input, [contenteditable="true"]',
      ),
    );

    return candidates.map((node, index) => {
      const text = (node.textContent || "").trim().replace(/\s+/g, " ");
      const ariaLabel = node.getAttribute("aria-label");
      const placeholder = node.getAttribute("placeholder");
      const role = node.getAttribute("role");
      const tag = node.tagName.toLowerCase();
      const testId = node.getAttribute("data-test-id");
      const className =
        typeof node.className === "string" ? node.className.slice(0, 160) : "";

      return {
        index,
        tag,
        role,
        text,
        ariaLabel,
        placeholder,
        testId,
        className,
      };
    });
  });

  await fs.writeFile(
    path.join(CAPTURE_DIR, "gemini_ui_elements.json"),
    JSON.stringify(elements, null, 2),
    "utf8",
  );

  console.log(`Saved ${elements.length} interactive elements to captures\\gemini_ui_elements.json`);
  await browser.close();
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});

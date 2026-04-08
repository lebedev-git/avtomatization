import fs from "node:fs/promises";
import path from "node:path";
import { chromium } from "playwright";

function normalizeText(value) {
  return String(value || "").toLowerCase().replace(/\s+/g, " ").trim();
}

async function readStdin() {
  const chunks = [];
  for await (const chunk of process.stdin) {
    chunks.push(chunk);
  }
  return Buffer.concat(chunks).toString("utf8");
}

function buildCookies(rawCookies) {
  return Object.entries(rawCookies || {}).map(([name, value]) => ({
    name,
    value: String(value ?? ""),
    domain: ".google.com",
    path: "/",
    secure: name.startsWith("__Secure-"),
  }));
}

async function safeInnerText(locator) {
  try {
    return await locator.innerText();
  } catch {
    return "";
  }
}

async function inspectInfographicState(page) {
  const studioText = normalizeText(await safeInnerText(page.locator("artifact-library").first()));
  const chatText = normalizeText(await safeInnerText(page.locator("chat-panel").first()));
  const emptyStateCount = await page.locator("chat-panel .chat-panel-empty-state").count().catch(() => 0);
  const largeVisualCount = await page.evaluate(() => {
    return Array.from(document.querySelectorAll("chat-panel img, chat-panel canvas, chat-panel svg"))
      .filter((element) => {
        const rect = element.getBoundingClientRect();
        return rect.width >= 320 && rect.height >= 320;
      })
      .length;
  }).catch(() => 0);

  const generatingMarkers = [
    "генерируется",
    "начинается генерация объекта",
    "starting generation",
    "in progress",
  ];
  const isGenerating = generatingMarkers.some(
    (marker) => studioText.includes(marker) || chatText.includes(marker),
  );
  const isReady = !isGenerating && (largeVisualCount > 0 || emptyStateCount === 0);

  return {
    isReady,
    isGenerating,
    studioText,
    chatText,
    emptyStateCount,
    largeVisualCount,
  };
}

async function waitForInfographic(page, timeoutSec) {
  const deadline = Date.now() + Math.max(30, timeoutSec) * 1000;
  let lastState = null;

  while (Date.now() < deadline) {
    lastState = await inspectInfographicState(page);
    if (lastState.isReady) {
      return lastState;
    }

    await page.waitForTimeout(5000);
    await page.reload({ waitUntil: "domcontentloaded", timeout: 120000 }).catch(() => {});
    await page.waitForTimeout(4000);
  }

  throw new Error(
    `NotebookLM did not finish the infographic in time. ` +
      `Studio: ${lastState?.studioText || "n/a"}; Chat: ${lastState?.chatText || "n/a"}`,
  );
}

async function captureInfographic(page, outputPath) {
  await page.addStyleTag({
    content: `
      .boqOnegoogleliteOgbOneGoogleBar,
      notebook-header,
      section.source-panel,
      studio-panel,
      footer,
      omnibar,
      follow-up,
      .chat-panel-empty-state-action-bar,
      .customize-button {
        display: none !important;
      }

      body,
      .app-body,
      notebook,
      .panel-container,
      chat-panel,
      .chat-panel-content {
        background: #ffffff !important;
      }
    `,
  }).catch(() => {});

  const libraryItem = page.locator("artifact-library-item").first();
  if (await libraryItem.count().catch(() => 0)) {
    await libraryItem.click({ timeout: 5000 }).catch(() => {});
    await page.waitForTimeout(1200);
  }

  const target = page.locator("chat-panel").first();
  await target.waitFor({ state: "visible", timeout: 30000 });
  await fs.mkdir(path.dirname(outputPath), { recursive: true });
  await target.screenshot({
    path: outputPath,
    animations: "disabled",
  });
}

async function main() {
  const rawInput = await readStdin();
  const payload = JSON.parse(rawInput || "{}");

  const notebookUrl = String(payload.notebookUrl || "").trim();
  const cookiesPath = String(payload.cookiesPath || "").trim();
  const outputPath = String(payload.outputPath || "").trim();
  const chromeExecutablePath = String(payload.chromeExecutablePath || "").trim() || undefined;
  const timeoutSec = Number(payload.timeoutSec || 420);

  if (!notebookUrl) {
    throw new Error("Missing notebookUrl.");
  }
  if (!cookiesPath) {
    throw new Error("Missing cookiesPath.");
  }
  if (!outputPath) {
    throw new Error("Missing outputPath.");
  }

  const rawCookies = JSON.parse(await fs.readFile(cookiesPath, "utf8"));
  const browser = await chromium.launch({
    headless: true,
    executablePath: chromeExecutablePath,
  });

  try {
    const context = await browser.newContext({
      locale: "ru-RU",
      viewport: { width: 1600, height: 1200 },
      colorScheme: "light",
    });
    await context.addCookies(buildCookies(rawCookies));

    const page = await context.newPage();
    await page.goto(notebookUrl, {
      waitUntil: "domcontentloaded",
      timeout: 120000,
    });
    await page.waitForTimeout(5000);

    const state = await waitForInfographic(page, timeoutSec);
    await captureInfographic(page, outputPath);

    process.stdout.write(
      JSON.stringify(
        {
          ok: true,
          outputPath,
          notebookUrl,
          state,
        },
        null,
        2,
      ),
    );
  } finally {
    await browser.close();
  }
}

main().catch((error) => {
  process.stderr.write(String(error?.stack || error?.message || error));
  process.exit(1);
});

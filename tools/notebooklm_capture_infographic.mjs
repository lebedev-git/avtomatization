import fs from "node:fs/promises";
import path from "node:path";
import os from "node:os";
import { chromium } from "playwright";

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

async function createContext({
  profileDir,
  cookiesPath,
  chromeExecutablePath,
}) {
  if (profileDir) {
    return chromium.launchPersistentContext(profileDir, {
      headless: true,
      executablePath: chromeExecutablePath,
      locale: "ru-RU",
      viewport: { width: 1600, height: 1200 },
      colorScheme: "light",
    });
  }

  if (!cookiesPath) {
    throw new Error("Missing cookiesPath.");
  }

  const rawCookies = JSON.parse(await fs.readFile(cookiesPath, "utf8"));
  const browser = await chromium.launch({
    headless: true,
    executablePath: chromeExecutablePath,
  });
  const context = await browser.newContext({
    locale: "ru-RU",
    viewport: { width: 1600, height: 1200 },
    colorScheme: "light",
  });
  await context.addCookies(buildCookies(rawCookies));
  context.__browser = browser;
  return context;
}

async function closeContext(context) {
  const browser = context.__browser ?? null;
  await context.close();
  if (browser) {
    await browser.close();
  }
}

async function openInfographicViewer(page, timeoutSec) {
  const deadline = Date.now() + Math.max(30, timeoutSec) * 1000;
  let lastStudioText = "";

  while (Date.now() < deadline) {
    const button = page.locator(".artifact-library-container .artifact-stretched-button").first();
    if (await button.count().catch(() => 0)) {
      await button.click({ timeout: 10000 }).catch(() => {});
      await page.waitForTimeout(2000);
      return;
    }

    lastStudioText = await page.locator(".artifact-library-container").first().innerText().catch(() => "");
    await page.waitForTimeout(3000);
    await page.reload({ waitUntil: "domcontentloaded", timeout: 120000 }).catch(() => {});
    await page.waitForTimeout(4000);
  }

  throw new Error(
    `NotebookLM did not show the infographic artifact button in time. Studio: ${lastStudioText || "n/a"}`,
  );
}

async function waitForRenderedInfographic(page, timeoutSec) {
  const selector = "artifact-viewer img, infographic-viewer img, image-viewer img";
  await page.waitForSelector(selector, {
    state: "visible",
    timeout: Math.max(30000, timeoutSec * 1000),
  });
  await page.waitForFunction(
    (imageSelector) => {
      const image = document.querySelector(imageSelector);
      return !!image && image.naturalWidth > 0 && image.naturalHeight > 0;
    },
    selector,
    { timeout: Math.max(30000, timeoutSec * 1000) },
  );
  await page.waitForTimeout(3000);

  const image = page.locator(selector).first();
  const meta = await image.evaluate((element) => ({
    currentSrc: element.currentSrc || element.getAttribute("src") || null,
    naturalWidth: element.naturalWidth,
    naturalHeight: element.naturalHeight,
    clientWidth: element.clientWidth,
    clientHeight: element.clientHeight,
    alt: element.getAttribute("alt") || null,
  }));
  return { selector, meta };
}

async function captureInfographic(page, outputPath) {
  await page.addStyleTag({
    content: `
      .boqOnegoogleliteOgbOneGoogleBar,
      notebook-header,
      section.source-panel,
      .artifact-viewer-header,
      .artifact-viewer-footer,
      .image-viewer-controls,
      .feedback-container,
      .studio-footer,
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
      .artifact-viewer-container,
      .artifact-content,
      .image-viewer-container {
        background: #ffffff !important;
      }
    `,
  }).catch(() => {});

  const target = page.locator(".image-viewer-container").first();
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
  const profileDir =
    String(payload.profileDir || "").trim() ||
    path.join(os.homedir(), ".nlm", "chrome-profile");

  if (!notebookUrl) {
    throw new Error("Missing notebookUrl.");
  }
  if (!outputPath) {
    throw new Error("Missing outputPath.");
  }

  const context = await createContext({
    profileDir,
    cookiesPath,
    chromeExecutablePath,
  });

  try {
    const page = context.pages()[0] || (await context.newPage());
    await page.goto(notebookUrl, {
      waitUntil: "domcontentloaded",
      timeout: 120000,
    });
    await page.waitForTimeout(5000);

    await openInfographicViewer(page, timeoutSec);
    const state = await waitForRenderedInfographic(page, timeoutSec);
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
    await closeContext(context);
  }
}

main().catch((error) => {
  process.stderr.write(String(error?.stack || error?.message || error));
  process.exit(1);
});

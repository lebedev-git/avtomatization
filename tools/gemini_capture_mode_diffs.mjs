import fs from "node:fs/promises";
import path from "node:path";
import { chromium } from "playwright";

const ROOT = process.cwd();
const CAPTURE_DIR = path.join(ROOT, "captures", "ui_reverse");
const CHROME_PATH = "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe";

const RU = {
  fast: "\u0411\u044b\u0441\u0442\u0440\u0430\u044f",
  thinking: "\u0414\u0443\u043c\u0430\u044e\u0449\u0430\u044f",
  pro: "Pro",
  createImage: "\u0421\u043e\u0437\u0434\u0430\u0442\u044c \u0438\u0437\u043e\u0431\u0440\u0430\u0436\u0435\u043d\u0438\u0435",
};

const SCENARIOS = [
  {
    name: "fast_text",
    modeLabel: RU.fast,
    prompt: "Reply with exactly FAST_MODE_OK and nothing else.",
  },
  {
    name: "thinking_text",
    modeLabel: RU.thinking,
    prompt: "Reply with exactly THINKING_MODE_OK and nothing else.",
  },
  {
    name: "pro_text",
    modeLabel: RU.pro,
    prompt: "Reply with exactly PRO_MODE_OK and nothing else.",
  },
  {
    name: "fast_image",
    modeLabel: RU.fast,
    enableImageTool: true,
    prompt:
      "Create exactly one original image of a glossy red cube on a seamless white studio background. Do not search the web. Do not provide examples. Return only the generated image.",
  },
  {
    name: "pro_image",
    modeLabel: RU.pro,
    enableImageTool: true,
    prompt:
      "Create exactly one original image of a glossy red cube on a seamless white studio background. Do not search the web. Do not provide examples. Return only the generated image.",
  },
];

async function readText(filePath) {
  return fs.readFile(filePath, "utf8");
}

async function loadEnv() {
  const envPath = path.join(ROOT, ".env");
  const content = await readText(envPath);
  const env = {};

  for (const line of content.split(/\r?\n/)) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#")) continue;
    const idx = trimmed.indexOf("=");
    if (idx === -1) continue;
    env[trimmed.slice(0, idx).trim()] = trimmed.slice(idx + 1).trim();
  }

  return env;
}

function buildCookies(env) {
  return [
    ["__Secure-1PSID", env.GEMINI_SECURE_1PSID],
    ["__Secure-1PSIDTS", env.GEMINI_SECURE_1PSIDTS],
  ]
    .filter(([, value]) => Boolean(value))
    .map(([name, value]) => ({
      name,
      value,
      domain: ".google.com",
      path: "/",
      httpOnly: true,
      secure: true,
      sameSite: "None",
    }));
}

function sanitizeValue(value) {
  if (typeof value === "string") {
    if (value.length > 120) return `[redacted:${value.length}]`;
    return value;
  }

  if (Array.isArray(value)) {
    return value.map((item) => sanitizeValue(item));
  }

  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value).map(([key, nested]) => [key, sanitizeValue(nested)]),
    );
  }

  return value;
}

function tryParseJson(value) {
  if (typeof value !== "string") return value;
  const trimmed = value.trim();
  if (!trimmed) return value;
  if (!(trimmed.startsWith("[") || trimmed.startsWith("{") || trimmed.startsWith("\""))) {
    return value;
  }

  try {
    return JSON.parse(trimmed);
  } catch {
    return value;
  }
}

function deepParse(value, depth = 0) {
  if (depth > 4) return value;

  if (typeof value === "string") {
    const parsed = tryParseJson(value);
    if (parsed === value) return value;
    return deepParse(parsed, depth + 1);
  }

  if (Array.isArray(value)) {
    return value.map((item) => deepParse(item, depth + 1));
  }

  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value).map(([key, nested]) => [key, deepParse(nested, depth + 1)]),
    );
  }

  return value;
}

function parseFormPostData(postData) {
  if (!postData) return null;
  const params = new URLSearchParams(postData);
  const result = {};

  for (const [key, value] of params.entries()) {
    result[key] = deepParse(value);
  }

  return result;
}

function summarizeRequest(request) {
  const url = new URL(request.url);
  const query = Object.fromEntries(url.searchParams.entries());
  delete query["at"];
  delete query["f.sid"];
  delete query["_reqid"];

  return {
    url: `${url.origin}${url.pathname}`,
    method: request.method,
    resourceType: request.resourceType,
    query,
    postData: sanitizeValue(parseFormPostData(request.postData)),
  };
}

function classifyRequest(request) {
  if (request.url.includes("/StreamGenerate")) return "stream_generate";
  if (request.url.includes("/batchexecute")) {
    const rpcids = new URL(request.url).searchParams.get("rpcids");
    return `batchexecute:${rpcids ?? "unknown"}`;
  }

  if (request.url.includes("/g/collect")) {
    const url = new URL(request.url);
    return `analytics:${url.searchParams.get("en") ?? "unknown"}`;
  }

  return "other";
}

async function ensureReady(page) {
  await page.goto("https://gemini.google.com/app", {
    waitUntil: "domcontentloaded",
    timeout: 120000,
  });
  await page.waitForTimeout(12000);
  await page.locator('[data-test-id="bard-mode-menu-button"]').waitFor();
  await page
    .locator('textarea, [role="textbox"], [contenteditable="true"]')
    .first()
    .waitFor();
}

async function openModeMenu(page) {
  const button = page.locator('[data-test-id="bard-mode-menu-button"]');
  await button.click();
  await page.waitForTimeout(1500);
}

async function selectMode(page, modeLabel) {
  if (!modeLabel) return false;

  const currentLabel = ((await page.locator('[data-test-id="bard-mode-menu-button"]').textContent()) || "")
    .trim();
  if (currentLabel === modeLabel) return true;

  await openModeMenu(page);
  const option = page.getByText(modeLabel, { exact: true }).last();
  await option.waitFor({ state: "visible", timeout: 10000 });
  await option.click();
  await page.waitForTimeout(2000);

  const nextLabel = ((await page.locator('[data-test-id="bard-mode-menu-button"]').textContent()) || "")
    .trim();
  return nextLabel === modeLabel;
}

async function enableImageTool(page) {
  const chip = page.locator("button, [role='button']").filter({ hasText: RU.createImage }).first();
  await chip.waitFor({ state: "visible", timeout: 10000 });
  await chip.click();
  await page.waitForTimeout(1500);
}

function promptLocator(page) {
  return page.locator('textarea, [role="textbox"], [contenteditable="true"]').first();
}

async function collectUiState(page) {
  return page.evaluate(() => {
    const modeButton = document.querySelector('[data-test-id="bard-mode-menu-button"]');
    const buttons = Array.from(document.querySelectorAll("button, [role='button']"));
    const createImageButtons = buttons
      .map((node) => (node.textContent || "").trim().replace(/\s+/g, " "))
      .filter((text) => text.includes("\u0421\u043e\u0437\u0434\u0430\u0442\u044c"));

    const inputBarTexts = buttons
      .map((node) => (node.textContent || "").trim().replace(/\s+/g, " "))
      .filter(Boolean)
      .slice(0, 30);

    return {
      modeButtonText: modeButton?.textContent?.trim() ?? null,
      bodyTextIncludesThinking: document.body.innerText.includes("\u0414\u0443\u043c\u0430\u044e\u0449\u0430\u044f"),
      bodyTextIncludesCreateImage: document.body.innerText.includes("\u0421\u043e\u0437\u0434\u0430\u0442\u044c \u0438\u0437\u043e\u0431\u0440\u0430\u0436\u0435\u043d\u0438\u0435"),
      createImageButtons,
      sampleButtonTexts: inputBarTexts,
    };
  });
}

async function runScenario(browser, cookies, scenario) {
  const context = await browser.newContext({
    locale: "ru-RU",
    viewport: { width: 1600, height: 1200 },
  });
  await context.addCookies(cookies);

  const page = await context.newPage();
  page.setDefaultTimeout(30000);

  const requests = [];
  page.on("request", (req) => {
    if (!["xhr", "fetch"].includes(req.resourceType())) return;
    const url = req.url();
    if (!url.includes("gemini.google.com") && !url.includes("google-analytics.com")) return;
    if (req.method() !== "POST") return;

    requests.push({
      ts: Date.now(),
      kind: classifyRequest({
        url,
        method: req.method(),
        resourceType: req.resourceType(),
      }),
      url,
      method: req.method(),
      resourceType: req.resourceType(),
      postData: req.postData(),
    });
  });

  await ensureReady(page);
  const beforeState = await collectUiState(page);

  const modeSelected = await selectMode(page, scenario.modeLabel);
  const afterModeState = await collectUiState(page);

  if (scenario.enableImageTool) {
    await enableImageTool(page);
  }

  const beforeSubmitState = await collectUiState(page);
  await page.screenshot({
    path: path.join(CAPTURE_DIR, `${scenario.name}_before_submit.png`),
    fullPage: true,
  });

  const promptBox = promptLocator(page);
  await promptBox.click();
  await promptBox.fill(scenario.prompt);

  const streamRequestPromise = page.waitForRequest(
    (req) => req.method() === "POST" && req.url().includes("/StreamGenerate"),
    { timeout: 60000 },
  );

  await promptBox.press("Enter");
  const streamRequest = await streamRequestPromise;
  await page.waitForTimeout(scenario.enableImageTool ? 15000 : 8000);

  await page.screenshot({
    path: path.join(CAPTURE_DIR, `${scenario.name}_after_submit.png`),
    fullPage: true,
  });

  const summarizedRequests = requests.map((request) => ({
    kind: request.kind,
    ...summarizeRequest(request),
  }));
  const streamSummary = summarizeRequest({
    url: streamRequest.url(),
    method: streamRequest.method(),
    resourceType: streamRequest.resourceType(),
    postData: streamRequest.postData(),
  });

  const relevantRequests = summarizedRequests.filter((request) => {
    const kind = request.kind;
    if (kind === "stream_generate") return true;
    if (kind === "batchexecute:L5adhe") return true;
    if (kind === "analytics:current_conversation_mode") return true;
    return false;
  });

  const output = {
    scenario,
    modeSelected,
    beforeState,
    afterModeState,
    beforeSubmitState,
    requestCount: summarizedRequests.length,
    streamRequest: streamSummary,
    relevantRequests,
  };

  await fs.writeFile(
    path.join(CAPTURE_DIR, `${scenario.name}.json`),
    JSON.stringify(output, null, 2),
    "utf8",
  );

  await context.close();
  return output;
}

async function main() {
  await fs.mkdir(CAPTURE_DIR, { recursive: true });

  const env = await loadEnv();
  const cookies = buildCookies(env);
  if (cookies.length === 0) {
    throw new Error("Missing Gemini cookies in .env");
  }

  const requestedNames = new Set(process.argv.slice(2));
  const scenarios = requestedNames.size
    ? SCENARIOS.filter((scenario) => requestedNames.has(scenario.name))
    : SCENARIOS;
  if (scenarios.length === 0) {
    throw new Error(`No matching scenarios for args: ${process.argv.slice(2).join(", ")}`);
  }

  const browser = await chromium.launch({
    headless: true,
    executablePath: CHROME_PATH,
  });

  const results = [];
  try {
    for (const scenario of scenarios) {
      console.log(`Running ${scenario.name}...`);
      results.push(await runScenario(browser, cookies, scenario));
    }
  } finally {
    await browser.close();
  }

  await fs.writeFile(
    path.join(CAPTURE_DIR, "summary.json"),
    JSON.stringify(results, null, 2),
    "utf8",
  );

  console.log(`Saved ${results.length} scenario captures to ${CAPTURE_DIR}`);
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});

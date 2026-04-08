import fs from "node:fs/promises";
import path from "node:path";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { chromium } from "playwright";

const ROOT = process.cwd();
const GEMINI_URL = "https://gemini.google.com/app";
const RU = {
  fast: "\u0411\u044b\u0441\u0442\u0440\u0430\u044f",
  thinking: "\u0414\u0443\u043c\u0430\u044e\u0449\u0430\u044f",
  pro: "Pro",
  createImage: "\u0421\u043e\u0437\u0434\u0430\u0442\u044c \u0438\u0437\u043e\u0431\u0440\u0430\u0436\u0435\u043d\u0438\u0435",
  imageChip: "\u0421\u043e\u0437\u0434\u0430\u043d\u0438\u0435 \u0438\u0437\u043e\u0431\u0440\u0430\u0436\u0435\u043d\u0438\u0439",
  openUploadMenu: "\u041e\u0442\u043a\u0440\u044b\u0442\u044c \u043c\u0435\u043d\u044e \u0437\u0430\u0433\u0440\u0443\u0437\u043a\u0438 \u0444\u0430\u0439\u043b\u043e\u0432",
  uploadFiles: "\u0417\u0430\u0433\u0440\u0443\u0437\u0438\u0442\u044c \u0444\u0430\u0439\u043b\u044b",
  signIn: "\u0412\u043e\u0439\u0442\u0438",
  geminiChat: "\u0427\u0430\u0442 \u0441 Gemini",
  signedOut: "\u0412\u044b \u0432\u044b\u0448\u043b\u0438 \u0438\u0437 \u0430\u043a\u043a\u0430\u0443\u043d\u0442\u0430",
};

const MODE_LABELS = {
  fast: RU.fast,
  thinking: RU.thinking,
  pro: RU.pro,
};

const execFileAsync = promisify(execFile);

function normalizeText(value) {
  return String(value || "").trim().replace(/\s+/g, " ");
}

function normalizeBlockText(value) {
  return String(value || "")
    .replace(/\r/g, "")
    .split("\n")
    .map((line) => line.trim().replace(/[ \t]+/g, " "))
    .join("\n")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

function extractAssistantTextFromTurn(turnText, promptText = "") {
  const normalizedTurn = normalizeBlockText(turnText);
  if (!normalizedTurn) return null;

  let text = normalizedTurn;
  const answerMarkers = [
    "\nОтвет Gemini\n",
    "\nGemini response\n",
    "\nResponse from Gemini\n",
  ];
  for (const marker of answerMarkers) {
    const index = text.lastIndexOf(marker);
    if (index >= 0) {
      text = text.slice(index + marker.length).trim();
      break;
    }
  }

  const normalizedPrompt = normalizeBlockText(promptText);
  if (normalizedPrompt && text.startsWith(normalizedPrompt)) {
    text = text.slice(normalizedPrompt.length).trim();
  }

  const removableHeadings = new Set([
    "audio",
    "mp3",
    "ваш запрос",
    "your request",
    "анализ",
    "analysis",
    "ответ gemini",
    "gemini response",
    "response from gemini",
    "показать процесс размышления",
    "show thinking",
  ]);
  const lines = text
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
  while (lines.length && removableHeadings.has(lines[0].toLowerCase())) {
    lines.shift();
  }

  const candidate = normalizeBlockText(lines.join("\n"));
  if (!candidate) return null;
  if (normalizedPrompt && candidate === normalizedPrompt) return null;
  if (candidate.includes("Ваш запрос") && candidate.includes(normalizedPrompt)) return null;
  return candidate;
}

function enrichSnapshot(snapshot, promptText = "") {
  const assistantText = snapshot.assistantText
    || extractAssistantTextFromTurn(snapshot.lastTurnText, promptText);
  return {
    ...snapshot,
    assistantText: assistantText || null,
  };
}

function maybeCleanThoughtText(value) {
  const text = normalizeBlockText(value);
  if (!text) return null;
  if (text === "\u041f\u043e\u043a\u0430\u0437\u0430\u0442\u044c \u043f\u0440\u043e\u0446\u0435\u0441\u0441 \u0440\u0430\u0437\u043c\u044b\u0448\u043b\u0435\u043d\u0438\u044f") {
    return null;
  }
  return text;
}

function looksLikeSignedOutBody(text) {
  return (
    (text.includes(RU.signIn) && text.includes(RU.geminiChat))
    || text.includes(RU.signedOut)
  );
}

function slugify(value) {
  return normalizeText(value)
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 40) || "run";
}

function stampNow() {
  const now = new Date();
  const pad = (n) => String(n).padStart(2, "0");
  return [
    now.getFullYear(),
    pad(now.getMonth() + 1),
    pad(now.getDate()),
    "_",
    pad(now.getHours()),
    pad(now.getMinutes()),
    pad(now.getSeconds()),
  ].join("");
}

async function readStdin() {
  const chunks = [];
  for await (const chunk of process.stdin) {
    chunks.push(chunk);
  }
  return Buffer.concat(chunks).toString("utf8");
}

function tryParseJson(value) {
  if (typeof value !== "string") return value;
  const trimmed = value.trim();
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
  if (depth > 5) return value;
  if (typeof value === "string") {
    const parsed = tryParseJson(value);
    if (parsed === value) return value;
    return deepParse(parsed, depth + 1);
  }
  if (Array.isArray(value)) return value.map((item) => deepParse(item, depth + 1));
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

function summarizeRequest(req) {
  const url = new URL(req.url());
  const query = Object.fromEntries(url.searchParams.entries());
  delete query.at;
  delete query["f.sid"];
  delete query._reqid;

  const postData = parseFormPostData(req.postData());
  const body = postData?.["f.req"]?.[1];

  return {
    url: `${url.origin}${url.pathname}`,
    query,
    imageFlag: Array.isArray(body) ? body[49] ?? null : null,
    requestHash: Array.isArray(body) ? body[4] ?? null : null,
  };
}

async function createContext(profileDir, headless, chromeExecutablePath) {
  await fs.mkdir(profileDir, { recursive: true });
  await cleanupProfileProcesses(profileDir);
  const context = await chromium.launchPersistentContext(profileDir, {
    headless,
    executablePath: chromeExecutablePath,
    locale: "ru-RU",
    viewport: { width: 1600, height: 1200 },
  });
  const page = context.pages()[0] || await context.newPage();
  page.setDefaultTimeout(30000);
  return { context, page };
}

function escapePowerShellSingleQuoted(value) {
  return String(value).replace(/'/g, "''");
}

async function cleanupProfileProcesses(profileDir) {
  const escaped = escapePowerShellSingleQuoted(path.resolve(profileDir));
  const script = [
    `$profileDir = '${escaped}'`,
    "Get-CimInstance Win32_Process -Filter \"name = 'chrome.exe'\" |",
    "Where-Object { $_.CommandLine -match [regex]::Escape($profileDir) } |",
    "ForEach-Object { try { Stop-Process -Id $_.ProcessId -Force -ErrorAction Stop } catch {} }",
  ].join(" ");

  try {
    await execFileAsync("powershell", ["-NoProfile", "-Command", script], { windowsHide: true });
    await new Promise((resolve) => setTimeout(resolve, 1000));
  } catch {
    // Best effort cleanup only.
  }
}

async function navigateToGemini(page) {
  await page.goto(GEMINI_URL, {
    waitUntil: "domcontentloaded",
    timeout: 120000,
  });
  await page.waitForTimeout(6000);
}

async function bodyText(page) {
  try {
    return normalizeText(await page.locator("body").innerText());
  } catch {
    return "";
  }
}

async function anyVisible(locator) {
  const count = await locator.count().catch(() => 0);
  for (let index = 0; index < Math.min(count, 8); index += 1) {
    const visible = await locator.nth(index).isVisible().catch(() => false);
    if (visible) return true;
  }
  return false;
}

async function isReadyForPrompt(page) {
  try {
    const promptVisible = await anyVisible(
      page.locator('textarea, [role="textbox"], [contenteditable="true"]'),
    );
    if (!promptVisible) return false;

    const modeVisible = await anyVisible(page.locator('[data-test-id="bard-mode-menu-button"]'));
    const sendVisible = await anyVisible(
      page.locator(
        '[data-test-id="send-button"], button[aria-label*="Отправ"], button[aria-label*="Send"]',
      ),
    );
    const uploadVisible = await anyVisible(page.locator(`[aria-label="${RU.openUploadMenu}"]`));
    const tempChatVisible = await anyVisible(page.locator('[data-test-id="temp-chat-button"]'));
    return modeVisible || sendVisible || uploadVisible || tempChatVisible;
  } catch {
    return false;
  }
}

function looksLikeAccessChallengeBody(text) {
  const body = String(text || "").toLowerCase();
  return [
    "unusual traffic",
    "not a robot",
    "captcha",
    "verify it's you",
    "подтвердите, что это вы",
    "не робот",
    "подозрительный трафик",
    "подозрительная активность",
    "access denied",
    "доступ запрещен",
    "temporarily blocked",
  ].some((needle) => body.includes(needle));
}

function bodyPreview(text, limit = 280) {
  const value = normalizeText(text);
  if (!value) return "";
  return value.length > limit ? `${value.slice(0, limit)}…` : value;
}

async function captureReadyFailure(page, runDir, currentUrl, currentBody) {
  if (!runDir) return null;

  const screenshotPath = path.join(runDir, "ready_failure.png");
  const detailsPath = path.join(runDir, "ready_failure.txt");

  await page.screenshot({ path: screenshotPath, fullPage: true }).catch(() => null);
  await fs.writeFile(
    detailsPath,
    [
      `url: ${currentUrl || ""}`,
      "",
      String(currentBody || ""),
    ].join("\n"),
    "utf8",
  ).catch(() => null);

  return { screenshotPath, detailsPath };
}

async function ensureReady(page, timeoutSec = 45, runDir = null) {
  await navigateToGemini(page);
  const deadline = Date.now() + Math.max(20, Math.min(Number(timeoutSec) || 45, 90)) * 1000;
  let currentBody = "";
  let currentUrl = page.url();
  let retriedNavigation = false;

  while (Date.now() < deadline) {
    currentUrl = page.url();
    currentBody = await bodyText(page);

    if (looksLikeSignedOutBody(currentBody) || currentUrl.includes("accounts.google.com")) {
      throw new Error("Gemini web session is not signed in. Open the persistent Gemini profile and log in first.");
    }

    if (looksLikeAccessChallengeBody(currentBody)) {
      const artifacts = await captureReadyFailure(page, runDir, currentUrl, currentBody);
      const artifactNote = artifacts
        ? ` Capture: ${artifacts.screenshotPath}. Details: ${artifacts.detailsPath}.`
        : "";
      throw new Error(
        `Google blocked or challenged the Gemini web session before the prompt became available. URL: ${currentUrl}.${artifactNote}`,
      );
    }

    if (await isReadyForPrompt(page)) {
      return;
    }

    if (!retriedNavigation && Date.now() + 8000 < deadline) {
      retriedNavigation = true;
      await navigateToGemini(page).catch(() => null);
      continue;
    }

    await page.waitForTimeout(2000);
  }

  const artifacts = await captureReadyFailure(page, runDir, currentUrl, currentBody);
  const preview = bodyPreview(currentBody);
  const artifactNote = artifacts
    ? ` Capture: ${artifacts.screenshotPath}. Details: ${artifacts.detailsPath}.`
    : "";
  const previewNote = preview ? ` Body preview: ${preview}.` : "";
  throw new Error(`Gemini UI did not become ready. URL: ${currentUrl}.${previewNote}${artifactNote}`);
}

async function selectMode(page, modeKey) {
  const label = MODE_LABELS[modeKey] ?? MODE_LABELS.fast;
  const button = page.locator('[data-test-id="bard-mode-menu-button"]');
  const current = normalizeText(await button.textContent());
  if (current === label) return current;

  await button.click();
  await page.waitForTimeout(1500);
  const options = page.getByText(label, { exact: true });
  const count = await options.count();
  if (count === 0) {
    throw new Error(`Gemini Pro is not available for the current account.`);
  }

  let clicked = false;
  for (let index = count - 1; index >= 0; index -= 1) {
    const option = options.nth(index);
    const visible = await option.isVisible().catch(() => false);
    if (!visible) continue;

    const disabled = await option.evaluate((node) => {
      let currentNode = node;
      while (currentNode) {
        if (currentNode instanceof HTMLElement) {
          if (currentNode.hasAttribute("disabled")) return true;
          if (currentNode.getAttribute("aria-disabled") === "true") return true;
        }
        currentNode = currentNode.parentElement;
      }
      return false;
    }).catch(() => false);

    if (disabled) continue;

    await option.click();
    clicked = true;
    break;
  }

  if (!clicked) {
    throw new Error(`Gemini Pro is not available for the current account.`);
  }

  await page.waitForTimeout(2000);
  const actual = normalizeText(await button.textContent());
  if (actual !== label) {
    throw new Error(`Gemini stayed in mode ${actual || "unknown"} instead of ${label}.`);
  }
  return actual;
}

async function enableImageTool(page) {
  const chip = page.locator("button, [role='button']").filter({ hasText: RU.createImage }).first();
  await chip.waitFor({ state: "visible", timeout: 10000 });
  await chip.click();
  await page.waitForTimeout(1500);
}

async function startTemporaryChat(page) {
  const button = page.locator('[data-test-id="temp-chat-button"]').first();
  const count = await button.count();
  if (!count) return false;
  await button.click({ force: true });
  await page.waitForTimeout(2000);
  return true;
}

async function attachFile(page, filePath) {
  if (!filePath) return null;

  const resolved = path.resolve(filePath);
  await fs.access(resolved);

  const uploadButton = page.locator(`[aria-label="${RU.openUploadMenu}"]`).first();
  await uploadButton.waitFor({ state: "visible", timeout: 30000 });
  await uploadButton.click({ force: true });
  await page.waitForTimeout(500);

  const uploadMenuItem = page.locator(`[aria-label^="${RU.uploadFiles}"]`).first();
  await uploadMenuItem.waitFor({ state: "visible", timeout: 10000 });

  const [chooser] = await Promise.all([
    page.waitForEvent("filechooser", { timeout: 10000 }),
    uploadMenuItem.click({ force: true }),
  ]);
  await chooser.setFiles(resolved);

  // Gemini needs a short moment to upload and bind the file to the draft turn.
  await page.waitForTimeout(4000);

  const fileName = path.basename(resolved);
  await page.waitForFunction(
    (name) => document.body.innerText.includes(name),
    fileName,
    { timeout: 15000 },
  ).catch(() => null);

  return fileName;
}

function promptLocator(page) {
  return page.locator('textarea, [role="textbox"], [contenteditable="true"]').first();
}

async function collectSnapshot(page) {
  return page.evaluate(({ createImageText, imageChipText, signInText, geminiChatText, signedOutText }) => {
    const normalize = (value) => (value || "").trim().replace(/\s+/g, " ");
    const normalizeBlock = (value) => String(value || "")
      .replace(/\r/g, "")
      .split("\n")
      .map((line) => line.trim().replace(/[ \t]+/g, " "))
      .join("\n")
      .replace(/\n{3,}/g, "\n\n")
      .trim();
    const looksSignedOut = (text) => (
      (text.includes(signInText) && text.includes(geminiChatText))
      || text.includes(signedOutText)
    );
    const buttonMeta = Array.from(document.querySelectorAll("button, [role='button']")).map((node) => ({
      text: normalize(node.textContent),
      aria: normalize(node.getAttribute("aria-label")),
      className: normalize(node.getAttribute("class")),
    }));
    const containers = Array.from(document.querySelectorAll(".conversation-container"));
    const container = containers.at(-1) || null;
    const modeButton = document.querySelector('[data-test-id="bard-mode-menu-button"]');
    const response = container?.querySelector("model-response") || null;
    const thoughtEl = response?.querySelector("model-thoughts");
    const messageEl =
      response?.querySelector(
        "structured-content-container .markdown, structured-content-container, message-content .markdown, message-content",
      ) || null;
    const imageNodes = Array.from(response?.querySelectorAll("img") || []).map((img) => ({
      src: img.currentSrc || img.src || img.getAttribute("src") || "",
      alt: img.alt || null,
      width: img.naturalWidth || null,
      height: img.naturalHeight || null,
    }));
    const buttonTexts = buttonMeta.map((item) => item.text).filter(Boolean);
    const stopButtonVisible = buttonMeta.some((item) => {
      const haystack = `${item.text} ${item.aria} ${item.className}`;
      return haystack.includes("stop") || haystack.includes("\u041e\u0441\u0442\u0430\u043d\u043e\u0432\u0438\u0442\u044c");
    });
    const allBodyText = normalize(document.body.innerText);
    const generationHintVisible = [
      "Creating your image",
      "Constructing",
      "Generating",
      "\u0421\u043e\u0437\u0434\u0430\u044e \u0438\u0437\u043e\u0431\u0440\u0430\u0436\u0435\u043d\u0438\u0435",
      "\u0421\u043e\u0437\u0434\u0430\u044e \u0438\u043d\u0444\u043e\u0433\u0440\u0430\u0444\u0438\u043a\u0443",
      "\u0424\u043e\u0440\u043c\u0438\u0440\u0443\u044e \u0438\u0437\u043e\u0431\u0440\u0430\u0436\u0435\u043d\u0438\u0435",
      "\u0424\u043e\u0440\u043c\u0438\u0440\u0443\u044e \u0438\u043d\u0444\u043e\u0433\u0440\u0430\u0444\u0438\u043a\u0443",
    ].some((hint) => allBodyText.includes(hint));

    return {
      modeButtonText: normalize(modeButton?.textContent),
      imageToolActive: buttonTexts.some((text) => text.includes(imageChipText)),
      assistantText: normalizeBlock(messageEl?.innerText || messageEl?.textContent),
      thoughtText: normalizeBlock(thoughtEl?.innerText || thoughtEl?.textContent),
      lastTurnText: normalizeBlock(container?.innerText || container?.textContent),
      imageNodes,
      stopButtonVisible,
      generationHintVisible,
      signedOut: looksSignedOut(allBodyText),
      buttonTexts: buttonTexts.slice(0, 40),
      bodyIncludesImageTool: document.body.innerText.includes(createImageText),
      currentUrl: window.location.href,
    };
  }, {
    createImageText: RU.createImage,
    imageChipText: RU.imageChip,
    signInText: RU.signIn,
    geminiChatText: RU.geminiChat,
    signedOutText: RU.signedOut,
  });
}

async function readBlobAsDataUrl(page, src) {
  return page.evaluate(async (blobUrl) => {
    const response = await fetch(blobUrl);
    const blob = await response.blob();
    return await new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(String(reader.result || ""));
      reader.onerror = () => reject(reader.error || new Error("Failed to read blob"));
      reader.readAsDataURL(blob);
    });
  }, src);
}

async function maybeSaveImageSources(page, images, runDir) {
  const saved = [];
  const imageLocators = page.locator(".conversation-container").last().locator("model-response img");
  const locatorCount = await imageLocators.count();

  for (let index = 0; index < images.length; index += 1) {
    const image = images[index];
    let savedPath = null;
    const src = image.src || "";
    let dataUrl = src;

    if (src.startsWith("blob:")) {
      try {
        dataUrl = await readBlobAsDataUrl(page, src);
      } catch {
        dataUrl = src;
      }
    }

    if (typeof dataUrl === "string" && dataUrl.startsWith("data:image/")) {
      const match = dataUrl.match(/^data:(image\/[a-zA-Z0-9.+-]+);base64,(.+)$/);
      if (match) {
        const ext = match[1].split("/")[1]?.replace("jpeg", "jpg") || "png";
        savedPath = path.join(runDir, `result_${String(index + 1).padStart(2, "0")}.${ext}`);
        await fs.writeFile(savedPath, Buffer.from(match[2], "base64"));
      }
    }

    if (!savedPath && index < locatorCount) {
      try {
        savedPath = path.join(runDir, `result_${String(index + 1).padStart(2, "0")}.png`);
        await imageLocators.nth(index).screenshot({ path: savedPath });
      } catch {
        savedPath = null;
      }
    }

    saved.push({
      src,
      alt: image.alt || null,
      savedPath,
    });
  }

  return saved;
}

async function waitForResult(page, imageTool, timeoutSec, promptText = "") {
  const deadline = Date.now() + timeoutSec * 1000;
  let stableRounds = 0;
  let lastSignature = "";
  let snapshot = enrichSnapshot(await collectSnapshot(page), promptText);
  let sawActiveGeneration = snapshot.stopButtonVisible || snapshot.generationHintVisible;

  while (Date.now() < deadline) {
    await page.waitForTimeout(2000);
    snapshot = enrichSnapshot(await collectSnapshot(page), promptText);
    sawActiveGeneration = sawActiveGeneration || snapshot.stopButtonVisible || snapshot.generationHintVisible;

    if (snapshot.signedOut) {
      throw new Error("Gemini web session expired during generation. Open the persistent Gemini profile and sign in again.");
    }

    const signature = JSON.stringify({
      assistantText: snapshot.assistantText,
      thoughtText: snapshot.thoughtText,
      imageCount: snapshot.imageNodes.length,
      stopButtonVisible: snapshot.stopButtonVisible,
      lastTurnText: snapshot.lastTurnText.slice(0, 500),
    });

    if (signature === lastSignature) {
      stableRounds += 1;
    } else {
      stableRounds = 0;
      lastSignature = signature;
    }

    if (!imageTool) {
      if (!snapshot.stopButtonVisible && snapshot.assistantText && stableRounds >= 1) {
        return snapshot;
      }
      if (sawActiveGeneration && !snapshot.stopButtonVisible && snapshot.assistantText && stableRounds >= 2) {
        return snapshot;
      }
    }

    if (imageTool) {
      if (snapshot.imageNodes.length > 0 && !snapshot.stopButtonVisible && stableRounds >= 1) {
        return snapshot;
      }
      if (snapshot.stopButtonVisible || (snapshot.generationHintVisible && !snapshot.imageNodes.length)) {
        continue;
      }
      if (sawActiveGeneration && !snapshot.stopButtonVisible && stableRounds >= 3) {
        return snapshot;
      }
    }
  }

  return snapshot;
}

async function withTimeout(promise, timeoutMs, fallbackValue) {
  let timeoutId;
  try {
    return await Promise.race([
      promise,
      new Promise((resolve) => {
        timeoutId = setTimeout(() => resolve(fallbackValue), timeoutMs);
      }),
    ]);
  } finally {
    clearTimeout(timeoutId);
  }
}

async function handleLogin(page, profileDir, timeoutSec, headless) {
  await navigateToGemini(page);
  const currentBody = await bodyText(page);
  const currentUrl = page.url();
  const readyNow = await isReadyForPrompt(page);

  if (!looksLikeSignedOutBody(currentBody) && readyNow) {
    return {
      ok: true,
      signedIn: true,
      alreadySignedIn: true,
      profileDir,
      currentUrl,
      message: "Профиль Gemini уже готов. Можно запускать генерацию.",
    };
  }

  if (!headless) {
    try {
      await page.bringToFront();
    } catch {
      // ignore
    }
  }

  const deadline = Date.now() + timeoutSec * 1000;
  while (Date.now() < deadline) {
    await page.waitForTimeout(2000);
    if (page.isClosed()) {
      return {
        ok: false,
        signedIn: false,
        alreadySignedIn: false,
        profileDir,
        currentUrl: null,
        message: "Окно входа было закрыто до завершения авторизации.",
      };
    }

    const loopBody = await bodyText(page);
    if (looksLikeAccessChallengeBody(loopBody)) {
      return {
        ok: false,
        signedIn: false,
        alreadySignedIn: false,
        profileDir,
        currentUrl: page.url(),
        message: "Google запросил дополнительную проверку или временно ограничил доступ к Gemini. Заверши проверку вручную и повтори вход.",
      };
    }
    const loopReady = await isReadyForPrompt(page);
    if (!looksLikeSignedOutBody(loopBody) && loopReady) {
      return {
        ok: true,
        signedIn: true,
        alreadySignedIn: false,
        profileDir,
        currentUrl: page.url(),
        message: "Вход в Gemini завершен. Теперь можно запускать генерацию.",
      };
    }
  }

  return {
    ok: false,
    signedIn: false,
    alreadySignedIn: false,
    profileDir,
    currentUrl: page.url(),
    message: "Вход не был завершен в отведенное время. Открой вход еще раз и войди в аккаунт вручную.",
  };
}

async function runGeneration(page, input, capturesDir) {
  const modeKey = ["fast", "thinking", "pro"].includes(input.mode) ? input.mode : "fast";
  const imageTool = Boolean(input.imageTool);
  const filePath = input.filePath ? path.resolve(String(input.filePath)) : null;
  const prompt = normalizeText(input.prompt);
  const debugCaptures = Boolean(input.debugCaptures);
  const timeoutSec = Number.isFinite(input.timeoutSec) ? Number(input.timeoutSec) : 90;
  const waitAfterSubmitSec = Number.isFinite(input.waitAfterSubmitSec)
    ? Number(input.waitAfterSubmitSec)
    : 0;

  if (!prompt) {
    throw new Error("Prompt is required");
  }

  const runDir = path.join(
    capturesDir,
    "web_runs",
    `${stampNow()}_${slugify(input.captureLabel || `${modeKey}-${imageTool ? "image" : "text"}`)}`,
  );
  await fs.mkdir(runDir, { recursive: true });

  const notes = [];
  let streamResponseExcerpt = null;
  let streamResponsePath = null;
  let streamRequestSummary = null;

  await ensureReady(page, timeoutSec, runDir);
  if (filePath) {
    const startedTemporary = await startTemporaryChat(page);
    if (startedTemporary) {
      notes.push("Started a fresh temporary chat before uploading the file.");
    }
    await ensureReady(page, timeoutSec, runDir);
  }
  const modeActual = await selectMode(page, modeKey);
  if (modeActual !== MODE_LABELS[modeKey]) {
    notes.push(`Requested mode ${MODE_LABELS[modeKey]}, but UI shows ${modeActual || "unknown"}`);
  }

  if (imageTool) {
    await enableImageTool(page);
  }
  if (filePath) {
    const attachedFileName = await attachFile(page, filePath);
    if (attachedFileName) {
      notes.push(`Attached file: ${attachedFileName}`);
    }
  }

  let beforeCapturePath = null;
  if (debugCaptures) {
    beforeCapturePath = path.join(runDir, "before_submit.png");
    await page.screenshot({ path: beforeCapturePath, fullPage: true });
  }

  const box = promptLocator(page);
  await box.click();
  await box.fill(prompt);

  const streamRequestPromise = page.waitForRequest(
    (req) => req.method() === "POST" && req.url().includes("/StreamGenerate"),
    { timeout: 60000 },
  );
  const streamResponsePromise = page.waitForResponse(
    (resp) => resp.request().method() === "POST" && resp.url().includes("/StreamGenerate"),
    { timeout: 60000 },
  );

  await box.press("Enter");
  if (waitAfterSubmitSec > 0) {
    await page.waitForTimeout(waitAfterSubmitSec * 1000);
  }
  const [streamRequest, streamResponse] = await Promise.all([
    streamRequestPromise,
    streamResponsePromise,
  ]);

  streamRequestSummary = summarizeRequest(streamRequest);
  const streamText = await withTimeout(
    streamResponse.text().catch(() => null),
    10000,
    null,
  );
  if (typeof streamText === "string") {
    if (streamText.includes("[1060]")) {
      throw new Error("Gemini temporarily blocked this automated request (1060).");
    }
    streamResponseExcerpt = streamText.slice(0, 3000);
    streamResponsePath = path.join(runDir, "stream_response.txt");
    await fs.writeFile(streamResponsePath, streamText, "utf8");
  } else {
    notes.push("Stream response body did not finish within 10 seconds; used live UI state instead.");
  }

  const snapshot = await waitForResult(page, imageTool, timeoutSec, prompt);
  const savedImages = await maybeSaveImageSources(page, snapshot.imageNodes, runDir);

  let afterCapturePath = null;
  if (debugCaptures) {
    afterCapturePath = path.join(runDir, "after_submit.png");
    await page.screenshot({ path: afterCapturePath, fullPage: true });
  }

  const output = {
    ok: true,
    modeRequested: modeKey,
    modeActual: snapshot.modeButtonText || modeActual || null,
    imageToolRequested: imageTool,
    imageToolActive: snapshot.imageToolActive || snapshot.bodyIncludesImageTool,
    prompt,
    assistantText: snapshot.assistantText || null,
    thoughtText: maybeCleanThoughtText(snapshot.thoughtText),
    lastTurnText: snapshot.lastTurnText || null,
    beforeCapturePath,
    afterCapturePath,
    captureDir: runDir,
    streamResponsePath,
    streamRequestSummary,
    streamResponseExcerpt,
    images: savedImages,
    notes,
  };

  await fs.writeFile(
    path.join(runDir, "result.json"),
    JSON.stringify(output, null, 2),
    "utf8",
  );

  return output;
}

async function main() {
  const rawInput = await readStdin();
  const input = rawInput ? JSON.parse(rawInput) : {};
  const action = input.action || "run";
  const headless = input.headless !== false;
  const timeoutSec = Number.isFinite(input.timeoutSec) ? Number(input.timeoutSec) : 90;
  const chromeExecutablePath = input.chromeExecutablePath
    || "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe";
  const profileDir = path.resolve(input.profileDir || path.join(ROOT, "profiles", "gemini-runner"));
  const capturesDir = path.resolve(input.capturesDir || path.join(ROOT, "captures"));

  const { context, page } = await createContext(profileDir, headless, chromeExecutablePath);

  try {
    if (action === "login") {
      const output = await handleLogin(page, profileDir, timeoutSec, headless);
      process.stdout.write(JSON.stringify(output));
      return;
    }

    const output = await runGeneration(page, input, capturesDir);
    process.stdout.write(JSON.stringify(output));
  } finally {
    await context.close();
  }
}

main().catch((error) => {
  process.stderr.write(`${error?.stack || error}\n`);
  process.exitCode = 1;
});

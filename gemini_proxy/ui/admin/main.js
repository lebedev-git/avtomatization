import { loadAgentViews, defaultAgentViews } from "../shared/agents.js";
import { fetchJson } from "../shared/http.js";

let agents = defaultAgentViews();

const state = {
  currentView: "analytics",
};

const els = {
  agentList: document.getElementById("agentList"),
  currentTitle: document.getElementById("currentTitle"),
  currentDescription: document.getElementById("currentDescription"),
  analyticsView: document.getElementById("analyticsView"),
  protocolView: document.getElementById("protocolView"),
  saveBtn: document.getElementById("saveBtn"),
  status: document.getElementById("status"),
  day1Forms: document.getElementById("day1Forms"),
  day2Forms: document.getElementById("day2Forms"),
  day1Prompt: document.getElementById("day1Prompt"),
  day2Prompt: document.getElementById("day2Prompt"),
  summaryPrompt: document.getElementById("summaryPrompt"),
  infographicPrompt: document.getElementById("infographicPrompt"),
  protocolAnalysisPrompt: document.getElementById("protocolAnalysisPrompt"),
  protocolPrompt: document.getElementById("protocolPrompt"),
};

function setStatus(message, kind = "") {
  els.status.textContent = message || "";
  els.status.className = `status ${kind}`.trim();
}

function setBusy(isBusy) {
  els.saveBtn.disabled = isBusy;
}

function clear(node) {
  if (node) {
    node.replaceChildren();
  }
}

function renderForms(node, forms) {
  clear(node);
  (forms || []).forEach((form) => {
    const card = document.createElement("div");
    card.className = "form-card";

    const title = document.createElement("strong");
    title.textContent = form.name;

    const url = document.createElement("span");
    url.textContent = form.url;

    const survey = document.createElement("span");
    survey.textContent = `surveyId: ${form.survey_id}`;

    card.append(title, url, survey);
    node.append(card);
  });
}

function renderAgentList() {
  clear(els.agentList);
  Object.entries(agents).forEach(([id, agent]) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `agent-btn${state.currentView === id ? " active" : ""}`;
    button.textContent = agent.title;
    button.addEventListener("click", () => switchView(id));
    els.agentList.append(button);
  });
}

function switchView(viewId) {
  state.currentView = viewId;
  const agent = agents[viewId];
  els.currentTitle.textContent = agent.title;
  els.currentDescription.textContent = agent.description || "";
  els.saveBtn.textContent = agent.saveLabel;
  els.analyticsView.classList.toggle("hidden", viewId !== "analytics");
  els.protocolView.classList.toggle("hidden", viewId !== "protocol");
  renderAgentList();
}

function applyAnalyticsConfig(data) {
  const blocks = Object.fromEntries((data.blocks || []).map((block) => [block.id, block]));
  renderForms(els.day1Forms, blocks.day1?.source_forms || []);
  renderForms(els.day2Forms, blocks.day2?.source_forms || []);
  els.day1Prompt.value = blocks.day1?.system_prompt || "";
  els.day2Prompt.value = blocks.day2?.system_prompt || "";
  els.summaryPrompt.value = blocks.summary?.system_prompt || "";
  els.infographicPrompt.value = blocks.infographic?.system_prompt || "";
  return data.sync_message || "";
}

function applyProtocolConfig(data) {
  els.protocolAnalysisPrompt.value = data.analysis_prompt || "";
  els.protocolPrompt.value = data.protocol_prompt || "";
}

async function hydrateAgentViews() {
  try {
    agents = await loadAgentViews();
  } catch (error) {
    agents = defaultAgentViews();
  }
}

async function loadConfig() {
  setBusy(true);
  setStatus("Загружаю промпты...");
  try {
    const [analyticsResult, protocolResult] = await Promise.allSettled([
      fetchJson("/agents/analytics-note/config"),
      fetchJson("/agents/protocol/config"),
    ]);

    const errors = [];
    const messages = [];

    if (analyticsResult.status === "fulfilled") {
      const syncMessage = applyAnalyticsConfig(analyticsResult.value);
      if (syncMessage) {
        messages.push(syncMessage);
      }
    } else {
      errors.push(`Аналитика: ${String(analyticsResult.reason?.message || analyticsResult.reason)}`);
    }

    if (protocolResult.status === "fulfilled") {
      applyProtocolConfig(protocolResult.value);
    } else {
      errors.push(`Протокол: ${String(protocolResult.reason?.message || protocolResult.reason)}`);
    }

    if (errors.length) {
      setStatus(errors.join(" "), "err");
      return;
    }

    setStatus(messages.join(" ") || "Промпты загружены.", "ok");
  } catch (error) {
    setStatus(String(error.message || error), "err");
  } finally {
    setBusy(false);
  }
}

async function saveAnalyticsConfig() {
  const result = await fetchJson("/agents/analytics-note/config", {
    method: "POST",
    body: JSON.stringify({
      day1_prompt: els.day1Prompt.value.trim(),
      day2_prompt: els.day2Prompt.value.trim(),
      summary_prompt: els.summaryPrompt.value.trim(),
      infographic_prompt: els.infographicPrompt.value.trim(),
    }),
  });
  const syncMessage = result.sync_message ? ` ${result.sync_message}` : "";
  return `Промпты аналитики сохранены.${syncMessage}`.trim();
}

async function saveProtocolConfig() {
  await fetchJson("/agents/protocol/config", {
    method: "POST",
    body: JSON.stringify({
      analysis_prompt: els.protocolAnalysisPrompt.value.trim(),
      protocol_prompt: els.protocolPrompt.value.trim(),
    }),
  });
  return "Промпты протокола сохранены.";
}

async function saveCurrentConfig() {
  setBusy(true);
  setStatus(
    state.currentView === "analytics" ? "Сохраняю промпты аналитики..." : "Сохраняю промпты протокола...",
  );
  try {
    const message = state.currentView === "analytics"
      ? await saveAnalyticsConfig()
      : await saveProtocolConfig();
    setStatus(message, "ok");
  } catch (error) {
    setStatus(String(error.message || error), "err");
  } finally {
    setBusy(false);
  }
}

async function init() {
  await hydrateAgentViews();
  renderAgentList();
  switchView(state.currentView);
  els.saveBtn.addEventListener("click", saveCurrentConfig);
  await loadConfig();
}

init().catch((error) => {
  setStatus(String(error.message || error), "err");
});

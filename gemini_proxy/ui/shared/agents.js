import { fetchJson } from "./http.js";

const VIEW_SPECS = {
  analytics: {
    agentId: "analytics-note",
    title: "Аналитика",
    description: "Собирает документы первого и второго дня, итоговую аналитику и отдельный блок инфографики в NotebookLM.",
    resetEndpoint: "/agents/analytics-note/reset",
    saveLabel: "Сохранить промпты аналитики",
  },
  protocol: {
    agentId: "protocol",
    title: "Протокол",
    description: "Формирует итоговый протокол встречи из аудио или видео.",
    resetEndpoint: "/agents/protocol/reset",
    saveLabel: "Сохранить промпты протокола",
  },
};

export function defaultAgentViews() {
  return Object.fromEntries(
    Object.entries(VIEW_SPECS).map(([viewId, spec]) => [viewId, { ...spec }]),
  );
}

export async function loadAgentViews() {
  const views = defaultAgentViews();
  const payload = await fetchJson("/agents");
  const serverAgents = Array.isArray(payload.agents) ? payload.agents : [];

  Object.values(views).forEach((view) => {
    const serverAgent = serverAgents.find((item) => item.id === view.agentId);
    if (!serverAgent) {
      return;
    }
    if (serverAgent.name) {
      view.title = serverAgent.name;
    }
    if (serverAgent.description) {
      view.description = serverAgent.description;
    }
  });

  return views;
}

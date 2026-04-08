import { defaultAgentViews, loadAgentViews } from "/ui/shared/agents.js";
import { extractApiMessage, fetchJson, readJsonResponse } from "/ui/shared/http.js";

(() => {
  let agents = defaultAgentViews();

  const state = {
    currentView: "analytics",
    busy: {
      day1: false,
      day2: false,
      summary: false,
      infographic: false,
      protocol: false,
    },
    analytics: {
      day1History: null,
      day2History: null,
      summaryState: null,
      draftDates: {
        day1: "",
        day2From: "",
        day2To: "",
      },
      infographicDraft: {
        googleDocUrl: "",
      },
      pendingInfographicPhoto: null,
      pendingInfographicLogo: null,
      loaded: false,
    },
    protocol: {
      reportState: null,
      loaded: false,
      pendingFile: null,
    },
    pollers: {
      analytics: null,
      protocol: null,
      liveClock: null,
      analyticsBusy: false,
      protocolBusy: false,
    },
  };

  const ids = [
    "agentList",
    "currentTitle",
    "currentDescription",
    "analyticsView",
    "protocolView",
    "resetBtn",
    "day1Date",
    "day1RunBtn",
    "day1Info",
    "day1Lock",
    "day1Status",
    "day1DocName",
    "day1Meta",
    "day1Download",
    "day1TimelineSummary",
    "day1Timeline",
    "day2DateFrom",
    "day2DateTo",
    "day2RunBtn",
    "day2Info",
    "day2Lock",
    "day2Status",
    "day2DocName",
    "day2Meta",
    "day2Download",
    "day2TimelineSummary",
    "day2Timeline",
    "summaryDay1State",
    "summaryDay2State",
    "summaryFinalState",
    "summaryRunBtn",
    "summaryStatus",
    "summaryDocName",
    "summaryMeta",
    "summaryDownload",
    "summaryTimelineSummary",
    "summaryTimeline",
    "infographicSummaryState",
    "infographicGoogleDocState",
    "infographicNotebookState",
    "infographicGoogleDoc",
    "infographicPhoto",
    "infographicLogo",
    "infographicPhotoInfo",
    "infographicLogoInfo",
    "infographicRunBtn",
    "infographicStatus",
    "infographicNotebookName",
    "infographicMeta",
    "infographicOpen",
    "infographicImageWrap",
    "infographicImage",
    "infographicTimelineSummary",
    "infographicTimeline",
    "protocolFile",
    "protocolFileInfo",
    "protocolRunBtn",
    "protocolStatus",
    "protocolDocName",
    "protocolMeta",
    "protocolDownload",
    "protocolTimelineSummary",
    "protocolTimeline",
    "protocolStateSource",
    "protocolStateStrategy",
    "protocolStateDoc",
  ];

  const el = Object.fromEntries(ids.map((id) => [id, document.getElementById(id)]));

  function text(node, value) {
    if (node) {
      node.textContent = value || "";
    }
  }

  function clear(node) {
    if (node) {
      node.replaceChildren();
    }
  }

  function setStatus(node, message, tone = "") {
    if (!node) {
      return;
    }
    node.className = "status";
    if (tone) {
      node.classList.add(tone);
    }
    node.textContent = message || "";
  }

  function sessionKey(name) {
    return `codex-generated-${name}`;
  }

  function markGenerated(name, value) {
    if (value) {
      sessionStorage.setItem(sessionKey(name), "1");
    } else {
      sessionStorage.removeItem(sessionKey(name));
    }
  }

  function hasGenerated(name) {
    return sessionStorage.getItem(sessionKey(name)) === "1";
  }

  function clearGenerated(names) {
    names.forEach((name) => markGenerated(name, false));
  }

  function formatDate(value) {
    if (!value) {
      return "—";
    }
    const date = new Date(`${value}T00:00:00`);
    if (Number.isNaN(date.getTime())) {
      return value;
    }
    return new Intl.DateTimeFormat("ru-RU", {
      day: "numeric",
      month: "long",
      year: "numeric",
    }).format(date);
  }

  function formatDateTime(value) {
    if (!value) {
      return "—";
    }
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) {
      return value;
    }
    return new Intl.DateTimeFormat("ru-RU", {
      day: "2-digit",
      month: "2-digit",
      year: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    }).format(date);
  }

  function formatClock(value) {
    if (!value) {
      return "";
    }
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) {
      return value;
    }
    return new Intl.DateTimeFormat("ru-RU", {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    }).format(date);
  }

  function formatDuration(seconds) {
    if (!Number.isFinite(seconds) || seconds < 0) {
      return "—";
    }
    const total = Math.max(0, Math.floor(seconds));
    const hours = Math.floor(total / 3600);
    const minutes = Math.floor((total % 3600) / 60);
    const secs = total % 60;
    return [hours, minutes, secs]
      .map((item, index) => (index === 0 ? String(item) : String(item).padStart(2, "0")))
      .join(":");
  }

  function parseDateValue(value) {
    if (!value) {
      return null;
    }
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? null : date;
  }

  function durationSecondsBetween(startValue, endValue) {
    const start = parseDateValue(startValue);
    const end = parseDateValue(endValue);
    if (!start || !end) {
      return null;
    }
    return Math.max(0, (end.getTime() - start.getTime()) / 1000);
  }

  function stepDurationSeconds(step) {
    if (!step) {
      return null;
    }
    const endValue =
      step.finished_at || (step.status === "active" ? new Date().toISOString() : null);
    return durationSecondsBetween(step.started_at, endValue);
  }

  function timelineDurationSeconds(timeline) {
    const steps = Array.isArray(timeline?.steps) ? timeline.steps : [];
    let startedAt = null;
    let finishedAt = null;

    steps.forEach((step) => {
      const stepStart = parseDateValue(step.started_at);
      const stepEnd = parseDateValue(
        step.finished_at || (timeline?.running && step.status === "active" ? new Date().toISOString() : null),
      );

      if (stepStart && (!startedAt || stepStart < startedAt)) {
        startedAt = stepStart;
      }
      if (stepEnd && (!finishedAt || stepEnd > finishedAt)) {
        finishedAt = stepEnd;
      }
    });

    if (!startedAt || !finishedAt) {
      return null;
    }
    return Math.max(0, (finishedAt.getTime() - startedAt.getTime()) / 1000);
  }

  function formatTimelineDuration(timeline) {
    const seconds = timelineDurationSeconds(timeline);
    return seconds == null ? "" : formatDuration(seconds);
  }

  function formatExecutionTime(timeline) {
    const duration = formatTimelineDuration(timeline);
    return duration ? `Время выполнения: ${duration}` : "";
  }

  function formatBytes(bytes) {
    if (!Number.isFinite(bytes) || bytes <= 0) {
      return "0 Б";
    }
    const units = ["Б", "КБ", "МБ", "ГБ"];
    let value = bytes;
    let index = 0;
    while (value >= 1024 && index < units.length - 1) {
      value /= 1024;
      index += 1;
    }
    return `${value.toFixed(value >= 10 || index === 0 ? 0 : 1)} ${units[index]}`;
  }

  function formatFileLabel(file) {
    if (!file) {
      return "";
    }
    return `${file.name} • ${formatBytes(file.size)} • ${file.type || "тип не определен"}`;
  }

  function createActionLink({ href, label, className = "download" }) {
    const link = document.createElement("a");
    link.className = className;
    link.href = href;
    link.textContent = label;
    link.target = "_blank";
    link.rel = "noreferrer";
    return link;
  }

  function setInfographicPreview(url, alt = "Инфографика") {
    if (!el.infographicImageWrap || !el.infographicImage) {
      return;
    }
    if (url) {
      el.infographicImage.src = url;
      el.infographicImage.alt = alt;
      el.infographicImageWrap.hidden = false;
      return;
    }
    el.infographicImage.removeAttribute("src");
    el.infographicImage.alt = alt;
    el.infographicImageWrap.hidden = true;
  }

  function compactMessage(message, fallback = "Нет данных") {
    const value = String(message || "").trim();
    return value || fallback;
  }

  function createTimelineStep(step) {
    const row = document.createElement("div");
    row.className = `timeline-step ${step.status || "pending"}`;

    const marker = document.createElement("div");
    marker.className = "timeline-marker";

    const content = document.createElement("div");
    content.className = "timeline-step-content";

    const title = document.createElement("strong");
    title.textContent = step.label || step.id || "Шаг";

    const meta = document.createElement("small");
    const parts = [];
    const stepDuration = formatDuration(stepDurationSeconds(step));
    if (step.message) {
      parts.push(step.message);
    }
    if (stepDuration !== "—") {
      if (step.status === "completed") {
        parts.push(`Длительность ${stepDuration}`);
      } else if (step.status === "active") {
        parts.push(`В работе ${stepDuration}`);
      } else if (step.status === "error") {
        parts.push(`Ошибка через ${stepDuration}`);
      }
    }
    meta.textContent = parts.join(" • ");

    content.append(title, meta);
    row.append(marker, content);
    return row;
  }

  function renderTimeline(summaryNode, listNode, timeline, emptySummary) {
    const steps = Array.isArray(timeline?.steps) ? timeline.steps : [];
    const summaryText = compactMessage(timeline?.summary, emptySummary);
    const totalDuration = formatTimelineDuration(timeline);
    text(
      summaryNode,
      totalDuration ? `${summaryText} • Время работы: ${totalDuration}` : summaryText,
    );
    clear(listNode);

    if (!steps.length) {
      listNode.append(
        createTimelineStep({
          id: "idle",
          label: "Ожидание запуска",
          status: "pending",
          message: emptySummary,
        }),
      );
      return;
    }

    steps.forEach((step) => listNode.append(createTimelineStep(step)));
  }

  function createDownloadLink({ href, label }) {
    const link = document.createElement("a");
    link.className = "download";
    link.href = href;
    link.textContent = label;
    link.setAttribute("download", "");
    return link;
  }

  function renderDownloads(slot, key, items, ready, stale = false) {
    clear(slot);
    const availableItems = Array.isArray(items)
      ? items.filter((item) => item?.url && item?.label)
      : [];

    if (!availableItems.length || !ready || stale || !hasGenerated(key)) {
      if (availableItems.length && ready && !stale) {
        const note = document.createElement("span");
        note.className = "session-note";
        note.textContent = "Ссылка появится после генерации документа в текущей сессии.";
        slot.append(note);
      }
      return;
    }

    availableItems.forEach((item) => {
      slot.append(createDownloadLink({ href: item.url, label: item.label }));
    });
  }

  function renderDownload(slot, key, documentUrl, ready, stale = false, label = "Скачать .docx") {
    renderDownloads(
      slot,
      key,
      documentUrl
        ? [
            {
              url: documentUrl,
              label,
            },
          ]
        : [],
      ready,
      stale,
    );
  }

  function hydrateDraftValue(key, history, fallbackField) {
    if (!history) {
      state.analytics.draftDates[key] = "";
      return;
    }
    const options = Array.isArray(history.available_dates) ? history.available_dates : [];
    const allowed = new Set(options.map((item) => item.date));
    const fallback = history[fallbackField] || options[0]?.date || "";
    if (!allowed.has(state.analytics.draftDates[key])) {
      state.analytics.draftDates[key] = fallback || "";
    }
  }

  function fillDateSelect(select, options, selectedValue, disabled, placeholder = "") {
    if (!select) {
      return;
    }
    const items = Array.isArray(options) ? options : [];
    const normalizedSelectedValue = selectedValue || "";
    clear(select);
    if (placeholder) {
      const option = document.createElement("option");
      option.value = "";
      option.textContent = placeholder;
      option.selected = !normalizedSelectedValue;
      select.append(option);
    }

    if (!items.length && !placeholder) {
      const option = document.createElement("option");
      option.value = "";
      option.textContent = "Нет доступных дат";
      select.append(option);
      select.disabled = true;
      return;
    }

    items.forEach((item) => {
      const option = document.createElement("option");
      option.value = item.date;
      const total = Number(item.total_count ?? item.count ?? 0);
      const secondary = Number(item.secondary_count ?? 0);
      option.textContent =
        secondary > 0
          ? `${item.label} • вход: ${item.count ?? 0}, выход: ${secondary}`
          : `${item.label} • ответов: ${total}`;
      if (item.date === normalizedSelectedValue) {
        option.selected = true;
      }
      select.append(option);
    });
    if (placeholder && normalizedSelectedValue && !items.some((item) => item.date === normalizedSelectedValue)) {
      select.value = "";
    }
    select.disabled = Boolean(disabled || (!items.length && !placeholder));
  }

  function findDateOption(history, value) {
    const options = Array.isArray(history?.available_dates) ? history.available_dates : [];
    return options.find((item) => item.date === value) || null;
  }

  function getDay2ToOptions(history, dateFrom) {
    const options = Array.isArray(history?.available_dates) ? history.available_dates : [];
    return options.filter((item) => !dateFrom || item.date > dateFrom);
  }

  function sanitizeDay2ToValue(history, dateFrom, candidate) {
    if (!candidate) {
      return "";
    }
    return getDay2ToOptions(history, dateFrom).some((item) => item.date === candidate)
      ? candidate
      : "";
  }

  function normalizeDay2Drafts(history) {
    const locked = Boolean(history?.locked);
    const from = locked
      ? history.selected_date_from || ""
      : state.analytics.draftDates.day2From || history?.date_from_default || "";
    const rawTo = locked
      ? history.selected_date_to || ""
      : state.analytics.draftDates.day2To || history?.date_to_default || "";
    const to = sanitizeDay2ToValue(history, from, rawTo);
    if (!locked && state.analytics.draftDates.day2To !== to) {
      state.analytics.draftDates.day2To = to;
    }
    return {
      from,
      to,
    };
  }

  function day1InfoText(history, selectedValue) {
    const option = findDateOption(history, selectedValue);
    const period = option?.label || formatDate(selectedValue);
    const entryCount = Number(option?.count ?? 0);
    const exitCount = Number(option?.secondary_count ?? 0);
    return [
      period ? `Период: ${period}` : "",
      `Входных ответов: ${entryCount}`,
      `Выходных ответов: ${exitCount}`,
    ].filter(Boolean).join(" • ");
  }

  function day2InfoText(history, dateFrom, dateTo) {
    const options = Array.isArray(history?.available_dates) ? history.available_dates : [];
    const normalizedTo = dateTo || dateFrom;
    const selected = options.filter((item) => item.date >= dateFrom && item.date <= normalizedTo);
    const total = selected.reduce((sum, item) => sum + Number(item.total_count ?? item.count ?? 0), 0);
    const period = normalizedTo && normalizedTo !== dateFrom
      ? `${formatDate(dateFrom)} - ${formatDate(normalizedTo)}`
      : formatDate(dateFrom);
    return [
      period ? `Период: ${period}` : "",
      `Ответов: ${total}`,
    ].filter(Boolean).join(" • ");
  }

  function describeAnalyticsState(reportState) {
    if (!reportState) {
      return "Документ еще не собран.";
    }
    if (reportState.timeline?.running) {
      return compactMessage(reportState.timeline.summary, "Выполняется сборка.");
    }
    if (reportState.ready) {
      const parts = [
        `Готово${reportState.created_at ? ` • ${formatDateTime(reportState.created_at)}` : ""}`,
        formatExecutionTime(reportState.timeline),
      ].filter(Boolean);
      return parts.join(" • ");
    }
    if (reportState.stale) {
      return "Есть предыдущий документ, но он устарел и требует пересборки.";
    }
    return "Документ еще не собран.";
  }

  function renderAnalyticsCard(blockId, history, reportState) {
    const isDay1 = blockId === "day1";
    const primarySelect = isDay1 ? el.day1Date : el.day2DateFrom;
    const secondarySelect = isDay1 ? null : el.day2DateTo;
    const button = isDay1 ? el.day1RunBtn : el.day2RunBtn;
    const infoNode = isDay1 ? el.day1Info : el.day2Info;
    const lockNode = isDay1 ? el.day1Lock : el.day2Lock;
    const statusNode = isDay1 ? el.day1Status : el.day2Status;
    const docNameNode = isDay1 ? el.day1DocName : el.day2DocName;
    const metaNode = isDay1 ? el.day1Meta : el.day2Meta;
    const downloadNode = isDay1 ? el.day1Download : el.day2Download;
    const timelineSummaryNode = isDay1 ? el.day1TimelineSummary : el.day2TimelineSummary;
    const timelineNode = isDay1 ? el.day1Timeline : el.day2Timeline;
    const busy = state.busy[blockId];
    const executionTime = formatExecutionTime(reportState?.timeline || history?.timeline || null);

    if (!history) {
      fillDateSelect(primarySelect, [], "", true);
      if (secondarySelect) {
        fillDateSelect(secondarySelect, [], "", true);
      }
      text(infoNode, "Подгружаю доступные даты...");
      text(lockNode, "");
      setStatus(statusNode, "Загружаю состояние блока...");
      text(docNameNode, "");
      text(metaNode, "");
      renderDownload(downloadNode, blockId, "", false, false);
      renderTimeline(
        timelineSummaryNode,
        timelineNode,
        null,
        "Запусков по этому блоку пока не было.",
      );
      if (button) {
        button.disabled = true;
      }
      return;
    }

    const selectedValue = isDay1
      ? (history.locked ? history.selected_date || "" : state.analytics.draftDates.day1)
      : normalizeDay2Drafts(history).from;
    const selectedRange = isDay1 ? null : normalizeDay2Drafts(history);
    fillDateSelect(primarySelect, history.available_dates, selectedValue, history.locked || busy);
    if (secondarySelect) {
      const day2ToOptions = getDay2ToOptions(history, selectedRange?.from || "");
      const day2Placeholder = history.locked
        ? (selectedRange?.to ? "Выбранная вторая дата" : "Один день")
        : day2ToOptions.length
          ? "Оставьте пустым для одного дня"
          : "Нет более поздней даты";
      fillDateSelect(
        secondarySelect,
        day2ToOptions,
        selectedRange?.to || "",
        history.locked || busy,
        day2Placeholder,
      );
    }

    text(
      infoNode,
      isDay1
        ? day1InfoText(history, selectedValue)
        : day2InfoText(history, selectedRange?.from || "", selectedRange?.to || ""),
    );
    text(lockNode, "");

    const timeline = reportState?.timeline || history.timeline || null;
    if (busy || timeline?.running) {
      setStatus(statusNode, compactMessage(timeline?.summary, "Идет обработка..."));
    } else if (reportState?.stale) {
      setStatus(statusNode, "Документ устарел после изменения исходных данных.", "err");
    } else if (reportState?.ready) {
      setStatus(
        statusNode,
        [
          `Документ готов${reportState.created_at ? ` • ${formatDateTime(reportState.created_at)}` : ""}`,
          executionTime,
        ].filter(Boolean).join(" • "),
        "ok",
      );
    } else {
      setStatus(statusNode, (history.available_dates || []).length ? "" : "Нет данных для формирования документа.", (history.available_dates || []).length ? "" : "err");
    }

    if (button) {
      const missingSelection = isDay1
        ? !selectedValue
        : !selectedRange?.from || (selectedRange?.to && selectedRange.to <= selectedRange.from);
      button.disabled = busy || history.locked || missingSelection;
    }

    if (reportState?.document_name) {
      text(docNameNode, reportState.document_name);
      const metaParts = [];
      if (reportState.period_label) {
        metaParts.push(reportState.period_label);
      }
      if (reportState.created_at) {
        metaParts.push(`Собрано: ${formatDateTime(reportState.created_at)}`);
      }
      if (executionTime) {
        metaParts.push(executionTime);
      }
      if (reportState.stale) {
        metaParts.push("Требует пересборки");
      }
      text(metaNode, metaParts.join(" • "));
    } else {
      text(docNameNode, "Документ еще не сформирован");
      text(metaNode, "");
    }

    renderDownload(
      downloadNode,
      blockId,
      reportState?.document_url || "",
      Boolean(reportState?.ready),
      Boolean(reportState?.stale),
    );
    renderTimeline(
      timelineSummaryNode,
      timelineNode,
      timeline,
      "Запусков по этому блоку пока не было.",
    );
  }

  function getInfographicGoogleDoc() {
    return String(state.analytics.infographicDraft.googleDocUrl || "").trim();
  }

  function getInfographicPhoto() {
    return state.analytics.pendingInfographicPhoto;
  }

  function setInfographicPhoto(file) {
    state.analytics.pendingInfographicPhoto = file || null;
  }

  function getInfographicLogo() {
    return state.analytics.pendingInfographicLogo;
  }

  function setInfographicLogo(file) {
    state.analytics.pendingInfographicLogo = file || null;
  }

  function updateInfographicFileInfo() {
    text(
      el.infographicPhotoInfo,
      getInfographicPhoto() ? formatFileLabel(getInfographicPhoto()) : "Общее фото пока не выбрано.",
    );
    text(
      el.infographicLogoInfo,
      getInfographicLogo() ? formatFileLabel(getInfographicLogo()) : "Логотип пока не выбран.",
    );
  }

  function renderInfographicCardLegacy(summaryState) {
    const summaryReport = summaryState?.summary || null;
    const infographic = summaryState?.infographic || null;
    const timeline = infographic?.timeline || null;
    const executionTime = formatExecutionTime(timeline);
    if (!state.analytics.infographicDraft.googleDocUrl && infographic?.google_doc_url) {
      state.analytics.infographicDraft.googleDocUrl = infographic.google_doc_url;
    }
    const googleDocUrl = getInfographicGoogleDoc();
    const photo = getInfographicPhoto();
    const logo = getInfographicLogo();
    const dependenciesReady = Boolean(summaryState?.infographic_dependencies_ready);
    const blockedByRunning = Boolean(summaryReport?.timeline?.running);
    const missingInputs = !googleDocUrl || !photo || !logo;

    text(el.infographicSummaryState, describeAnalyticsState(summaryReport));
    text(
      el.infographicGoogleDocState,
      googleDocUrl || infographic?.google_doc_url || "Ссылка на Google Doc пока не указана.",
    );
    text(
      el.infographicNotebookState,
      infographic?.notebook_title
        ? `${infographic.notebook_title}${infographic.created_at ? ` • ${formatDateTime(infographic.created_at)}` : ""}`
        : "Отдельный блокнот NotebookLM пока не создавался.",
    );
    if (el.infographicGoogleDoc && el.infographicGoogleDoc.value !== googleDocUrl) {
      el.infographicGoogleDoc.value = googleDocUrl;
    }
    updateInfographicFileInfo();

    el.infographicRunBtn.disabled =
      state.busy.infographic || !dependenciesReady || blockedByRunning || missingInputs;

    if (state.busy.infographic || timeline?.running) {
      setStatus(
        el.infographicStatus,
        compactMessage(timeline?.summary, "Собираю блок инфографики..."),
      );
    } else if (!dependenciesReady) {
      setStatus(el.infographicStatus, "Сначала соберите актуальную итоговую аналитику.");
    } else if (blockedByRunning) {
      setStatus(el.infographicStatus, "Дождитесь завершения итоговой аналитики.");
    } else if (infographic?.stale) {
      setStatus(el.infographicStatus, "Итоговая аналитика изменилась. Пересоберите инфографику.", "err");
    } else if (infographic?.ready) {
      setStatus(
        el.infographicStatus,
        [
          `Инфографика запущена${infographic.created_at ? ` • ${formatDateTime(infographic.created_at)}` : ""}`,
          executionTime,
        ].filter(Boolean).join(" • "),
        "ok",
      );
    } else if (missingInputs) {
      setStatus(el.infographicStatus, "Укажите Google Doc, общее фото и логотип.");
    } else {
      setStatus(el.infographicStatus, "Можно запускать инфографику.");
    }

    if (infographic?.notebook_title) {
      text(el.infographicNotebookName, infographic.notebook_title);
      const metaParts = [];
      if (infographic.profile) {
        metaParts.push(`Профиль: ${infographic.profile}`);
      }
      if (infographic.created_at) {
        metaParts.push(`Запущено: ${formatDateTime(infographic.created_at)}`);
      }
      if (infographic.summary_title) {
        metaParts.push(`Основа: ${infographic.summary_title}`);
      }
      if (infographic.photo_name) {
        metaParts.push(`Фото: ${infographic.photo_name}`);
      }
      if (infographic.logo_name) {
        metaParts.push(`Логотип: ${infographic.logo_name}`);
      }
      if (executionTime) {
        metaParts.push(executionTime);
      }
      if (infographic.stale) {
        metaParts.push("Требует пересборки");
      }
      text(el.infographicMeta, metaParts.join(" • "));
    } else {
      text(el.infographicNotebookName, "NotebookLM для инфографики еще не создан");
      text(el.infographicMeta, "");
    }

    clear(el.infographicOpen);
    if (infographic?.notebook_url) {
      el.infographicOpen.append(
        createActionLink({
          href: infographic.notebook_url,
          label: "Открыть NotebookLM",
          className: "download",
        }),
      );
    }

    renderTimeline(
      el.infographicTimelineSummary,
      el.infographicTimeline,
      timeline,
      "Инфографика пока не запускалась.",
    );
  }

  function renderInfographicCard(summaryState) {
    const summaryReport = summaryState?.summary || null;
    const infographic = summaryState?.infographic || null;
    const timeline = infographic?.timeline || null;
    const executionTime = formatExecutionTime(timeline);
    if (!state.analytics.infographicDraft.googleDocUrl && infographic?.google_doc_url) {
      state.analytics.infographicDraft.googleDocUrl = infographic.google_doc_url;
    }
    const googleDocUrl = getInfographicGoogleDoc();
    const photo = getInfographicPhoto();
    const logo = getInfographicLogo();
    const dependenciesReady = Boolean(summaryState?.infographic_dependencies_ready);
    const blockedByRunning = Boolean(summaryReport?.timeline?.running);
    const missingInputs = !googleDocUrl || !photo || !logo;

    text(el.infographicSummaryState, describeAnalyticsState(summaryReport));
    text(
      el.infographicGoogleDocState,
      googleDocUrl || infographic?.google_doc_url || "Ссылка на Google Doc пока не указана.",
    );
    text(
      el.infographicNotebookState,
      infographic?.image_name
        ? `${infographic.image_name}${infographic.created_at ? ` • ${formatDateTime(infographic.created_at)}` : ""}`
        : infographic?.notebook_title
          ? `${infographic.notebook_title}${infographic.created_at ? ` • ${formatDateTime(infographic.created_at)}` : ""}`
          : "Инфографика пока не создана.",
    );
    if (el.infographicGoogleDoc && el.infographicGoogleDoc.value !== googleDocUrl) {
      el.infographicGoogleDoc.value = googleDocUrl;
    }
    updateInfographicFileInfo();

    el.infographicRunBtn.disabled =
      state.busy.infographic || !dependenciesReady || blockedByRunning || missingInputs;

    if (state.busy.infographic || timeline?.running) {
      setStatus(
        el.infographicStatus,
        compactMessage(timeline?.summary, "Собираю блок инфографики..."),
      );
    } else if (!dependenciesReady) {
      setStatus(el.infographicStatus, "Сначала соберите актуальную итоговую аналитику.");
    } else if (blockedByRunning) {
      setStatus(el.infographicStatus, "Дождитесь завершения итоговой аналитики.");
    } else if (infographic?.stale) {
      setStatus(el.infographicStatus, "Итоговая аналитика изменилась. Пересоберите инфографику.", "err");
    } else if (infographic?.ready) {
      setStatus(
        el.infographicStatus,
        [
          `Инфографика сохранена${infographic.created_at ? ` • ${formatDateTime(infographic.created_at)}` : ""}`,
          executionTime,
        ].filter(Boolean).join(" • "),
        "ok",
      );
    } else if (missingInputs) {
      setStatus(el.infographicStatus, "Укажите Google Doc, общее фото и логотип.");
    } else {
      setStatus(el.infographicStatus, "Можно запускать инфографику.");
    }

    if (infographic?.image_name || infographic?.notebook_title) {
      text(el.infographicNotebookName, infographic?.image_name || infographic?.notebook_title || "");
      const metaParts = [];
      if (infographic.profile) {
        metaParts.push(`Профиль: ${infographic.profile}`);
      }
      if (infographic.created_at) {
        metaParts.push(`Запущено: ${formatDateTime(infographic.created_at)}`);
      }
      if (infographic.summary_title) {
        metaParts.push(`Основа: ${infographic.summary_title}`);
      }
      if (infographic.photo_name) {
        metaParts.push(`Фото: ${infographic.photo_name}`);
      }
      if (infographic.logo_name) {
        metaParts.push(`Логотип: ${infographic.logo_name}`);
      }
      if (executionTime) {
        metaParts.push(executionTime);
      }
      if (infographic.stale) {
        metaParts.push("Требует пересборки");
      }
      text(el.infographicMeta, metaParts.join(" • "));
    } else {
      text(el.infographicNotebookName, "PNG инфографики еще не сохранен");
      text(el.infographicMeta, "");
    }

    clear(el.infographicOpen);
    if (infographic?.image_url) {
      el.infographicOpen.append(
        createActionLink({
          href: infographic.image_url,
          label: "Открыть PNG",
          className: "download",
        }),
      );
      setInfographicPreview(
        infographic.image_url,
        infographic?.summary_title ? `Инфографика: ${infographic.summary_title}` : "Инфографика",
      );
    } else {
      setInfographicPreview("");
    }

    renderTimeline(
      el.infographicTimelineSummary,
      el.infographicTimeline,
      timeline,
      "Инфографика пока не запускалась.",
    );
  }

  function renderSummaryCard(summaryState) {
    const day1State = summaryState?.day1 || null;
    const day2State = summaryState?.day2 || null;
    const finalState = summaryState?.summary || null;
    const finalTimeline = finalState?.timeline || null;
    const executionTime = formatExecutionTime(finalTimeline);

    text(el.summaryDay1State, describeAnalyticsState(day1State));
    text(el.summaryDay2State, describeAnalyticsState(day2State));
    text(el.summaryFinalState, describeAnalyticsState(finalState));

    const dependenciesReady = Boolean(summaryState?.dependencies_ready);
    const blockedByRunning = Boolean(day1State?.timeline?.running || day2State?.timeline?.running);
    el.summaryRunBtn.disabled = state.busy.summary || !dependenciesReady || blockedByRunning;

    if (state.busy.summary || finalTimeline?.running) {
      setStatus(
        el.summaryStatus,
        compactMessage(finalTimeline?.summary, "Собираю итоговую аналитику..."),
      );
    } else if (finalState?.stale) {
      setStatus(
        el.summaryStatus,
        "Итог устарел: пересоберите его после обновления первого или второго дня.",
        "err",
      );
    } else if (finalState?.ready) {
      setStatus(
        el.summaryStatus,
        [
          `Итоговая аналитика готова${finalState.created_at ? ` • ${formatDateTime(finalState.created_at)}` : ""}`,
          executionTime,
        ].filter(Boolean).join(" • "),
        "ok",
      );
    } else if (!dependenciesReady) {
      setStatus(el.summaryStatus, "Сначала подготовьте документы первого и второго дня.");
    } else {
      setStatus(el.summaryStatus, "Можно собирать итоговую аналитику.");
    }

    if (finalState?.document_name) {
      text(el.summaryDocName, finalState.document_name);
      const metaParts = [];
      if (finalState.period_label) {
        metaParts.push(finalState.period_label);
      }
      if (finalState.created_at) {
        metaParts.push(`Собрано: ${formatDateTime(finalState.created_at)}`);
      }
      if (executionTime) {
        metaParts.push(executionTime);
      }
      if (finalState.stale) {
        metaParts.push("Требует пересборки");
      }
      text(el.summaryMeta, metaParts.join(" • "));
    } else {
      text(el.summaryDocName, "Итоговый документ еще не сформирован");
      text(el.summaryMeta, "");
    }

    renderDownload(
      el.summaryDownload,
      "summary",
      finalState?.document_url || "",
      Boolean(finalState?.ready),
      Boolean(finalState?.stale),
    );
    renderTimeline(
      el.summaryTimelineSummary,
      el.summaryTimeline,
      finalTimeline,
      "Итоговая аналитика пока не запускалась.",
    );
  }

  function renderAnalytics() {
    renderAnalyticsCard("day1", state.analytics.day1History, state.analytics.summaryState?.day1 || null);
    renderAnalyticsCard("day2", state.analytics.day2History, state.analytics.summaryState?.day2 || null);
    renderSummaryCard(state.analytics.summaryState);
    renderInfographicCard(state.analytics.summaryState);
    syncLiveClock();
  }

  function renderProtocol() {
    const report = state.protocol.reportState;
    const timeline = report?.timeline || null;
    const executionTime = formatExecutionTime(timeline);

    if (state.busy.protocol || timeline?.running) {
      setStatus(
        el.protocolStatus,
        compactMessage(timeline?.summary, "Обрабатываю файл и собираю протокол с транскрибацией..."),
      );
    } else if (report?.ready) {
      setStatus(
        el.protocolStatus,
        [
          `Протокол и транскрибация готовы${report.created_at ? ` • ${formatDateTime(report.created_at)}` : ""}`,
          executionTime,
        ].filter(Boolean).join(" • "),
        "ok",
      );
    } else {
      setStatus(el.protocolStatus, "Загрузите запись, чтобы собрать протокол.");
    }

    if (report?.document_name) {
      text(el.protocolDocName, report.document_name);
      const metaParts = [];
      if (report.created_at) {
        metaParts.push(`Собрано: ${formatDateTime(report.created_at)}`);
      }
      if (report.chunk_count) {
        metaParts.push(`Фрагментов: ${report.chunk_count}`);
      }
      if (report.duration_seconds) {
        metaParts.push(`Длительность: ${formatDuration(report.duration_seconds)}`);
      }
      if (executionTime) {
        metaParts.push(executionTime);
      }
      text(el.protocolMeta, metaParts.join(" • "));
    } else {
      text(el.protocolDocName, "Документ еще не сформирован");
      text(el.protocolMeta, "");
    }

    text(
      el.protocolStateSource,
      report?.source_name
        ? `${report.source_name}${report.source_mime_type ? ` • ${report.source_mime_type}` : ""}`
        : "Исходный файл еще не обработан.",
    );
    text(
      el.protocolStateStrategy,
      report?.processing_strategy
        ? `${report.processing_strategy}${report.preprocessing_message ? ` • ${report.preprocessing_message}` : ""}`
        : "Стратегия появится после старта обработки.",
    );
    text(
      el.protocolStateDoc,
      report?.document_name
        ? `${report.document_name}${report.created_at ? ` • ${formatDateTime(report.created_at)}` : ""}`
        : "Итоговый документ пока отсутствует.",
    );

    renderDownloads(
      el.protocolDownload,
      "protocol",
      [
        {
          url: report?.document_url || "",
          label: "Скачать протокол",
        },
        {
          url: report?.transcript_url || "",
          label: "Скачать транскрибацию",
        },
      ],
      Boolean(report?.ready),
      false,
    );
    renderTimeline(
      el.protocolTimelineSummary,
      el.protocolTimeline,
      timeline,
      "Протокол еще не запускался.",
    );

    const hasFile = Boolean(getProtocolFile());
    el.protocolRunBtn.disabled = state.busy.protocol || !hasFile;
    syncLiveClock();
  }

  function renderVisibleView() {
    if (state.currentView === "protocol") {
      renderProtocol();
      return;
    }
    renderAnalytics();
  }

  function analyticsHasRunningTimeline() {
    const summary = state.analytics.summaryState;
    return Boolean(
      state.busy.day1 ||
        state.busy.day2 ||
        state.busy.summary ||
        state.busy.infographic ||
        summary?.day1?.timeline?.running ||
        summary?.day2?.timeline?.running ||
        summary?.summary?.timeline?.running ||
        summary?.infographic?.timeline?.running,
    );
  }

  function protocolHasRunningTimeline() {
    return Boolean(state.busy.protocol || state.protocol.reportState?.timeline?.running);
  }

  function hasRunningTimeline() {
    return analyticsHasRunningTimeline() || protocolHasRunningTimeline();
  }

  function startLiveClock() {
    if (state.pollers.liveClock) {
      return;
    }
    state.pollers.liveClock = window.setInterval(() => {
      if (!hasRunningTimeline()) {
        stopLiveClock();
        return;
      }
      renderVisibleView();
    }, 1000);
  }

  function stopLiveClock() {
    if (state.pollers.liveClock) {
      window.clearInterval(state.pollers.liveClock);
      state.pollers.liveClock = null;
    }
  }

  function syncLiveClock() {
    if (hasRunningTimeline()) {
      startLiveClock();
    } else {
      stopLiveClock();
    }
  }

  function startAnalyticsPolling() {
    if (state.pollers.analytics) {
      return;
    }
    state.pollers.analytics = window.setInterval(async () => {
      if (state.pollers.analyticsBusy) {
        return;
      }
      state.pollers.analyticsBusy = true;
      try {
        await loadAnalytics({ refreshHistory: false, silent: true });
        if (!analyticsHasRunningTimeline()) {
          stopAnalyticsPolling();
        }
      } catch (error) {
        stopAnalyticsPolling();
      } finally {
        state.pollers.analyticsBusy = false;
      }
    }, 2500);
  }

  function stopAnalyticsPolling() {
    if (state.pollers.analytics) {
      window.clearInterval(state.pollers.analytics);
      state.pollers.analytics = null;
    }
    state.pollers.analyticsBusy = false;
  }

  function startProtocolPolling() {
    if (state.pollers.protocol) {
      return;
    }
    state.pollers.protocol = window.setInterval(async () => {
      try {
        await loadProtocol({ silent: true });
        if (!protocolHasRunningTimeline()) {
          stopProtocolPolling();
        }
      } catch (error) {
        stopProtocolPolling();
      }
    }, 2500);
  }

  function stopProtocolPolling() {
    if (state.pollers.protocol) {
      window.clearInterval(state.pollers.protocol);
      state.pollers.protocol = null;
    }
  }

  async function loadAnalytics({ refreshHistory = true, silent = false } = {}) {
    try {
      const tasks = [
        fetchJson("/agents/analytics-note/summary/state"),
        refreshHistory
          ? fetchJson("/agents/analytics-note/day1/history")
          : Promise.resolve(state.analytics.day1History),
        refreshHistory
          ? fetchJson("/agents/analytics-note/day2/history")
          : Promise.resolve(state.analytics.day2History),
      ];
      const [summaryState, day1History, day2History] = await Promise.all(tasks);

      state.analytics.summaryState = summaryState;
      state.analytics.day1History = day1History;
      state.analytics.day2History = day2History;
      state.analytics.loaded = true;

      hydrateDraftValue("day1", day1History, "default_date");
      hydrateDraftValue("day2From", day2History, "date_from_default");
      hydrateDraftValue("day2To", day2History, "date_to_default");

      renderAnalytics();

      if (analyticsHasRunningTimeline()) {
        startAnalyticsPolling();
      } else {
        stopAnalyticsPolling();
      }
    } catch (error) {
      if (!silent) {
        setStatus(el.day1Status, error.message, "err");
        setStatus(el.day2Status, error.message, "err");
        setStatus(el.summaryStatus, error.message, "err");
        setStatus(el.infographicStatus, error.message, "err");
      }
      throw error;
    }
  }

  async function loadProtocol({ silent = false } = {}) {
    try {
      state.protocol.reportState = await fetchJson("/agents/protocol/state");
      state.protocol.loaded = true;
      renderProtocol();

      if (protocolHasRunningTimeline()) {
        startProtocolPolling();
      } else {
        stopProtocolPolling();
      }
    } catch (error) {
      if (!silent) {
        setStatus(el.protocolStatus, error.message, "err");
      }
      throw error;
    }
  }

  function renderAgentList() {
    clear(el.agentList);
    Object.entries(agents).forEach(([id, agent]) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = `agent-btn${state.currentView === id ? " active" : ""}`;

      const title = document.createElement("strong");
      title.textContent = agent.title;

      button.append(title);
      if (agent.description) {
        const description = document.createElement("span");
        description.textContent = agent.description;
        button.append(description);
      }
      button.addEventListener("click", () => {
        switchView(id).catch((error) => {
          if (id === "protocol") {
            setStatus(el.protocolStatus, error.message, "err");
          } else {
            setStatus(el.summaryStatus, error.message, "err");
            setStatus(el.infographicStatus, error.message, "err");
          }
        });
      });
      el.agentList.append(button);
    });
  }

  async function switchView(id) {
    state.currentView = id;
    renderAgentList();
    text(el.currentTitle, agents[id].title);
    text(el.currentDescription, agents[id].description || "");
    el.analyticsView.classList.toggle("hidden", id !== "analytics");
    el.protocolView.classList.toggle("hidden", id !== "protocol");

    if (id === "analytics" && !state.analytics.loaded) {
      await loadAnalytics();
    }
    if (id === "protocol" && !state.protocol.loaded) {
      await loadProtocol();
    }
  }

  async function runDay1() {
    const selectedDate = state.analytics.day1History?.locked
      ? state.analytics.day1History.selected_date
      : state.analytics.draftDates.day1;

    if (!selectedDate) {
      setStatus(el.day1Status, "Сначала выберите дату первого дня.", "err");
      return;
    }

    state.busy.day1 = true;
    renderAnalytics();
    startAnalyticsPolling();

    try {
      const payload = await fetchJson("/agents/analytics-note/day1/run", {
        method: "POST",
        body: JSON.stringify({ date: selectedDate }),
      });
      markGenerated("day1", true);
      markGenerated("summary", false);
      markGenerated("infographic", false);
      setStatus(
        el.day1Status,
        !payload.n8n_roundtrip_ok && payload.n8n_roundtrip_message
          ? `Документ первого дня собран. • ${payload.n8n_roundtrip_message}`
          : "Документ первого дня собран.",
        "ok",
      );
    } catch (error) {
      setStatus(el.day1Status, error.message, "err");
    } finally {
      state.busy.day1 = false;
      await loadAnalytics({ refreshHistory: true, silent: true });
      renderAnalytics();
    }
  }

  async function runDay2() {
    const selectedRange = normalizeDay2Drafts(state.analytics.day2History);
    const selectedDateFrom = selectedRange.from;
    const selectedDateTo = selectedRange.to;

    if (!selectedDateFrom) {
      setStatus(el.day2Status, "Сначала выберите дату второго дня.", "err");
      return;
    }
    if (selectedDateTo && selectedDateTo <= selectedDateFrom) {
      setStatus(el.day2Status, "Вторая дата должна быть позже первой.", "err");
      return;
    }

    state.busy.day2 = true;
    renderAnalytics();
    startAnalyticsPolling();

    try {
      const payload = await fetchJson("/agents/analytics-note/day2/run", {
        method: "POST",
        body: JSON.stringify({ date_from: selectedDateFrom, date_to: selectedDateTo || null }),
      });
      markGenerated("day2", true);
      markGenerated("summary", false);
      markGenerated("infographic", false);
      setStatus(
        el.day2Status,
        !payload.n8n_roundtrip_ok && payload.n8n_roundtrip_message
          ? `Документ второго дня собран. • ${payload.n8n_roundtrip_message}`
          : "Документ второго дня собран.",
        "ok",
      );
    } catch (error) {
      setStatus(el.day2Status, error.message, "err");
    } finally {
      state.busy.day2 = false;
      await loadAnalytics({ refreshHistory: true, silent: true });
      renderAnalytics();
    }
  }

  async function runSummary() {
    state.busy.summary = true;
    renderAnalytics();
    startAnalyticsPolling();

    try {
      const payload = await fetchJson("/agents/analytics-note/summary/run", {
        method: "POST",
        body: JSON.stringify({}),
      });
      markGenerated("summary", true);
      markGenerated("infographic", false);
      setStatus(
        el.summaryStatus,
        !payload.n8n_roundtrip_ok && payload.n8n_roundtrip_message
          ? `Итоговая аналитика собрана. • ${payload.n8n_roundtrip_message}`
          : "Итоговая аналитика собрана.",
        "ok",
      );
    } catch (error) {
      setStatus(el.summaryStatus, error.message, "err");
    } finally {
      state.busy.summary = false;
      await loadAnalytics({ refreshHistory: true, silent: true });
      renderAnalytics();
    }
  }

  async function runInfographic() {
    const googleDocUrl = getInfographicGoogleDoc();
    const photo = getInfographicPhoto();
    const logo = getInfographicLogo();

    if (!googleDocUrl) {
      setStatus(el.infographicStatus, "Укажите ссылку на Google Doc.", "err");
      return;
    }
    if (!photo || !logo) {
      setStatus(el.infographicStatus, "Загрузите общее фото и логотип.", "err");
      return;
    }

    state.busy.infographic = true;
    renderAnalytics();
    startAnalyticsPolling();

    try {
      const formData = new FormData();
      formData.append("google_doc_url", googleDocUrl);
      formData.append("photo", photo);
      formData.append("logo", logo);

      const response = await fetch("/agents/analytics-note/infographic/run", {
        method: "POST",
        body: formData,
      });
      const { payload, rawText } = await readJsonResponse(response);
      if (!response.ok) {
        throw new Error(extractApiMessage(payload, rawText, response.status));
      }

      markGenerated("infographic", true);
      setStatus(
        el.infographicStatus,
        payload.notebook_url
          ? "Инфографика запущена. Можно открыть NotebookLM."
          : "Инфографика запущена.",
        "ok",
      );
      setStatus(
        el.infographicStatus,
        payload.image_url
          ? "Инфографика сохранена. Можно открыть PNG."
          : "Инфографика собрана.",
        "ok",
      );
    } catch (error) {
      setStatus(el.infographicStatus, error.message, "err");
    } finally {
      state.busy.infographic = false;
      await loadAnalytics({ refreshHistory: true, silent: true });
      renderAnalytics();
    }
  }

  async function runProtocol() {
    const file = getProtocolFile();
    if (!file) {
      setStatus(el.protocolStatus, "Выберите файл записи перед запуском.", "err");
      return;
    }

    state.busy.protocol = true;
    renderProtocol();
    startProtocolPolling();

    try {
      const formData = new FormData();
      formData.append("file", file);
      const response = await fetch("/agents/protocol/run", {
        method: "POST",
        body: formData,
      });
      const { payload, rawText } = await readJsonResponse(response);
      if (!response.ok) {
        throw new Error(extractApiMessage(payload, rawText, response.status));
      }

      markGenerated("protocol", true);
      setStatus(
        el.protocolStatus,
        !payload.n8n_roundtrip_ok && payload.n8n_roundtrip_message
          ? `Протокол и транскрибация собраны. • ${payload.n8n_roundtrip_message}`
          : "Протокол и транскрибация собраны.",
        "ok",
      );
    } catch (error) {
      setStatus(el.protocolStatus, error.message, "err");
    } finally {
      state.busy.protocol = false;
      await loadProtocol({ silent: true });
      renderProtocol();
    }
  }

  async function resetCurrentView() {
    const agent = agents[state.currentView];
    if (!agent) {
      return;
    }

    try {
      await fetchJson(agent.resetEndpoint, {
        method: "POST",
        body: JSON.stringify({}),
      });

      if (state.currentView === "analytics") {
        clearGenerated(["day1", "day2", "summary", "infographic"]);
        state.analytics.draftDates.day1 = "";
        state.analytics.draftDates.day2From = "";
        state.analytics.draftDates.day2To = "";
        state.analytics.infographicDraft.googleDocUrl = "";
        setInfographicPhoto(null);
        setInfographicLogo(null);
        if (el.infographicGoogleDoc) {
          el.infographicGoogleDoc.value = "";
        }
        if (el.infographicPhoto) {
          el.infographicPhoto.value = "";
        }
        if (el.infographicLogo) {
          el.infographicLogo.value = "";
        }
        stopAnalyticsPolling();
        await loadAnalytics({ refreshHistory: true, silent: true });
        setStatus(el.day1Status, "Состояние первого дня очищено.");
        setStatus(el.day2Status, "Состояние второго дня очищено.");
        setStatus(el.summaryStatus, "Итоговая аналитика очищена.");
        setStatus(el.infographicStatus, "Инфографика очищена.");
      } else {
        clearGenerated(["protocol"]);
        stopProtocolPolling();
        state.protocol.pendingFile = null;
        if (el.protocolFile) {
          el.protocolFile.value = "";
        }
        updateProtocolFileInfo();
        await loadProtocol({ silent: true });
        setStatus(el.protocolStatus, "Состояние протокола очищено.");
      }
    } catch (error) {
      if (state.currentView === "analytics") {
        setStatus(el.summaryStatus, error.message, "err");
        setStatus(el.infographicStatus, error.message, "err");
      } else {
        setStatus(el.protocolStatus, error.message, "err");
      }
    }
  }

  function updateProtocolFileInfo() {
    const file = getProtocolFile();
    if (!file) {
      text(el.protocolFileInfo, "Файл пока не выбран.");
      el.protocolRunBtn.disabled = state.busy.protocol;
      return;
    }
    text(
      el.protocolFileInfo,
      `${file.name} • ${formatBytes(file.size)} • ${file.type || "тип не определен"}`,
    );
    el.protocolRunBtn.disabled = state.busy.protocol;
  }

  function getProtocolFile() {
    return state.protocol.pendingFile;
  }

  function setProtocolFile(file) {
    state.protocol.pendingFile = file || null;
  }

  function protocolDropzone() {
    return el.protocolFile?.closest(".field") || null;
  }

  function ensureProtocolDropzone() {
    const dropzone = protocolDropzone();
    if (!dropzone) {
      return;
    }
    dropzone.classList.add("dropzone");

    if (!document.getElementById("protocol-dropzone-style")) {
      const style = document.createElement("style");
      style.id = "protocol-dropzone-style";
      style.textContent = `
        .dropzone {
          padding: 16px;
          border: 1.5px dashed rgba(20, 87, 61, 0.28);
          border-radius: 18px;
          background: linear-gradient(180deg, rgba(247, 251, 248, 0.94) 0%, rgba(255, 255, 255, 0.96) 100%);
          transition: border-color .16s ease, background .16s ease, box-shadow .16s ease;
        }
        .dropzone.dragover {
          border-color: var(--accent);
          background: linear-gradient(180deg, rgba(228, 241, 234, 0.96) 0%, rgba(255, 255, 255, 0.98) 100%);
          box-shadow: 0 0 0 4px rgba(20, 87, 61, 0.08);
        }
        .dropzone-title {
          display: block;
          font-size: 15px;
          font-weight: 800;
          color: var(--text);
        }
        .dropzone-note {
          display: block;
          margin-top: 4px;
          color: var(--muted);
          font-size: 13px;
          line-height: 1.45;
        }
        .dropzone input[type=file] {
          margin-top: 10px;
        }
      `;
      document.head.append(style);
    }

    if (!dropzone.querySelector(".dropzone-title")) {
      const title = document.createElement("strong");
      title.className = "dropzone-title";
      title.textContent = "Перетащите аудио или видео сюда";
      dropzone.insertBefore(title, el.protocolFile);
    }

    if (!dropzone.querySelector(".dropzone-note")) {
      const note = document.createElement("small");
      note.className = "dropzone-note";
      note.textContent = "Можно перетащить файл мышкой или выбрать его вручную.";
      dropzone.insertBefore(note, el.protocolFile);
    }
  }

  function updateProtocolIntro() {
    const cardNote = document.querySelector("#protocolView .protocol-card-head .protocol-note");
    if (cardNote) {
      cardNote.textContent = "Загрузите аудио или видео встречи, чтобы сформировать протокол.";
    }
  }

  function bindEvents() {
    el.day1Date.addEventListener("change", (event) => {
      state.analytics.draftDates.day1 = event.target.value;
      renderAnalytics();
    });
    el.day2DateFrom.addEventListener("change", (event) => {
      state.analytics.draftDates.day2From = event.target.value;
      if (
        state.analytics.draftDates.day2To &&
        state.analytics.draftDates.day2To <= event.target.value
      ) {
        state.analytics.draftDates.day2To = "";
      }
      renderAnalytics();
    });
    el.day2DateTo.addEventListener("change", (event) => {
      state.analytics.draftDates.day2To = event.target.value;
      renderAnalytics();
    });
    el.day1RunBtn.addEventListener("click", () => {
      runDay1().catch((error) => setStatus(el.day1Status, error.message, "err"));
    });
    el.day2RunBtn.addEventListener("click", () => {
      runDay2().catch((error) => setStatus(el.day2Status, error.message, "err"));
    });
    el.summaryRunBtn.addEventListener("click", () => {
      runSummary().catch((error) => setStatus(el.summaryStatus, error.message, "err"));
    });
    el.infographicGoogleDoc.addEventListener("input", (event) => {
      state.analytics.infographicDraft.googleDocUrl = event.target.value;
      renderAnalytics();
    });
    el.infographicPhoto.addEventListener("change", () => {
      setInfographicPhoto(el.infographicPhoto?.files?.[0] || null);
      updateInfographicFileInfo();
      renderAnalytics();
    });
    el.infographicLogo.addEventListener("change", () => {
      setInfographicLogo(el.infographicLogo?.files?.[0] || null);
      updateInfographicFileInfo();
      renderAnalytics();
    });
    el.infographicRunBtn.addEventListener("click", () => {
      runInfographic().catch((error) => setStatus(el.infographicStatus, error.message, "err"));
    });
    el.protocolRunBtn.addEventListener("click", () => {
      runProtocol().catch((error) => setStatus(el.protocolStatus, error.message, "err"));
    });
    el.protocolFile.addEventListener("change", () => {
      setProtocolFile(el.protocolFile?.files?.[0] || null);
      updateProtocolFileInfo();
      renderProtocol();
    });
    const dropzone = protocolDropzone();
    if (dropzone) {
      ["dragenter", "dragover"].forEach((eventName) => {
        dropzone.addEventListener(eventName, (event) => {
          event.preventDefault();
          dropzone.classList.add("dragover");
        });
      });
      ["dragleave", "dragend", "drop"].forEach((eventName) => {
        dropzone.addEventListener(eventName, (event) => {
          event.preventDefault();
          dropzone.classList.remove("dragover");
        });
      });
      dropzone.addEventListener("drop", (event) => {
        const file = event.dataTransfer?.files?.[0] || null;
        if (!file) {
          return;
        }
        setProtocolFile(file);
        if (el.protocolFile) {
          el.protocolFile.value = "";
        }
        updateProtocolFileInfo();
        renderProtocol();
      });
    }
    el.resetBtn.addEventListener("click", () => {
      resetCurrentView();
    });
  }

  async function hydrateAgentViews() {
    try {
      agents = await loadAgentViews();
    } catch (error) {
      agents = defaultAgentViews();
    }
  }

  async function init() {
    await hydrateAgentViews();
    ensureProtocolDropzone();
    updateProtocolIntro();
    bindEvents();
    renderAgentList();
    updateInfographicFileInfo();
    updateProtocolFileInfo();
    await switchView("analytics");
  }

  init().catch((error) => {
    setStatus(el.day1Status, error.message, "err");
    setStatus(el.day2Status, error.message, "err");
    setStatus(el.summaryStatus, error.message, "err");
    setStatus(el.infographicStatus, error.message, "err");
  });
})();


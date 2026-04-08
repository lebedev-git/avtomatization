export function rawSnippet(raw) {
  const value = String(raw || "").trim();
  if (!value) {
    return "";
  }
  return value.length > 220 ? `${value.slice(0, 220)}...` : value;
}

export async function readJsonResponse(response) {
  const rawText = await response.text();
  const trimmed = rawText.trim();
  let payload = {};

  if (trimmed) {
    try {
      payload = JSON.parse(trimmed);
    } catch (error) {
      payload = { rawText: trimmed };
    }
  }

  return {
    rawText,
    payload,
  };
}

export function extractApiMessage(payload, fallbackText, status) {
  if (payload && typeof payload === "object") {
    if (typeof payload.detail === "string" && payload.detail.trim()) {
      return payload.detail.trim();
    }
    if (Array.isArray(payload.detail) && payload.detail.length) {
      return payload.detail
        .map((item) => {
          if (typeof item === "string") {
            return item;
          }
          if (item && typeof item === "object") {
            return item.msg || JSON.stringify(item);
          }
          return String(item);
        })
        .join("; ");
    }
    if (typeof payload.message === "string" && payload.message.trim()) {
      return payload.message.trim();
    }
    if (typeof payload.error === "string" && payload.error.trim()) {
      return payload.error.trim();
    }
  }

  const snippet = rawSnippet(fallbackText);
  if (snippet) {
    return snippet;
  }
  return `Ошибка запроса (${status}).`;
}

export async function fetchJson(url, options = {}) {
  const headers = new Headers(options.headers || {});
  headers.set("Accept", "application/json");
  const hasJsonBody = typeof options.body === "string" && !headers.has("Content-Type");
  if (hasJsonBody) {
    headers.set("Content-Type", "application/json");
  }

  const response = await fetch(url, {
    ...options,
    headers,
  });
  const { payload, rawText } = await readJsonResponse(response);

  if (!response.ok) {
    const message = extractApiMessage(payload, rawText, response.status);
    const err = new Error(message);
    err.status = response.status;
    err.payload = payload;
    throw err;
  }

  if (payload && typeof payload === "object" && !Array.isArray(payload)) {
    return payload;
  }

  if (rawText.trim()) {
    throw new Error(`Сервер вернул не JSON: ${rawSnippet(rawText)}`);
  }

  return {};
}

from __future__ import annotations

from typing import Any


def build_fetch_workflow_payload(
    *,
    existing_workflow: dict[str, Any],
    day1_entry_survey_id: str,
    day1_exit_survey_id: str,
    day2_survey_id: str,
    day1_entry_form_url: str,
    day1_exit_form_url: str,
    day2_form_url: str,
) -> dict[str, Any]:
    existing_nodes = existing_workflow.get("nodes") or []
    webhook_node = next(
        (node for node in existing_nodes if node.get("type") == "n8n-nodes-base.webhook"),
        None,
    )
    fetch_node = next(
        (node for node in existing_nodes if node.get("type") == "n8n-nodes-base.httpRequest"),
        None,
    )
    if webhook_node is None or fetch_node is None:
        raise RuntimeError("Не удалось найти webhook или httpRequest ноду в текущем workflow n8n.")

    webhook_parameters = dict(webhook_node.get("parameters") or {})
    webhook_parameters["path"] = webhook_parameters.get("path") or webhook_node.get("webhookId")
    webhook_parameters["responseMode"] = "responseNode"
    webhook_parameters["httpMethod"] = webhook_parameters.get("httpMethod") or "GET"
    webhook_parameters["options"] = webhook_parameters.get("options") or {}

    fetch_credentials = fetch_node.get("credentials")
    if not fetch_credentials:
        raise RuntimeError("В workflow n8n не найдены креденшелы доступа к Yandex Forms.")

    page_size = 100

    determine_block_code = f"""const rawBlock = String($json.query?.block ?? $json.body?.block ?? 'day2').trim().toLowerCase();
const knownBlocks = {{
  day1: {{
    block: 'day1',
    blockName: 'Первый день',
    forms: [
      {{
        formId: 'day1-entry',
        formName: 'Входная анкета первого дня',
        formUrl: '{day1_entry_form_url}',
        surveyId: '{day1_entry_survey_id}',
      }},
      {{
        formId: 'day1-exit',
        formName: 'Выходная анкета первого дня',
        formUrl: '{day1_exit_form_url}',
        surveyId: '{day1_exit_survey_id}',
      }},
    ],
  }},
  day2: {{
    block: 'day2',
    blockName: 'Второй день',
    forms: [
      {{
        formId: 'day2-main',
        formName: 'Анкета второго дня',
        formUrl: '{day2_form_url}',
        surveyId: '{day2_survey_id}',
      }},
    ],
  }},
}};

const selected = knownBlocks[rawBlock] ?? knownBlocks.day2;
return [{{
  json: {{
    block: selected.block,
    blockName: selected.blockName,
    forms: selected.forms,
  }}
}}];"""

    init_pagination_code = f"""const forms = Array.isArray($json.forms) ? $json.forms : [];
if (!forms.length) {{
  throw new Error('Не передан ни один источник анкеты для выгрузки.');
}}
const runKey = `${{Date.now()}}_${{Math.random().toString(16).slice(2)}}`;
const store = $getWorkflowStaticData('global');
store.multiFormRuns ??= {{}};
store.multiFormRuns[runKey] = {{
  block: $json.block,
  blockName: $json.blockName,
  pageSize: {page_size},
  currentFormIndex: 0,
  forms: forms.map((form) => ({{
    ...form,
    columns: [],
    answers: [],
  }})),
}};
const currentForm = store.multiFormRuns[runKey].forms[0];
return [{{
  json: {{
    runKey,
    pageUrl: `https://api.forms.yandex.net/v1/surveys/${{currentForm.surveyId}}/answers?page_size={page_size}`,
  }}
}}];"""

    collect_page_code = """const initData = $('Подготовить пагинацию').first().json;
const runKey = initData.runKey;
const store = $getWorkflowStaticData('global');
store.multiFormRuns ??= {};
const state = store.multiFormRuns[runKey];
if (!state) {
  throw new Error('Не найдено состояние пагинации в workflow static data.');
}
const currentForm = state.forms[state.currentFormIndex];
if (!currentForm) {
  throw new Error('Не найдена активная анкета для текущего шага пагинации.');
}
const page = $json ?? {};
if (!Array.isArray(currentForm.columns) || currentForm.columns.length === 0) {
  currentForm.columns = Array.isArray(page.columns) ? page.columns : [];
}
if (!Array.isArray(currentForm.answers)) {
  currentForm.answers = [];
}
if (Array.isArray(page.answers) && page.answers.length) {
  currentForm.answers.push(...page.answers);
}
state.forms[state.currentFormIndex] = currentForm;
store.multiFormRuns[runKey] = state;
const rawNextPath = page.next?.next_url ?? '';
const nextPath = rawNextPath ? rawNextPath.replace(/^\\/v3\\//, '/v1/') : '';
return [{
  json: {
    runKey,
    pageUrl: nextPath ? `https://api.forms.yandex.net${nextPath}` : '',
  }
}];"""

    prepare_next_form_code = f"""const initData = $('Подготовить пагинацию').first().json;
const runKey = $json.runKey ?? initData.runKey;
const store = $getWorkflowStaticData('global');
store.multiFormRuns ??= {{}};
const state = store.multiFormRuns[runKey];
if (!state) {{
  throw new Error('Не найдено состояние форм в workflow static data.');
}}

if (state.currentFormIndex < state.forms.length - 1) {{
  state.currentFormIndex += 1;
  const nextForm = state.forms[state.currentFormIndex];
  store.multiFormRuns[runKey] = state;
  return [{{
    json: {{
      runKey,
      hasNextForm: true,
      pageUrl: `https://api.forms.yandex.net/v1/surveys/${{nextForm.surveyId}}/answers?page_size={page_size}`,
    }}
  }}];
}}

return [{{
  json: {{
    runKey,
    hasNextForm: false,
    block: state.block,
    blockName: state.blockName,
    forms: state.forms.map((form) => ({{
      formId: form.formId,
      formName: form.formName,
      formUrl: form.formUrl,
      surveyId: form.surveyId,
      columns: form.columns ?? [],
      answers: form.answers ?? [],
    }})),
  }}
}}];"""

    normalize_code = """const store = $getWorkflowStaticData('global');
store.multiFormRuns ??= {};
const runKey = $json.runKey;
const forms = Array.isArray($json.forms) ? $json.forms : [];

function normalizeValue(value) {
  if (Array.isArray(value)) {
    if (value.length === 1) return value[0];
    return value;
  }
  return value ?? null;
}

const normalizedForms = forms.map((form) => {
  const columns = Array.isArray(form.columns) ? form.columns : [];
  const answers = Array.isArray(form.answers) ? form.answers : [];
  const items = answers.map((answer) => ({
    surveyId: form.surveyId,
    answerId: answer.id,
    created: answer.created,
    answers: columns.map((column, index) => ({
      order: index + 1,
      question: column.text,
      slug: column.slug,
      type: column.type,
      rows: column.rows ?? [],
      value: normalizeValue(answer.data?.[index]?.value),
    })),
  }));
  return {
    formId: form.formId,
    formName: form.formName,
    formUrl: form.formUrl,
    surveyId: form.surveyId,
    totalAnswers: items.length,
    items,
  };
});

if (runKey && store.multiFormRuns[runKey]) {
  delete store.multiFormRuns[runKey];
}

return [{
  json: {
    block: $json.block,
    blockName: $json.blockName,
    forms: normalizedForms,
  }
}];"""

    build_payload_code = """const forms = Array.isArray($json.forms) ? $json.forms : [];
return [{
  json: {
    block: $json.block,
    blockName: $json.blockName,
    fetchedAt: new Date().toISOString(),
    totalAnswers: forms.reduce((sum, form) => sum + Number(form.totalAnswers ?? 0), 0),
    forms,
  }
}];"""

    intake_receipt_code = """const payload = $json ?? {};
const kind = payload.agentId === 'protocol'
  ? 'protocol'
  : (payload.blockId ? `analytics-${payload.blockId}` : 'unknown');
const documentBase64 = typeof payload.documentBase64 === 'string' ? payload.documentBase64 : '';
return [{
  json: {
    ok: true,
    workflow: 'analytics-and-protocol',
    kind,
    receivedAt: new Date().toISOString(),
    title: payload.title ?? null,
    blockId: payload.blockId ?? null,
    agentId: payload.agentId ?? null,
    sourceName: payload.sourceName ?? null,
    periodLabel: payload.periodLabel ?? null,
    documentName: payload.documentName ?? null,
    hasDocument: Boolean(documentBase64),
    textLength: typeof payload.reportText === 'string' ? payload.reportText.length : 0,
  }
}];"""

    raw_settings = existing_workflow.get("settings") or {}
    sanitized_settings = {
        key: value
        for key, value in raw_settings.items()
        if key in {"callerPolicy", "availableInMCP"}
    }

    return {
        "name": "Аналитика и протокол: единый fetch и intake workflow",
        "nodes": [
            {
                "parameters": {
                    "content": "## 1. Точка входа\nWorkflow стартует вручную или по webhook от FastAPI.",
                    "height": 140,
                    "width": 280,
                    "color": 5,
                },
                "id": "2f4a1f61-9f4d-4ab8-8b1a-0a2254d0f110",
                "name": "Этап 1",
                "type": "n8n-nodes-base.stickyNote",
                "typeVersion": 1,
                "position": [-1120, -140],
            },
            {
                "parameters": {
                    "content": "## 2. Выбор блока\nОпределяем, нужен первый день или второй день, и подставляем нужные анкеты.",
                    "height": 170,
                    "width": 320,
                    "color": 6,
                },
                "id": "3e0af0ad-2d75-42f1-a84d-56a7f9a4c111",
                "name": "Этап 2",
                "type": "n8n-nodes-base.stickyNote",
                "typeVersion": 1,
                "position": [-760, -180],
            },
            {
                "parameters": {
                    "content": "## 3. Выгрузка и пагинация\nДля каждой анкеты тянем все страницы ответов и складываем их во временное состояние workflow.",
                    "height": 190,
                    "width": 340,
                    "color": 7,
                },
                "id": "4af2d5c2-b4c0-43eb-9fd5-e0db9d47f112",
                "name": "Этап 3",
                "type": "n8n-nodes-base.stickyNote",
                "typeVersion": 1,
                "position": [40, -210],
            },
            {
                "parameters": {
                    "content": "## 4. Нормализация\nСобираем единый JSON по формам и возвращаем его обратно в FastAPI.",
                    "height": 160,
                    "width": 320,
                    "color": 4,
                },
                "id": "5bb03e4f-5ee2-4971-8d47-987cf0f2f113",
                "name": "Этап 4",
                "type": "n8n-nodes-base.stickyNote",
                "typeVersion": 1,
                "position": [1560, 80],
            },
            {
                "parameters": {},
                "id": "0f4dd83d-1e9c-445a-8f04-bd5b8af4f001",
                "name": "Ручной запуск",
                "type": "n8n-nodes-base.manualTrigger",
                "typeVersion": 1,
                "position": [-1040, 40],
            },
            {
                "parameters": webhook_parameters,
                "id": "d5bb5649-a5b8-4f4d-b903-f6919fc1f004",
                "name": "Вебхук запуска",
                "type": "n8n-nodes-base.webhook",
                "typeVersion": 2.1,
                "position": [-1040, 220],
                "webhookId": webhook_node.get("webhookId") or webhook_parameters["path"],
            },
            {
                "parameters": {"jsCode": determine_block_code},
                "id": "31c503e1-b7fe-4f6b-9e03-0e4ac9472202",
                "name": "Определить блок и анкеты",
                "type": "n8n-nodes-base.code",
                "typeVersion": 2,
                "position": [-720, 130],
            },
            {
                "parameters": {"jsCode": init_pagination_code},
                "id": "b92a6e15-8e2d-46fe-a38f-1ebcfd10a100",
                "name": "Подготовить пагинацию",
                "type": "n8n-nodes-base.code",
                "typeVersion": 2,
                "position": [-400, 130],
            },
            {
                "parameters": {
                    "url": "={{ $json.pageUrl }}",
                    "authentication": "genericCredentialType",
                    "genericAuthType": "httpHeaderAuth",
                    "sendHeaders": True,
                    "headerParameters": {
                        "parameters": [{"name": "Accept", "value": "application/json"}]
                    },
                    "options": {},
                },
                "id": "f7ae6267-efda-4e01-8f69-62d42a814002",
                "name": "Получить ответы текущей анкеты",
                "type": "n8n-nodes-base.httpRequest",
                "typeVersion": 4.2,
                "position": [-80, 130],
                "credentials": fetch_credentials,
            },
            {
                "parameters": {"jsCode": collect_page_code},
                "id": "67d85d82-a3d9-4b16-8c62-9c3e0afb0100",
                "name": "Собрать страницу ответов",
                "type": "n8n-nodes-base.code",
                "typeVersion": 2,
                "position": [240, 130],
            },
            {
                "parameters": {
                    "conditions": {
                        "options": {
                            "caseSensitive": True,
                            "leftValue": "",
                            "typeValidation": "strict",
                            "version": 2,
                        },
                        "conditions": [
                            {
                                "id": "aeb98d34-6f5b-4ba7-96a2-311db84f0a11",
                                "leftValue": "={{ $json.pageUrl }}",
                                "rightValue": "",
                                "operator": {
                                    "type": "string",
                                    "operation": "notEmpty",
                                    "singleValue": True,
                                },
                            }
                        ],
                        "combinator": "and",
                    },
                    "options": {},
                },
                "id": "4d2c9be0-f67d-4e53-a54b-42a6df7d0100",
                "name": "Есть следующая страница?",
                "type": "n8n-nodes-base.if",
                "typeVersion": 2.2,
                "position": [560, 130],
            },
            {
                "parameters": {"jsCode": prepare_next_form_code},
                "id": "0ceaf760-66a6-4f87-8f80-d7a38e182203",
                "name": "Переключить на следующую анкету",
                "type": "n8n-nodes-base.code",
                "typeVersion": 2,
                "position": [880, 260],
            },
            {
                "parameters": {
                    "conditions": {
                        "options": {
                            "caseSensitive": True,
                            "leftValue": "",
                            "typeValidation": "strict",
                            "version": 2,
                        },
                        "conditions": [
                            {
                                "id": "fd3a8707-8a47-4527-b146-f0aa7f892204",
                                "leftValue": "={{ $json.hasNextForm }}",
                                "rightValue": True,
                                "operator": {
                                    "type": "boolean",
                                    "operation": "true",
                                    "singleValue": True,
                                },
                            }
                        ],
                        "combinator": "and",
                    },
                    "options": {},
                },
                "id": "5e287d48-b54d-4776-94f9-63f9f2522205",
                "name": "Есть следующая анкета?",
                "type": "n8n-nodes-base.if",
                "typeVersion": 2.2,
                "position": [1200, 260],
            },
            {
                "parameters": {"jsCode": normalize_code},
                "id": "f9d58f0c-fd7e-46d1-9f89-e78065d95003",
                "name": "Нормализовать ответы",
                "type": "n8n-nodes-base.code",
                "typeVersion": 2,
                "position": [1520, 390],
            },
            {
                "parameters": {"jsCode": build_payload_code},
                "id": "4cb210c7-e388-4b22-883d-845b8795b004",
                "name": "Собрать JSON для FastAPI",
                "type": "n8n-nodes-base.code",
                "typeVersion": 2,
                "position": [1840, 390],
            },
            {
                "parameters": {"options": {}},
                "id": "7436537d-5bc3-4946-bf3e-6ff4284cf004",
                "name": "Вернуть JSON в FastAPI",
                "type": "n8n-nodes-base.respondToWebhook",
                "typeVersion": 1.4,
                "position": [2160, 390],
            },
            {
                "parameters": {
                    "content": "## 5. Прием готовых документов\nЭта ветка принимает markdown/docx-пакеты от аналитики и протокола в тот же workflow.",
                    "height": 170,
                    "width": 320,
                    "color": 3,
                },
                "id": "6c3e4e8d-5ad3-44e3-8dc6-5d9fce9c6114",
                "name": "Этап 5",
                "type": "n8n-nodes-base.stickyNote",
                "typeVersion": 1,
                "position": [-520, 560],
            },
            {
                "parameters": {
                    "httpMethod": "POST",
                    "path": "codex-analytics-intake",
                    "responseMode": "responseNode",
                    "options": {},
                },
                "id": "228c9886-e8c5-45d3-93dd-abe5ee237b15",
                "name": "Вебхук intake документов",
                "type": "n8n-nodes-base.webhook",
                "typeVersion": 2.1,
                "position": [-400, 760],
                "webhookId": "codex-analytics-intake",
            },
            {
                "parameters": {"jsCode": intake_receipt_code},
                "id": "18120034-8580-4103-958d-53137560da43",
                "name": "Подтвердить прием аналитики и протокола",
                "type": "n8n-nodes-base.code",
                "typeVersion": 2,
                "position": [-60, 760],
            },
            {
                "parameters": {"options": {}},
                "id": "c7f837f4-03be-4c00-9b8c-9f5f95ab2665",
                "name": "Вернуть receipt intake",
                "type": "n8n-nodes-base.respondToWebhook",
                "typeVersion": 1.4,
                "position": [260, 760],
            },
        ],
        "connections": {
            "Ручной запуск": {
                "main": [[{"node": "Определить блок и анкеты", "type": "main", "index": 0}]]
            },
            "Вебхук запуска": {
                "main": [[{"node": "Определить блок и анкеты", "type": "main", "index": 0}]]
            },
            "Определить блок и анкеты": {
                "main": [[{"node": "Подготовить пагинацию", "type": "main", "index": 0}]]
            },
            "Подготовить пагинацию": {
                "main": [[{"node": "Получить ответы текущей анкеты", "type": "main", "index": 0}]]
            },
            "Получить ответы текущей анкеты": {
                "main": [[{"node": "Собрать страницу ответов", "type": "main", "index": 0}]]
            },
            "Собрать страницу ответов": {
                "main": [[{"node": "Есть следующая страница?", "type": "main", "index": 0}]]
            },
            "Есть следующая страница?": {
                "main": [
                    [{"node": "Получить ответы текущей анкеты", "type": "main", "index": 0}],
                    [{"node": "Переключить на следующую анкету", "type": "main", "index": 0}],
                ]
            },
            "Переключить на следующую анкету": {
                "main": [[{"node": "Есть следующая анкета?", "type": "main", "index": 0}]]
            },
            "Есть следующая анкета?": {
                "main": [
                    [{"node": "Получить ответы текущей анкеты", "type": "main", "index": 0}],
                    [{"node": "Нормализовать ответы", "type": "main", "index": 0}],
                ]
            },
            "Нормализовать ответы": {
                "main": [[{"node": "Собрать JSON для FastAPI", "type": "main", "index": 0}]]
            },
            "Собрать JSON для FastAPI": {
                "main": [[{"node": "Вернуть JSON в FastAPI", "type": "main", "index": 0}]]
            },
            "Вебхук intake документов": {
                "main": [[{"node": "Подтвердить прием аналитики и протокола", "type": "main", "index": 0}]]
            },
            "Подтвердить прием аналитики и протокола": {
                "main": [[{"node": "Вернуть receipt intake", "type": "main", "index": 0}]]
            },
        },
        "settings": sanitized_settings,
    }

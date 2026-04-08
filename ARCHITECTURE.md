# Архитектура проекта

## Что решает проект

Проект автоматизирует подготовку аналитических документов по ответам из Яндекс.Форм и делит процесс на четыре самостоятельных блока:

1. `Первый день`
   Берет входную и выходную анкеты первого дня и собирает одну аналитическую записку.
2. `Второй день`
   Берет анкету второго дня и строит отдельную записку по одной дате или диапазону дат.
3. `Общая итоговая аналитика`
   Собирается только из двух уже готовых текстовых записок первого и второго дня.
4. `Инфографика`
   Активируется только после актуальной итоговой аналитики, принимает ссылку на Google Doc, общее фото и логотип, создает отдельный блокнот NotebookLM и запускает там инфографику.

Пользовательский сценарий такой:

1. Пользователь открывает интерфейс аналитики.
2. Отдельно запускает `Первый день` и `Второй день`.
3. После готовности обеих частей запускает `Общую итоговую аналитику`.
4. После готовности итоговой аналитики вставляет ссылку на Google Doc, загружает фото и логотип и запускает `Инфографику`.
5. Для текстовых блоков получает `.docx`, а для инфографики получает ссылку на созданный NotebookLM notebook.

## Основные компоненты

### 1. FastAPI-приложение

Файлы:

- [main.py](/C:/DISK D/Автоматизация/gemini_proxy/main.py)
- [analytics_multi_agent.py](/C:/DISK D/Автоматизация/gemini_proxy/analytics_multi_agent.py)
- [analytics_n8n_workflow.py](/C:/DISK D/Автоматизация/gemini_proxy/analytics_n8n_workflow.py)
- [schemas.py](/C:/DISK D/Автоматизация/gemini_proxy/schemas.py)

Роль:

- отдает основной интерфейс и админ-панель;
- хранит конфиг блоков аналитики;
- получает свежие данные из `n8n`;
- формирует тексты записок через Gemini;
- собирает итоговые `.docx`;
- создает отдельный NotebookLM notebook для инфографики;
- хранит последние готовые артефакты первого, второго, итогового и infographic-блока.

### 2. Пользовательский интерфейс

Файлы:

- [web_playground.html](/C:/DISK D/Автоматизация/gemini_proxy/web_playground.html)
- [web_playground.js](/C:/DISK D/Автоматизация/gemini_proxy/web_playground.js)
- [admin_panel.html](/C:/DISK D/Автоматизация/gemini_proxy/admin_panel.html)
- [main.js](/C:/DISK D/Автоматизация/gemini_proxy/ui/admin/main.js)

Роль:

- основной экран показывает четыре секции: `Первый день`, `Второй день`, `Общая итоговая аналитика`, `Инфографика`;
- для первого дня дает выбрать одну дату и показывает объем входных и выходных анкет;
- для второго дня дает выбрать первую дату и, при необходимости, вторую дату строго позже первой;
- итоговый блок показывает готовность зависимостей и разблокируется только после двух базовых записок;
- блок инфографики разблокируется только после актуальной итоговой аналитики;
- админ-панель показывает используемые формы и позволяет редактировать четыре промпта аналитики и два промпта протокола.

### 3. Workflow в n8n

Файл-конструктор:

- [analytics_n8n_workflow.py](/C:/DISK D/Автоматизация/gemini_proxy/analytics_n8n_workflow.py)

Рабочая схема в `n8n` обслуживает два сценария выгрузки исходных анкет:

- `block=day1`: workflow последовательно выгружает две формы, входную и выходную, и возвращает единый JSON;
- `block=day2`: workflow выгружает форму второго дня и возвращает JSON по ней.

`n8n` не строит итоговую аналитику и не строит инфографику. Эти шаги выполняются внутри приложения.

### 4. Gemini и NotebookLM

Используются три режима:

- основной текстовый генератор: прямой Gemini-клиент через [service.py](/C:/DISK D/Автоматизация/gemini_proxy/service.py);
- резервный генератор: браузерный runner через [web_runner.py](/C:/DISK D/Автоматизация/gemini_proxy/web_runner.py);
- блок инфографики: NotebookLM через [notebooklm_service.py](/C:/DISK D/Автоматизация/gemini_proxy/notebooklm_service.py).

Для инфографики приложение:

1. Проверяет, что итоговая аналитика актуальна.
2. Сохраняет загруженные фото и логотип в каталог запуска.
3. Получает краткие описания этих изображений через Gemini.
4. Создает отдельный NotebookLM notebook.
5. Добавляет в него Google Doc, итоговую аналитику и текстовые описания изображений.
6. Запускает artifact типа `infographic`.

## Поток данных

### Шаг 1. Выбор блока

FastAPI запускает один из сценариев:

- `POST /agents/analytics-note/day1/run`
- `POST /agents/analytics-note/day2/run`
- `POST /agents/analytics-note/summary/run`
- `POST /agents/analytics-note/infographic/run`

### Шаг 2. Получение источников

Для `Первого дня` и `Второго дня` FastAPI запрашивает `n8n` по одному webhook URL, но с разным `block`.

Если `n8n` временно недоступен, используется локальный кеш:

- [downloads/n8n-inbox](/C:/DISK D/Автоматизация/downloads/n8n-inbox)

### Шаг 3. Фильтрация по датам

FastAPI:

- переводит дату ответа в локальную таймзону проекта;
- по первому дню фильтрует две формы по одной общей дате;
- по второму дню фильтрует одну форму по одной дате или диапазону;
- не допускает вторую дату равной или более ранней, чем первая;
- после генерации фиксирует выбранные даты до ручного reset.

### Шаг 4. Генерация аналитических текстов

Для каждого текстового блока собирается свой prompt:

- первый день: объединение входной и выходной анкеты;
- второй день: анализ ответов второго дня;
- итоговая аналитика: синтез двух уже готовых текстовых записок.

Артефакты запуска сохраняются в папки:

- [downloads/analytics-reports/day1](/C:/DISK D/Автоматизация/downloads/analytics-reports/day1)
- [downloads/analytics-reports/day2](/C:/DISK D/Автоматизация/downloads/analytics-reports/day2)
- [downloads/analytics-reports/summary](/C:/DISK D/Автоматизация/downloads/analytics-reports/summary)
- [downloads/analytics-reports/infographic](/C:/DISK D/Автоматизация/downloads/analytics-reports/infographic)

Внутри каждого запуска сохраняются служебные файлы, prompt, исходные данные и результат.

### Шаг 5. Управление актуальностью

- `summary/latest.json` считается актуальным только если совпадают `day1ReportCreatedAt` и `day2ReportCreatedAt`.
- `infographic/latest.json` считается актуальным только если совпадает `summaryReportCreatedAt`.
- Если пересобран первый или второй день, summary и infographic становятся stale.
- Если пересобран summary, infographic становится stale.

### Шаг 6. Возврат результата

FastAPI возвращает:

- `document_url` и `document_name` для текстовых блоков;
- статус генерации;
- статус обратной отправки в `n8n`, если настроен intake webhook;
- `notebook_url`, `notebook_id`, `notebook_title` и timeline для инфографики.

Скачивание документов идет через `/downloads/...`.

## Конфигурация

Рабочий конфиг аналитики хранится в:

- [analytics-note.json](/C:/DISK D/Автоматизация/data/agents/analytics-note.json)

В нем лежат:

- промпт первого дня;
- промпт второго дня;
- промпт итоговой аналитики;
- промпт инфографики;
- список используемых форм;
- статус синхронизации с `n8n`.

## Ключевые API endpoints

- `GET /agents`
- `GET /agents/analytics-note/config`
- `POST /agents/analytics-note/config`
- `GET /agents/analytics-note/day1/history`
- `GET /agents/analytics-note/day2/history`
- `GET /agents/analytics-note/source-status`
- `GET /agents/analytics-note/summary/state`
- `POST /agents/analytics-note/day1/run`
- `POST /agents/analytics-note/day2/run`
- `POST /agents/analytics-note/summary/run`
- `POST /agents/analytics-note/infographic/run`
- `POST /web-login`

## Где смотреть артефакты

- конфиг: [analytics-note.json](/C:/DISK D/Автоматизация/data/agents/analytics-note.json)
- отчеты: [downloads/analytics-reports](/C:/DISK D/Автоматизация/downloads/analytics-reports)
- кеш выгрузок `n8n`: [downloads/n8n-inbox](/C:/DISK D/Автоматизация/downloads/n8n-inbox)
- бэкапы workflow: [n8n_backups](/C:/DISK D/Автоматизация/n8n_backups)

# AGENTS

## Main Reference

This file is the primary project reference. The former `README.md` content is merged here.

## Project Rules

- Keep project rules and agent instructions in `AGENTS.md`.
- Do not add introductory banners or explanatory hint blocks to the analytics UI unless explicitly requested.
- Do not add `stickyNote` hint nodes to generated n8n workflows unless explicitly requested.
- The analytics interface contains four independent blocks: source document 1, source document 2, final summary, and infographic.
- The final summary must be built only from the ready text documents of the first two blocks, not from raw JSON answers.
- The infographic block must remain disabled until the final summary is актуален and ready.
- The infographic block uses three user inputs: a Google Doc link, a common photo, and a logo.
- The infographic block must automatically include the current final summary and create a separate NotebookLM notebook.
- After generating a source document, its selected date is locked until the user presses reset.
- Reset clears current `latest.json` state only and must not delete historical run artifacts.
- If either source document is regenerated, the previous summary becomes stale and must be rebuilt.
- If the final summary is regenerated, the previous infographic becomes stale and must be rebuilt.
- The second source block uses a required start date and an optional end date; if the end date is provided, it must be later than the start date.
- The UI must not offer document download before the user explicitly generated a document in the current session.
- After important changes, ask the user whether to push to git; do not push automatically.

## Quick Start

1. Create and activate the virtual environment:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

2. Create `.env` from `.env.example`.

3. Start the proxy:

```powershell
.\.venv\Scripts\python run_proxy.py
```

4. Check auth:

```powershell
curl.exe -X POST http://127.0.0.1:8099/auth/check
```

5. Open the analytics UI:

- [http://127.0.0.1:8099/web-playground](http://127.0.0.1:8099/web-playground)

## What The Service Does

- Wraps `gemini-webapi` behind local FastAPI endpoints.
- Supports local automation flows through `n8n`.
- Builds analytics documents and `.docx` files from survey responses.
- Builds a NotebookLM infographic flow from the final summary, Google Doc, photo, and logo.
- Supports browser-driven Gemini web fallback through Playwright and a persistent Chrome profile.

## Auth Options

### Option 1: Explicit cookies

Set in `.env`:

```env
GEMINI_SECURE_1PSID=...
GEMINI_SECURE_1PSIDTS=...
```

### Option 2: Cookies JSON export

Set:

```env
GEMINI_COOKIE_JSON_PATH=cookies/google_cookies.json
```

Supported formats:

- Object with `__Secure-1PSID` and `__Secure-1PSIDTS`
- Browser extension export array with `name`, `value`, `domain`

### Option 3: Automatic browser cookies

Keep:

```env
GEMINI_ALLOW_BROWSER_COOKIE_FALLBACK=true
```

This works only if the local browser profile is already signed in and `browser-cookie3` can read it.

## Main Endpoints

- `GET /health`
- `POST /auth/check`
- `GET /models`
- `POST /generate`
- `POST /generate-image`
- `POST /generate-web`
- `POST /web-login`
- `GET /agents/analytics-note/config`
- `POST /agents/analytics-note/config`
- `GET /agents/analytics-note/day1/history`
- `GET /agents/analytics-note/day2/history`
- `GET /agents/analytics-note/summary/state`
- `POST /agents/analytics-note/day1/run`
- `POST /agents/analytics-note/day2/run`
- `POST /agents/analytics-note/summary/run`
- `POST /agents/analytics-note/infographic/run`
- `POST /agents/analytics-note/reset`

## File Inputs

Each file item must provide exactly one of:

- `path`
- `base64_data`
- `url`

## n8n Usage

Use a normal `HTTP Request` node, for example:

- Method: `POST`
- URL: `http://127.0.0.1:8099/generate-image`
- Content type: `JSON`

Example body:

```json
{
  "prompt": "Generate a 2k product mockup of a coffee bag on stone",
  "temporary": true,
  "image_filename_prefix": "coffee_bag"
}
```

## Notes

- This is not an official Google API integration.
- Changes in the Gemini web app can break the browser runner at any time.
- The browser runner always uses Gemini Pro and should return an explicit error if Pro is unavailable for the current account.
- Treat cookies like passwords.
- The direct API-style flow is less fragile than browser automation.

## Architecture

For the analytics flow, report storage, and detailed project structure, see [ARCHITECTURE.md](./ARCHITECTURE.md).

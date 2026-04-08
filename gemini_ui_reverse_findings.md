# Gemini UI Reverse Findings

Date: 2026-04-04

## Re-run

```powershell
cd "C:\DISK D\Автоматизация"
npm run capture:inspect
node .\tools\gemini_capture_mode_diffs.mjs fast_text thinking_text pro_text fast_image pro_image
```

Artifacts are written to:

- `C:\DISK D\Автоматизация\captures\gemini_ui_inspect.png`
- `C:\DISK D\Автоматизация\captures\gemini_ui_elements.json`
- `C:\DISK D\Автоматизация\captures\ui_reverse\`

## Stable selectors

- Mode menu button: `[data-test-id="bard-mode-menu-button"]`
- Prompt box: `textarea, [role="textbox"], [contenteditable="true"]`
- Image tool chip: `button, [role='button']` filtered by text containing `Создать изображение`

## Mode persistence

Gemini stores the selected mode with `batchexecute` request `rpcids=L5adhe` and the key `last_selected_mode_id_on_web`.

Observed IDs:

- Fast: `56fdd199312815e2`
- Thinking: `e051ce1aa80aa576`
- Pro: `e6fa609c3fa255c0`

This means mode selection is not only a visual toggle in the DOM. The selection is pushed into server-side/session state.

## StreamGenerate payload

The main submit request is:

- `POST /_/BardChatUi/data/assistant.lamda.BardFrontendService/StreamGenerate`

### Text mode

For `Fast`, `Thinking`, and `Pro`, the captured `StreamGenerate` payloads were identical except for:

- prompt text
- per-request hash at array index `4`
- per-request UUID later in the array

Important implication:

- the selected text mode is not directly encoded in the `StreamGenerate` body
- it is likely resolved from previously persisted Gemini session state

## Image tool flag

When `Создание изображений` is enabled, the UI changes into the image-style picker and the input chip becomes `Создание изображений`.

Comparing `fast_text` vs `fast_image`:

- `StreamGenerate` stays structurally the same
- but array index `49` changes from `null` to `14`

This is the cleanest observed request-body signal for the image-generation tool.

## Pro + image

`Pro` can stay selected while `Создание изображений` is active.

Observed UI state:

- screen shows image-style picker
- input chip shows `Создание изображений`
- mode button still shows `Pro`

Comparing `fast_image` vs `pro_image`:

- `StreamGenerate` payloads were identical except for the per-request hash
- the image flag at index `49` stayed `14` in both cases

Implication:

- the image tool flag is in the submit body
- the `Pro` vs `Fast` choice is still likely carried by persisted session state, not by a dedicated field in the submit body

## What we did not confirm yet

- a raw request field explicitly named `Nano Banana Pro`
- a separate official image-only backend endpoint
- a submit-body field that directly says `Fast` / `Thinking` / `Pro`

## Practical takeaway

If you want to automate the real Gemini web UI:

1. Set mode first by clicking the UI menu.
2. If needed, click `Создать изображение`.
3. Submit the prompt.
4. Treat mode as session state.
5. Treat image generation as an extra submit flag at index `49 = 14`.

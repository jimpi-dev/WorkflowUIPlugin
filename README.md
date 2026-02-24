# WorkflowUI Plugin

A **ComfyUI custom node** that adds HTTP API endpoints for media (output/input/temp) so the WorkflowUI application can delete files on the ComfyUI server, browse media, and serve images with optional previews—without adding any graph nodes to the ComfyUI UI.

⚠️ This custom node is a plugin for: [WorkflowUI](https://github.com/jimpi-dev/WorkflowUIPlugin)

---

## Features

- **Delete media** — Remove output/input/temp files by filename and subfolder (used when deleting remote run images from WorkflowUI).
- **View media** — Serve image, video, and audio files with caching; optional compressed image previews (WebP/JPEG or custom size).
- **List & browse** — Flat file list or one-level folder tree (JSON metadata only, no binary data) for building file-manager UIs.
- **Capabilities & version info** — Endpoints to detect the plugin and report installed custom node modules/versions for run metadata.

---

## Installation

1. Copy the `WorkflowUIPlugin` folder into your ComfyUI `custom_nodes` directory:

   ```
   ComfyUI/custom_nodes/WorkflowUIPlugin/
   ├── __init__.py
   ├── routes.py
   ├── version_info.py
   └── README.md
   ```

2. Restart ComfyUI. A startup banner confirms the plugin loaded.

---

## API Reference

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/delete` | Delete a file by `filename` / `subfolder` / `type`. Same as namespaced path below. |
| `POST` | `/workflowui/media/delete` | Delete a file (JSON body: `filename`, optional `subfolder`, `type`). |
| `GET` | `/workflowui/media/view` | Serve file bytes (images, video, audio). Query: `filename`, `subfolder`, `type`; optional `preview=webp` \| `jpeg` \| `256` for image previews. |
| `GET` | `/workflowui/media/list` | Flat list of files in a folder (JSON: `filename`, `size`, `mtime`). Query: `type`, `subfolder`. |
| `GET` | `/workflowui/media/tree` | One-level tree: `folders` and `files` for browsing. Query: `type`, `subfolder`. |
| `GET` | `/workflowui/media/capabilities` | Returns supported features (e.g. `workflowui_plugin`, `delete`, `list`, `tree`, `view`, `view_preview`). |
| `GET` | `/workflowui/version_info` | Plugin version and list of installed custom node modules/versions for run metadata. |

### Delete (POST)

**Request body (JSON):**

- `filename` (required) — e.g. `ComfyUI_00001_.png`
- `subfolder` (optional) — e.g. `""` or `"audio"`
- `type` (optional) — `"output"` (default), `"input"`, or `"temp"`

**Response:** `200` with `{"ok": true}` on success; `400` / `404` / `500` with `{"ok": false, "error": "..."}` on failure.

### View & preview

- **View** — Serves the file from disk with `Cache-Control: public, max-age=3600`.
- **Preview** (images only) — Add `&preview=webp`, `&preview=jpeg`, or `&preview=256` (or any size 64–2048) for a resized/compressed image. Non-image files ignore `preview` and return the original.

### List & tree

- **List** — Flat list of files with `filename`, `size`, `mtime` (newest first).
- **Tree** — One level: `folders` (subfolder names) and `files` (with `filename`, `size`, `mtime`). Use for file-manager style browsing.

---

## Security

- Paths are restricted to ComfyUI’s output/input/temp roots via `folder_paths.get_directory_by_type()`.
- `subfolder` and `filename` are validated to prevent directory traversal (`..`, path separators in filename).

---

## Compatibility

- **ComfyUI** — Uses `folder_paths.get_directory_by_type()` and `PromptServer.instance.routes`.
- **WorkflowUI** — Works with the existing backend that calls `POST /delete` with the same JSON shape; no backend change required when the plugin is installed.

---

## License

See repository license.

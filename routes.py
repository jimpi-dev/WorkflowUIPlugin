"""
WorkflowUI plugin: ComfyUI API routes for media access and deletion.

- POST /delete and POST /workflowui/media/delete — delete a file by filename/subfolder/type
  (WorkflowUI calls POST /delete with JSON body).
- GET /workflowui/media/view — serve a file from output/input/temp; ?preview=webp|jpeg|SIZE for compressed image previews.
- GET /workflowui/media/list — list files in output/input/temp (optional subfolder).
- GET /workflowui/media/tree — tree of subfolders and files for browsing.
- GET /workflowui/media/capabilities — report supported features (delete, list, view).
- GET /workflowui/version_info — installed custom node modules and versions (for run metadata).
"""

from __future__ import annotations

import io
import logging
import mimetypes
import os
from pathlib import Path

from aiohttp import web

logger = logging.getLogger(__name__)

MEDIA_TYPES = ("output", "input", "temp")

PREVIEW_MAX_DIM_DEFAULT = 512
PREVIEW_QUALITY_DEFAULT = 85
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tiff", ".tif"}


def _get_media_root(folder_type: str):
    """Resolve ComfyUI media root for output/input/temp. Returns None if invalid."""
    try:
        import folder_paths
        out = folder_paths.get_directory_by_type(folder_type)
        return os.path.abspath(out) if out else None
    except Exception:
        return None


def _resolve_media_path(folder_type: str, subfolder: str, filename: str) -> tuple[str | None, str | None]:
    """
    Resolve full_path for a media file. Ensures path is under base (no traversal).
    Returns (full_path, None) on success; (None, error_message) on validation failure.
    """
    if folder_type not in MEDIA_TYPES:
        return None, "Invalid type; use output, input, or temp"
    base = _get_media_root(folder_type)
    if not base:
        return None, "Could not resolve media directory"
    # Normalize subfolder: no leading/trailing slashes, no '..'
    sub = (subfolder or "").strip().replace("\\", "/").strip("/")
    if ".." in sub or sub.startswith("/"):
        return None, "Invalid subfolder"
    # Filename must be a single segment (no path)
    name = (filename or "").strip()
    if not name or "/" in name or "\\" in name or ".." in name:
        return None, "Invalid filename"
    full = os.path.join(base, sub, name) if sub else os.path.join(base, name)
    full = os.path.normpath(os.path.abspath(full))
    if not full.startswith(base):
        return None, "Path outside media directory"
    return full, None


def _delete_file(folder_type: str, subfolder: str, filename: str) -> tuple[bool, str, int]:
    """Delete one file. Returns (success, message, http_status)."""
    full, err = _resolve_media_path(folder_type, subfolder, filename)
    if err:
        return False, err, 400
    if not os.path.isfile(full):
        return False, "File not found", 404
    try:
        os.remove(full)
        return True, "ok", 200
    except OSError as e:
        logger.warning("Delete failed for %s: %s", full, e)
        return False, str(e), 500


def _is_image_file(filename: str, content_type: str | None) -> bool:
    if content_type and content_type.startswith("image/"):
        return True
    ext = Path(filename).suffix.lower()
    return ext in IMAGE_EXTENSIONS


def _make_image_preview(
    full_path: str,
    fmt: str,
    max_dim: int,
    quality: int,
) -> tuple[bytes | None, str | None]:
    """Return (body, content_type) for a resized/compressed preview, or (None, None) on failure."""
    try:
        from PIL import Image
    except ImportError:
        return None, None
    try:
        resample = getattr(Image.Resampling, "LANCZOS", getattr(Image, "LANCZOS", Image.BICUBIC))
        with Image.open(full_path) as img:
            img.load()
            if img.mode in ("RGBA", "P"):
                img = img.convert("RGB")
            w, h = img.size
            if w > max_dim or h > max_dim:
                if w >= h:
                    new_w, new_h = max_dim, max(1, int(h * max_dim / w))
                else:
                    new_w, new_h = max(1, int(w * max_dim / h)), max_dim
                img = img.resize((new_w, new_h), resample)
            buf = io.BytesIO()
            if fmt == "webp":
                img.save(buf, "WEBP", quality=quality, method=6)
                return buf.getvalue(), "image/webp"
            if fmt == "jpeg" or fmt == "jpg":
                img.save(buf, "JPEG", quality=quality, optimize=True)
                return buf.getvalue(), "image/jpeg"
            return None, None
    except Exception as e:
        logger.debug("Preview generation failed for %s: %s", full_path, e)
        return None, None


async def _handle_view(request: web.Request) -> web.Response:
    """GET ?filename=...&subfolder=...&type=output&preview=webp|jpeg|SIZE — serve file; optional compressed image preview."""
    filename = request.query.get("filename", "").strip()
    subfolder = request.query.get("subfolder", "")
    folder_type = (request.query.get("type") or "output").strip().lower()
    preview = request.query.get("preview", "").strip().lower()
    if not filename:
        return web.json_response({"error": "Missing filename"}, status=400)
    if folder_type not in MEDIA_TYPES:
        return web.json_response({"error": "Invalid type"}, status=400)
    full, err = _resolve_media_path(folder_type, subfolder, filename)
    if err:
        return web.json_response({"error": err}, status=400)
    if not os.path.isfile(full):
        return web.Response(status=404)
    content_type, _ = mimetypes.guess_type(filename, strict=False)
    if not content_type:
        content_type = "application/octet-stream"

    # Optional preview: only for images; format webp (default) or jpeg, optional max dimension
    if preview and _is_image_file(filename, content_type):
        max_dim = PREVIEW_MAX_DIM_DEFAULT
        quality = PREVIEW_QUALITY_DEFAULT
        fmt = "webp"
        if preview in ("webp", "jpeg", "jpg"):
            fmt = "jpeg" if preview in ("jpeg", "jpg") else "webp"
        else:
            try:
                max_dim = min(2048, max(64, int(preview)))
            except ValueError:
                pass
        body, preview_content_type = _make_image_preview(full, fmt, max_dim, quality)
        if body is not None and preview_content_type:
            response = web.Response(body=body)
            response.headers["Content-Type"] = preview_content_type
            response.headers["Cache-Control"] = "public, max-age=3600"
            return response

    response = web.FileResponse(full)
    response.headers["Content-Type"] = content_type
    response.headers["Cache-Control"] = "public, max-age=3600"
    return response


async def _handle_delete(request: web.Request) -> web.Response:
    """POST body: JSON { filename, subfolder?, type? }. type defaults to 'output'."""
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)
    filename = body.get("filename")
    if not filename:
        return web.json_response({"error": "Missing filename"}, status=400)
    subfolder = body.get("subfolder", "")
    folder_type = (body.get("type") or "output").strip().lower()
    if folder_type not in MEDIA_TYPES:
        return web.json_response({"error": "Invalid type"}, status=400)
    ok, msg, status = _delete_file(folder_type, subfolder, filename)
    if ok:
        return web.json_response({"ok": True}, status=status)
    return web.json_response({"ok": False, "error": msg}, status=status)


def _list_dir(folder_type: str, subfolder: str) -> tuple[list | None, str | None]:
    """List files in type/subfolder. Returns (list of {name, size, mtime}, error)."""
    if folder_type not in MEDIA_TYPES:
        return None, "Invalid type"
    base = _get_media_root(folder_type)
    if not base:
        return None, "Could not resolve media directory"
    sub = (subfolder or "").strip().replace("\\", "/").strip("/")
    if ".." in sub or sub.startswith("/"):
        return None, "Invalid subfolder"
    dir_path = os.path.join(base, sub) if sub else base
    dir_path = os.path.normpath(os.path.abspath(dir_path))
    if not dir_path.startswith(base):
        return None, "Path outside media directory"
    if not os.path.isdir(dir_path):
        return [], None
    result = []
    try:
        for name in os.listdir(dir_path):
            full = os.path.join(dir_path, name)
            if os.path.isfile(full):
                try:
                    stat = os.stat(full)
                    result.append({
                        "filename": name,
                        "size": stat.st_size,
                        "mtime": int(stat.st_mtime),
                    })
                except OSError:
                    continue
    except OSError as e:
        return None, str(e)
    result.sort(key=lambda x: (-x["mtime"], x["filename"]))
    return result, None


async def _handle_list(request: web.Request) -> web.Response:
    """GET ?type=output&subfolder= (subfolder optional)."""
    folder_type = (request.query.get("type") or "output").strip().lower()
    subfolder = request.query.get("subfolder", "")
    data, err = _list_dir(folder_type, subfolder)
    if err:
        return web.json_response({"error": err}, status=400 if "Invalid" in err else 500)
    return web.json_response({"type": folder_type, "subfolder": subfolder, "files": data})


def _tree_dir(folder_type: str, subfolder: str) -> tuple[dict | None, str | None]:
    """Return tree { folders: [name], files: [{filename, size, mtime}] } for one level."""
    if folder_type not in MEDIA_TYPES:
        return None, "Invalid type"
    base = _get_media_root(folder_type)
    if not base:
        return None, "Could not resolve media directory"
    sub = (subfolder or "").strip().replace("\\", "/").strip("/")
    if ".." in sub or sub.startswith("/"):
        return None, "Invalid subfolder"
    dir_path = os.path.join(base, sub) if sub else base
    dir_path = os.path.normpath(os.path.abspath(dir_path))
    if not dir_path.startswith(base):
        return None, "Path outside media directory"
    if not os.path.isdir(dir_path):
        return {"folders": [], "files": []}, None
    folders = []
    files = []
    try:
        for name in sorted(os.listdir(dir_path)):
            full = os.path.join(dir_path, name)
            if os.path.isdir(full):
                folders.append(name)
            elif os.path.isfile(full):
                try:
                    stat = os.stat(full)
                    files.append({"filename": name, "size": stat.st_size, "mtime": int(stat.st_mtime)})
                except OSError:
                    pass
    except OSError as e:
        return None, str(e)
    files.sort(key=lambda x: (-x["mtime"], x["filename"]))
    return {"folders": folders, "files": files}, None


async def _handle_tree(request: web.Request) -> web.Response:
    """GET ?type=output&subfolder= (one level: folders + files)."""
    folder_type = (request.query.get("type") or "output").strip().lower()
    subfolder = request.query.get("subfolder", "")
    data, err = _tree_dir(folder_type, subfolder)
    if err:
        return web.json_response({"error": err}, status=400 if "Invalid" in err else 500)
    return web.json_response({
        "type": folder_type,
        "subfolder": subfolder,
        "folders": data["folders"],
        "files": data["files"],
    })


async def _handle_capabilities(request: web.Request) -> web.Response:
    """GET /workflowui/media/capabilities — report what this plugin supports."""
    return web.json_response({
        "workflowui_plugin": True,
        "delete": True,
        "list": True,
        "tree": True,
        "view": True,
        "view_preview": True,
    })


async def _handle_version_info(request: web.Request) -> web.Response:
    """GET /workflowui/version_info — installed custom node modules and versions for run metadata."""
    from .version_info import get_version_info_payload
    return web.json_response(get_version_info_payload())


def register_routes():
    """Register routes with ComfyUI PromptServer. Call from __init__.py."""
    try:
        from server import PromptServer
        routes = PromptServer.instance.routes
    except Exception as e:
        logger.warning("WorkflowUIPlugin: could not get PromptServer routes: %s", e)
        return
    # WorkflowUI backend calls POST /delete with JSON { filename, subfolder, type }
    routes.post("/delete")(_handle_delete)
    routes.post("/workflowui/media/delete")(_handle_delete)
    routes.get("/workflowui/media/view")(_handle_view)
    routes.get("/workflowui/media/list")(_handle_list)
    routes.get("/workflowui/media/tree")(_handle_tree)
    routes.get("/workflowui/media/capabilities")(_handle_capabilities)
    routes.get("/workflowui/version_info")(_handle_version_info)
    logger.info("WorkflowUIPlugin: registered media routes (delete, view, list, tree, capabilities, version_info)")

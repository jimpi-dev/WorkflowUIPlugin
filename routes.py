from __future__ import annotations

import hashlib
import importlib.util
import json
import io
import logging
import mimetypes
import os
import sys
from pathlib import Path

from aiohttp import web

logger = logging.getLogger(__name__)


def _load_workflow_converter():
    try:
        from .workflow_converter import WorkflowConverter
        return WorkflowConverter
    except SyntaxError as e:
        if "null bytes" not in str(e).lower():
            raise
        logger.warning(
            "WorkflowUIPlugin: workflow_converter.py contains null bytes in source; "
            "loading with null bytes stripped (check deployment/sync)."
        )
        pkg_dir = os.path.dirname(os.path.abspath(__file__))
        path = os.path.join(pkg_dir, "workflow_converter.py")
        with open(path, "rb") as f:
            source = f.read().replace(b"\x00", b"").decode("utf-8", errors="replace")
        fullname = (__package__ or "WorkflowUIPlugin") + ".workflow_converter"

        class _Loader:
            def __init__(self, src, pathname):
                self.src = src
                self.pathname = pathname
            def create_module(self, spec):
                return None
            def exec_module(self, module):
                code = compile(self.src, self.pathname, "exec")
                exec(code, module.__dict__)

        spec = importlib.util.spec_from_loader(fullname, _Loader(source, path), origin=path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[fullname] = module
        spec.loader.exec_module(module)
        return module.WorkflowConverter

MEDIA_TYPES = ("output", "input", "temp")

PREVIEW_MAX_DIM_DEFAULT = 512
PREVIEW_QUALITY_DEFAULT = 85
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tiff", ".tif"}
UPLOAD_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
UPLOAD_VIDEO_EXTENSIONS = {".mp4", ".webm", ".mkv", ".mov"}
UPLOAD_AUDIO_EXTENSIONS = {".mp3", ".wav", ".ogg", ".flac", ".m4a"}


def _get_media_root(folder_type: str):
    try:
        import folder_paths
        out = folder_paths.get_directory_by_type(folder_type)
        return os.path.abspath(out) if out else None
    except Exception:
        return None


def _resolve_media_path(folder_type: str, subfolder: str, filename: str) -> tuple[str | None, str | None]:
    if folder_type not in MEDIA_TYPES:
        return None, "Invalid type; use output, input, or temp"
    base = _get_media_root(folder_type)
    if not base:
        return None, "Could not resolve media directory"
    sub = (subfolder or "").strip().replace("\\", "/").strip("/")
    if ".." in sub or sub.startswith("/"):
        return None, "Invalid subfolder"
    name = (filename or "").strip()
    if not name or "/" in name or "\\" in name or ".." in name:
        return None, "Invalid filename"
    full = os.path.join(base, sub, name) if sub else os.path.join(base, name)
    full = os.path.normpath(os.path.abspath(full))
    if not full.startswith(base):
        return None, "Path outside media directory"
    return full, None


def _delete_file(folder_type: str, subfolder: str, filename: str) -> tuple[bool, str, int]:
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


def _content_hash_name(content: bytes, ext: str) -> str:
    h = hashlib.sha256(content).hexdigest()[:16]
    return f"{h}{ext}"


async def _handle_upload(request: web.Request) -> web.Response:
    try:
        reader = await request.multipart()
    except Exception as e:
        logger.debug("WorkflowUIPlugin: upload multipart error: %s", e)
        return web.json_response({"error": "Invalid multipart body"}, status=400)
    media_type = (request.query.get("type") or "image").strip().lower()
    if media_type not in ("image", "video", "audio"):
        return web.json_response({"error": "type must be image, video, or audio"}, status=400)
    if media_type == "image":
        allowed_ext = UPLOAD_IMAGE_EXTENSIONS
    elif media_type == "video":
        allowed_ext = UPLOAD_VIDEO_EXTENSIONS
    else:
        allowed_ext = UPLOAD_AUDIO_EXTENSIONS
    part = None
    while True:
        try:
            p = await reader.next()
        except Exception:
            p = None
        if p is None:
            break
        name = getattr(p, "name", None) or ""
        if name in ("file", "image"):
            part = p
            break
    if part is None:
        return web.json_response({"error": "Missing form field 'file' or 'image'"}, status=400)
    filename = getattr(part, "filename", None) or "upload"
    ext = Path(filename).suffix.lower()
    if ext not in allowed_ext:
        return web.json_response(
            {"error": f"Unsupported {media_type} format. Allowed: {', '.join(sorted(allowed_ext))}"},
            status=400,
        )
    try:
        content = await part.read()
    except Exception as e:
        logger.warning("WorkflowUIPlugin: upload read failed: %s", e)
        return web.json_response({"error": "Failed to read uploaded file"}, status=400)
    if not content:
        return web.json_response({"error": "Empty file"}, status=400)
    base = _get_media_root("input")
    if not base:
        return web.json_response({"error": "Could not resolve input directory"}, status=500)
    safe_name = _content_hash_name(content, ext)
    full = os.path.join(base, safe_name)
    try:
        with open(full, "wb") as f:
            f.write(content)
    except OSError as e:
        logger.warning("WorkflowUIPlugin: upload write failed for %s: %s", full, e)
        return web.json_response({"error": f"Failed to save file: {e}"}, status=500)
    return web.json_response({
        "filename": safe_name,
        "name": safe_name,
        "subfolder": "",
        "type": "input",
    })


def _list_dir(folder_type: str, subfolder: str) -> tuple[list | None, str | None]:
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
    folder_type = (request.query.get("type") or "output").strip().lower()
    subfolder = request.query.get("subfolder", "")
    data, err = _list_dir(folder_type, subfolder)
    if err:
        return web.json_response({"error": err}, status=400 if "Invalid" in err else 500)
    return web.json_response({"type": folder_type, "subfolder": subfolder, "files": data})


def _tree_dir(folder_type: str, subfolder: str) -> tuple[dict | None, str | None]:
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
    return web.json_response({
        "workflowui_plugin": True,
        "delete": True,
        "list": True,
        "tree": True,
        "view": True,
        "view_preview": True,
        "upload": True,
    })


async def _handle_version_info(request: web.Request) -> web.Response:
    from .version_info import get_version_info_payload
    return web.json_response(get_version_info_payload())


async def _handle_workflows_list(request: web.Request) -> web.Response:
    from .workflow_export import list_workflows
    try:
        items = list_workflows()
        return web.json_response(items)
    except Exception as e:
        logger.warning("WorkflowUIPlugin: workflows list failed: %s", e)
        return web.json_response([])


async def _handle_workflow_by_id(request: web.Request) -> web.Response:
    from .workflow_export import load_workflow
    workflow_id = request.match_info.get("id", "").strip()
    if not workflow_id:
        return web.json_response({"error": "Missing workflow id"}, status=400)
    try:
        name, graph = load_workflow(workflow_id)
        return web.json_response({"name": name, "graph": graph})
    except FileNotFoundError as e:
        return web.json_response({"error": str(e)}, status=404)
    except ValueError as e:
        return web.json_response({"error": str(e)}, status=400)
    except Exception as e:
        logger.exception("WorkflowUIPlugin: load workflow %s failed", workflow_id)
        return web.json_response({"error": str(e)}, status=500)


WORKFLOWCONVERTER_MAX_CONTENT_LENGTH = 1 * 1024 * 1024


def _strip_null_bytes(obj):
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            key = k
            if isinstance(k, str) and ("\x00" in k or "\u0000" in k):
                key = k.replace("\x00", "").replace("\u0000", "")
            out[key] = _strip_null_bytes(v)
        return out
    if isinstance(obj, list):
        return [_strip_null_bytes(v) for v in obj]
    if isinstance(obj, str):
        if "\x00" in obj or "\u0000" in obj:
            return obj.replace("\x00", "").replace("\u0000", "")
        return obj
    return obj


async def _handle_workflowconverter_convert_post(request: web.Request) -> web.Response:
    logger.info("WorkflowUIPlugin: workflowconverter/convert POST received")
    if request.content_length is not None and request.content_length > WORKFLOWCONVERTER_MAX_CONTENT_LENGTH:
        return web.json_response({
            "success": False,
            "error": f"Request too large. Maximum size is {WORKFLOWCONVERTER_MAX_CONTENT_LENGTH // (1024 * 1024)} MB",
        }, status=413)
    try:
        raw = await request.read()
        raw = raw.replace(b"\x00", b"")
        body = json.loads(raw.decode("utf-8", errors="replace"))
    except Exception as e:
        logger.debug("WorkflowUIPlugin: workflowconverter/convert invalid JSON: %s", e)
        return web.json_response({"success": False, "error": f"Invalid JSON: {e!s}"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"success": False, "error": "Body must be a JSON object"}, status=400)
    body = _strip_null_bytes(body)
    try:
        WorkflowConverter = _load_workflow_converter()
        if WorkflowConverter.is_api_format(body):
            return web.json_response(body)
        if "nodes" not in body or "links" not in body:
            return web.json_response({
                "success": False,
                "error": "Invalid workflow format - missing nodes or links",
            }, status=400)
        if not isinstance(body.get("nodes"), list):
            return web.json_response({
                "success": False,
                "error": "Invalid workflow format - 'nodes' must be a list",
            }, status=400)
        if not isinstance(body.get("links"), list):
            return web.json_response({
                "success": False,
                "error": "Invalid workflow format - 'links' must be a list",
            }, status=400)
        api_prompt = WorkflowConverter.convert_to_api(body)
        num_nodes = len(body.get("nodes", []))
        num_links = len(body.get("links", []))
        num_converted = len(api_prompt)
        logger.info(
            "WorkflowUIPlugin: workflowconverter converted %s nodes, %s links -> %s API nodes",
            num_nodes, num_links, num_converted,
        )
        return web.json_response(api_prompt)
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        logger.error("WorkflowUIPlugin: workflowconverter/convert failed: %s", e)
        logger.error("Traceback: %s", tb)
        err_msg = str(e).strip() or "Internal server error during conversion"
        if len(err_msg) > 500:
            err_msg = err_msg[:497] + "..."
        return web.json_response({
            "success": False,
            "error": err_msg,
            "traceback": tb,
        }, status=500)


async def _handle_workflowconverter_convert_get(request: web.Request) -> web.Response:
    return web.json_response({
        "name": "WorkflowUI Workflow Converter",
        "version": "2.1.0",
        "description": "Converts non-API workflow format to API format for execution (subgraphs, bypassed nodes, GetNode/SetNode). Part of WorkflowUIPlugin.",
        "usage": "POST a workflow JSON to this endpoint to convert it to API format",
        "endpoint": "/workflowui/workflowconverter/convert",
        "source": "Based on Seth A. Robinson's comfyui-workflow-to-api-converter-endpoint",
        "repository": "https://github.com/SethRobinson/comfyui-workflow-to-api-converter-endpoint",
    })


def register_routes():
    try:
        from server import PromptServer
        routes = PromptServer.instance.routes
    except Exception as e:
        logger.warning("WorkflowUIPlugin: could not get PromptServer routes: %s", e)
        return
    routes.post("/delete")(_handle_delete)
    routes.post("/workflowui/media/delete")(_handle_delete)
    routes.post("/workflowui/media/upload")(_handle_upload)
    routes.get("/workflowui/media/view")(_handle_view)
    routes.get("/workflowui/media/list")(_handle_list)
    routes.get("/workflowui/media/tree")(_handle_tree)
    routes.get("/workflowui/media/capabilities")(_handle_capabilities)
    routes.get("/workflowui/version_info")(_handle_version_info)
    routes.get("/workflowui/workflows")(_handle_workflows_list)
    routes.get("/workflowui/workflows/{id}")(_handle_workflow_by_id)
    routes.post("/workflowui/workflowconverter/convert")(_handle_workflowconverter_convert_post)
    routes.get("/workflowui/workflowconverter/convert")(_handle_workflowconverter_convert_get)
    logger.info("WorkflowUIPlugin: registered media + workflow export + workflowconverter routes")

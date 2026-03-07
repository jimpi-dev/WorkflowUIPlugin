from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_PLUGIN_DIR = Path(__file__).resolve().parent
_COMFYUI_ROOT = _PLUGIN_DIR.parent.parent

def _workflow_ui_link_widget_order() -> list[str]:
    order = ["form_label"]
    for i in range(8):
        order.append(f"type_{i}")
        order.append(f"name_{i}")
    for i in range(8):
        order.append(f"input_text_{i}")
    for i in range(8):
        order.append(f"input_number_{i}")
    for i in range(8):
        order.append(f"input_image_{i}")
    for i in range(8):
        order.append(f"input_video_{i}")
    for i in range(8):
        order.append(f"input_audio_{i}")
    for i in range(8):
        order.append(f"input_boolean_{i}")
    return order

_WIDGET_ORDER: dict[str, list[str]] = {
    "WorkflowUILink": _workflow_ui_link_widget_order(),
    "WorkflowUI Link": _workflow_ui_link_widget_order(),
    "CLIPTextEncode": ["text"],
    "Text Multiline": ["text"],
    "PrimitiveStringMultiline": ["value"],
    "EmptyLatentImage": ["width", "height", "batch_size"],
    "EmptySD3LatentImage": ["width", "height", "batch_size"],
    "SDXLEmptyLatentSizePicker+": ["resolution", "batch_size", "width_override", "height_override"],
    "KSampler": ["seed", "steps", "cfg", "sampler_name", "scheduler", "denoise"],
    "BasicScheduler": ["scheduler", "steps", "denoise"],
    "UNETLoader": ["unet_name", "weight_dtype"],
    "DiffusionModelLoader": ["unet_name", "weight_dtype"],
    "LoadDiffusionModel": ["unet_name", "weight_dtype"],
    "StableCascadeCheckpointLoader": ["key_opt_b", "key_opt_c", "cache_mode"],
    "StableCascade_CheckpointLoader": ["key_opt_b", "key_opt_c", "cache_mode"],
    "SD3CheckpointLoader": ["ckpt_name", "shift"],
    "SD3LoadCheckpoint": ["ckpt_name", "shift"],
    "SaveImage": ["filename_prefix"],
    "Save Image": ["filename_prefix"],
    "Save Image (api)": ["filename_prefix"],
    "SaveImageNode": ["filename_prefix"],
}


def get_workflows_directory() -> Path | None:
    env_path = os.environ.get("WORKFLOWUI_WORKFLOWS_DIR", "").strip()
    if env_path:
        p = Path(env_path).resolve()
        if p.is_dir():
            return p
        return None
    default = _COMFYUI_ROOT / "user" / "default" / "workflows"
    if default.is_dir():
        return default
    return None


def list_workflows() -> list[dict[str, str]]:
    wf_dir = get_workflows_directory()
    if not wf_dir:
        return []
    result: list[dict[str, str]] = []
    try:
        for path in sorted(wf_dir.glob("*.json")):
            if path.is_file():
                stem = path.stem
                label = stem.replace("_", " ").strip() or stem
                result.append({"id": stem, "label": label})
    except OSError as e:
        logger.warning("WorkflowUIPlugin: list_workflows failed: %s", e)
    return result


def _is_api_format(data: dict[str, Any]) -> bool:
    if not data or not isinstance(data, dict):
        return False
    for v in data.values():
        if isinstance(v, dict) and ("class_type" in v or "inputs" in v):
            return True
    return False


def _extract_graph(workflow: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(workflow, dict):
        return workflow
    if workflow.get("nodes") is not None:
        return workflow
    if _is_api_format(workflow):
        return workflow
    for key in ("graph", "workflow", "prompt"):
        inner = workflow.get(key)
        if isinstance(inner, dict):
            if inner.get("nodes") is not None or _is_api_format(inner):
                return inner
            deeper = inner.get("graph") or inner.get("workflow")
            if isinstance(deeper, dict):
                return deeper
    return workflow


def _build_link_map(links: list[Any]) -> dict[int | str, tuple[str, int]]:
    result: dict[int | str, tuple[str, int]] = {}
    if not isinstance(links, list):
        return result
    for link in links:
        if not isinstance(link, (list, tuple)) or len(link) < 6:
            continue
        link_id, origin_id, origin_slot = link[0], link[1], link[2]
        try:
            origin_slot_int = int(origin_slot)
        except (TypeError, ValueError):
            continue
        result[link_id] = (str(origin_id), origin_slot_int)
    return result


def _inputs_from_ui_node(
    node: dict[str, Any],
    class_type: str,
    link_map: dict[int | str, tuple[str, int]],
) -> dict[str, Any]:
    inputs: dict[str, Any] = {}
    node_inputs = node.get("inputs")
    widgets_values = (
        node.get("widgets_values")
        or node.get("widgetsValues")
        or node.get("widget_values")
    )
    widget_order = _WIDGET_ORDER.get(class_type)

    if isinstance(widgets_values, dict):
        inputs = dict(widgets_values)
    elif isinstance(widgets_values, list) and widget_order:
        for idx, field in enumerate(widget_order):
            if idx < len(widgets_values):
                inputs[field] = widgets_values[idx]

    if isinstance(node_inputs, list):
        widget_only_names: list[str] = []
        for inp in node_inputs:
            if not isinstance(inp, dict):
                continue
            name = inp.get("name")
            if not name or not isinstance(name, str):
                continue
            link = inp.get("link")
            if link is not None:
                conn = link_map.get(link)
                if conn is not None:
                    inputs[name] = [conn[0], conn[1]]
            else:
                widget_only_names.append(name)
        for idx, name in enumerate(widget_only_names):
            if name not in inputs and isinstance(widgets_values, list) and idx < len(widgets_values):
                inputs[name] = widgets_values[idx]
    elif isinstance(node_inputs, dict) and not inputs:
        inputs = dict(node_inputs)
    return inputs


def _ensure_saveimage_defaults(api_graph: dict[str, Any]) -> None:
    save_types = ("SaveImage", "Save Image", "Save Image (api)", "SaveImageNode")
    for node in api_graph.values():
        if not isinstance(node, dict) or node.get("class_type") not in save_types:
            continue
        inputs = node.get("inputs")
        if isinstance(inputs, dict) and not inputs.get("filename_prefix"):
            inputs["filename_prefix"] = "ComfyUI"

_NODE_MODE_DISABLED = 4


def ui_to_api_format(workflow: dict[str, Any]) -> dict[str, Any]:
    workflow = _extract_graph(workflow)
    nodes = workflow.get("nodes")
    if not isinstance(nodes, list):
        if _is_api_format(workflow):
            return workflow
        return {}
    links = workflow.get("links")
    link_map = _build_link_map(links) if isinstance(links, list) else {}
    out: dict[str, Any] = {}
    for node in nodes:
        if not isinstance(node, dict):
            continue
        if node.get("mode") == _NODE_MODE_DISABLED:
            continue
        nid = node.get("id")
        if nid is None:
            continue
        node_id = str(nid)
        class_type = node.get("type") or node.get("class_type")
        if not class_type:
            continue
        inputs = _inputs_from_ui_node(node, class_type, link_map)
        meta = node.get("_meta")
        if not meta and isinstance(node.get("properties"), dict):
            s_r_name = (node.get("properties") or {}).get("Node name for S&R")
            if isinstance(s_r_name, str) and s_r_name.strip():
                meta = {"title": s_r_name.strip()}
        normalized: dict[str, Any] = {"class_type": class_type, "inputs": inputs}
        if meta:
            normalized["_meta"] = meta
        out[node_id] = normalized
    _ensure_saveimage_defaults(out)
    return out


def load_workflow(workflow_id: str) -> tuple[str, dict[str, Any]]:
    wf_dir = get_workflows_directory()
    if not wf_dir:
        raise FileNotFoundError("Workflow directory not configured")
    safe_id = (workflow_id or "").strip()
    if not safe_id or "/" in safe_id or "\\" in safe_id or ".." in safe_id:
        raise ValueError("Invalid workflow id")
    path = (wf_dir / safe_id).with_suffix(".json")
    if not path.is_file():
        raise FileNotFoundError(f"Workflow not found: {safe_id}")
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid workflow JSON: {e}") from e
    if not isinstance(data, dict):
        raise ValueError("Workflow must be a JSON object")
    name = data.get("name")
    if not isinstance(name, str) and isinstance(data.get("workflow"), dict):
        name = data["workflow"].get("name")
    if not isinstance(name, str) or not name.strip():
        name = safe_id.replace("_", " ").strip() or safe_id

    graph = _extract_graph(data)
    if not graph or (isinstance(graph, dict) and not graph.get("nodes") and not _is_api_format(graph)):
        raise ValueError("Workflow has no executable nodes")
    return name, graph

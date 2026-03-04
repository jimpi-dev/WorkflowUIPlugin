"""
WorkflowUILink: A ComfyUI custom node that defines which form fields WorkflowUI shows.

IMPORTANT - form_label: This is displayed as the header/section title above the inputs in the
WorkflowUI app. This is the main text users see when filling out the form. Set it to describe
your inputs (e.g. "Generation parameters", "Image settings").

Configuration:
- form_label: Header shown above inputs in the WorkflowUI app (displayed as section title)
- type_0..7: Dropdown to select field type per slot (text, number, seed, image, video, audio, boolean, select)
- name_0..7: Field name and label shown in the app (use "seed" for master seed binding; reserved convention)
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

try:
    import torch
    import numpy as np
    from PIL import Image, ImageOps, ImageSequence
    _HAS_IMAGE_DEPS = True
except ImportError:
    _HAS_IMAGE_DEPS = False

MAX_INPUT_SLOTS = 8

# Type options for the per-slot dropdown; empty = unused slot
TYPE_OPTIONS = ["", "text", "number", "seed", "image", "video", "audio", "boolean", "select"]


def _input_field_for_type(slot: int, typ: str) -> str:
    """Map definition type to actual input field name for binding."""
    if typ == "image":
        return f"input_image_{slot}"
    if typ == "video":
        return f"input_video_{slot}"
    if typ == "audio":
        return f"input_audio_{slot}"
    if typ == "boolean":
        return f"input_boolean_{slot}"
    if typ in ("number", "seed"):
        return f"input_number_{slot}"
    return f"input_text_{slot}"  # text, select, or unknown


def _get_media_files(content_types: list[str]) -> list[str]:
    """Get sorted list of filenames from input directory filtered by content type."""
    try:
        import folder_paths
        input_dir = folder_paths.get_input_directory()
        if not os.path.isdir(input_dir):
            return []
        files = [f for f in os.listdir(input_dir) if os.path.isfile(os.path.join(input_dir, f))]
        return sorted(folder_paths.filter_files_content_types(files, content_types))
    except Exception as e:
        logger.warning("WorkflowUILink: could not list media files: %s", e)
        return []


def _build_input_types() -> dict:
    """Build INPUT_TYPES with title widgets and file pickers for image/video/audio."""
    try:
        import folder_paths
        input_dir = folder_paths.get_input_directory()
        all_files = [f for f in os.listdir(input_dir) if os.path.isfile(os.path.join(input_dir, f))]
        image_files = sorted(folder_paths.filter_files_content_types(all_files, ["image"]))
        video_files = sorted(folder_paths.filter_files_content_types(all_files, ["video"]))
        audio_files = sorted(folder_paths.filter_files_content_types(all_files, ["audio"]))
    except Exception:
        image_files = video_files = audio_files = []

    required = {
        "form_label": ("STRING", {
            "default": "Generation parameters",
            "placeholder": "Header shown above inputs in WorkflowUI app (e.g. 'Generation parameters'). This text appears as the section title in the app.",
        }),
    }
    for i in range(MAX_INPUT_SLOTS):
        required[f"type_{i}"] = (TYPE_OPTIONS, {"default": ""})
        required[f"name_{i}"] = ("STRING", {
            "default": "",
            "placeholder": f"Name and label in app (e.g. 'Positive prompt'; use 'seed' for master seed)",
        })

    # Ensure '' is always first so unused slots can send empty and pass validation
    image_opts = [""] + (image_files or [])
    video_opts = [""] + (video_files or [])
    audio_opts = [""] + (audio_files or [])
    optional = {}
    for i in range(MAX_INPUT_SLOTS):
        optional[f"input_text_{i}"] = ("STRING", {"default": "", "multiline": True})
        optional[f"input_number_{i}"] = ("INT", {"default": 0, "min": 0, "max": 2**31 - 1})
        optional[f"input_image_{i}"] = (image_opts, {"image_upload": True})
        optional[f"input_video_{i}"] = (video_opts,)
        optional[f"input_audio_{i}"] = (audio_opts,)
        optional[f"input_boolean_{i}"] = ("BOOLEAN", {"default": False})

    return {"required": required, "optional": optional}


def _build_return_types() -> tuple[str, ...]:
    return (
        ("STRING",) * MAX_INPUT_SLOTS
        + ("INT",) * MAX_INPUT_SLOTS
        + ("IMAGE",) * MAX_INPUT_SLOTS
        + ("STRING",) * MAX_INPUT_SLOTS  # video filenames
        + ("STRING",) * MAX_INPUT_SLOTS   # audio filenames
        + ("BOOLEAN",) * MAX_INPUT_SLOTS  # boolean values
    )


def _build_return_names() -> tuple[str, ...]:
    names = []
    for i in range(MAX_INPUT_SLOTS):
        names.append(f"text_{i}")
    for i in range(MAX_INPUT_SLOTS):
        names.append(f"number_{i}")
    for i in range(MAX_INPUT_SLOTS):
        names.append(f"image_{i}")
    for i in range(MAX_INPUT_SLOTS):
        names.append(f"video_{i}")
    for i in range(MAX_INPUT_SLOTS):
        names.append(f"audio_{i}")
    for i in range(MAX_INPUT_SLOTS):
        names.append(f"boolean_{i}")
    return tuple(names)


def _load_image_from_path(image: str) -> Any:
    """Load image from filename (ComfyUI input dir) and return IMAGE tensor [1,H,W,3]."""
    if not _HAS_IMAGE_DEPS or not image or not isinstance(image, str):
        return None
    try:
        import folder_paths
        import node_helpers
        image_path = folder_paths.get_annotated_filepath(image)
        img = node_helpers.pillow(Image.open, image_path)
        output_images = []
        for i in ImageSequence.Iterator(img):
            i = node_helpers.pillow(ImageOps.exif_transpose, i)
            if i.mode in ("RGBA", "P"):
                i = i.convert("RGB")
            arr = np.array(i).astype(np.float32) / 255.0
            output_images.append(torch.from_numpy(arr)[None,])
        if not output_images:
            return None
        return torch.cat(output_images, dim=0) if len(output_images) > 1 else output_images[0]
    except Exception as e:
        logger.warning("WorkflowUILink: failed to load image %s: %s", image, e)
        return None


def _empty_image_placeholder() -> Any:
    if not _HAS_IMAGE_DEPS:
        return None
    return torch.zeros(1, 64, 64, 3)


class WorkflowUILink:
    """Pass-through node: defines app form inputs with per-slot labels and file pickers for media."""

    @classmethod
    def INPUT_TYPES(cls) -> dict:
        return _build_input_types()

    RETURN_TYPES = _build_return_types()
    RETURN_NAMES = _build_return_names()
    FUNCTION = "run"
    CATEGORY = "WorkflowUI"

    def run(self, form_label: str, **kwargs: Any) -> tuple:
        _empty_img = _empty_image_placeholder() if _HAS_IMAGE_DEPS else None
        results: list[Any] = []

        for i in range(MAX_INPUT_SLOTS):
            results.append(kwargs.get(f"input_text_{i}", ""))
        for i in range(MAX_INPUT_SLOTS):
            results.append(kwargs.get(f"input_number_{i}", 0))
        for i in range(MAX_INPUT_SLOTS):
            val = kwargs.get(f"input_image_{i}")
            if isinstance(val, str) and val.strip():
                loaded = _load_image_from_path(val)
                results.append(loaded if loaded is not None else _empty_img)
            else:
                results.append(_empty_img if val is None else val)
        for i in range(MAX_INPUT_SLOTS):
            val = kwargs.get(f"input_video_{i}")
            results.append(val if isinstance(val, str) else "")
        for i in range(MAX_INPUT_SLOTS):
            val = kwargs.get(f"input_audio_{i}")
            results.append(val if isinstance(val, str) else "")
        for i in range(MAX_INPUT_SLOTS):
            val = kwargs.get(f"input_boolean_{i}", False)
            results.append(bool(val))

        return tuple(results)


# Registration
NODE_CLASS_MAPPINGS = {
    "WorkflowUILink": WorkflowUILink,
}
NODE_DISPLAY_NAME_MAPPINGS = {
    "WorkflowUILink": "WorkflowUI Link",
}

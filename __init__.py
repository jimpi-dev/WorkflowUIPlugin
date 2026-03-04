"""
ComfyUI custom node: WorkflowUI plugin.

Registers API routes so WorkflowUI (and other clients) can:
- Delete files from ComfyUI output/input/temp (POST /delete, POST /workflowui/media/delete)
- List and browse media (GET /workflowui/media/list, GET /workflowui/media/tree)
- Detect plugin (GET /workflowui/media/capabilities)
- Installed modules/versions for run metadata (GET /workflowui/version_info)

Adds WorkflowUILink node: configurable inputs/outputs for WorkflowUI schema.
"""

from .version_info import __version__
from .routes import register_routes
from .workflow_ui_link import NODE_CLASS_MAPPINGS as LINK_MAPPINGS
from .workflow_ui_link import NODE_DISPLAY_NAME_MAPPINGS as LINK_DISPLAY_NAMES

register_routes()

# Startup banner
_banner_text = f"  WorkflowUI Plugin v{__version__}  "
_width = max(len(_banner_text) + 2, 24)
_border = "+" + "-" * (_width - 2) + "+"
print()
print(_border)
print("|" + _banner_text.center(_width - 2) + "|")
print(_border)
print()

NODE_CLASS_MAPPINGS = dict(LINK_MAPPINGS)
NODE_DISPLAY_NAME_MAPPINGS = dict(LINK_DISPLAY_NAMES)

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS"]
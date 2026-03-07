from .version_info import __version__
from .routes import register_routes
from .workflow_ui_link import NODE_CLASS_MAPPINGS as LINK_MAPPINGS
from .workflow_ui_link import NODE_DISPLAY_NAME_MAPPINGS as LINK_DISPLAY_NAMES

register_routes()

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
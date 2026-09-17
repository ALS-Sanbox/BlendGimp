"""Shared artist-facing paint-tool presentation state for BlendGimp.

This module intentionally contains no bpy/GIMP calls.  Texture Paint and
Object Paint import the same labels, action hints and cursor mapping so the
persistent hover owners present one consistent interaction model.
"""

TOOL_LABELS = {
    "PAINTBRUSH": "Paintbrush",
    "PENCIL": "Pencil",
    "ERASER": "Eraser",
    "AIRBRUSH": "Airbrush",
    "FILL": "Fill",
    "GRADIENT": "Gradient",
    "SMUDGE": "Smudge",
    "CLONE": "Clone",
    "HEAL": "Heal",
}

TOOL_ACTION_HINTS = {
    "PAINTBRUSH": "LMB paint",
    "PENCIL": "LMB paint",
    "ERASER": "LMB erase",
    "AIRBRUSH": "LMB paint",
    "FILL": "LMB fill",
    "GRADIENT": "LMB drag start/end",
    "SMUDGE": "LMB smudge",
    "CLONE": "Ctrl+LMB source • LMB clone",
    "HEAL": "Ctrl+LMB source • LMB heal",
}

CROSSHAIR_CURSOR_TOOLS = {"FILL", "GRADIENT"}
SOURCE_TOOLS = {"CLONE", "HEAL"}


def normalize_tool(tool):
    value = str(tool or "PAINTBRUSH").upper()
    return value if value in TOOL_LABELS else "PAINTBRUSH"


def tool_label(tool):
    tool = normalize_tool(tool)
    return TOOL_LABELS[tool]


def tool_action_hint(tool):
    tool = normalize_tool(tool)
    return TOOL_ACTION_HINTS[tool]


def tool_cursor(tool, ctrl=False):
    """Return a Blender cursor enum for a paint-canvas interaction.

    UI/non-paint regions remain DEFAULT and are handled by the modal owners.
    Clone/Heal expose an eyedropper cursor only while Ctrl is held to make
    source capture discoverable; normal clone/heal painting remains a brush.
    """
    tool = normalize_tool(tool)
    if tool in CROSSHAIR_CURSOR_TOOLS:
        return "CROSSHAIR"
    if tool in SOURCE_TOOLS and bool(ctrl):
        return "EYEDROPPER"
    return "PAINT_BRUSH"

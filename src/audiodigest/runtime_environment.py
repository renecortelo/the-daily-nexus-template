from __future__ import annotations

import os
from pathlib import Path

from audiodigest.cost_guard import FORBIDDEN_ENVIRONMENT_VARIABLES


def local_tool_environment(
    source: dict[str, str],
    runtime_dir: Path,
) -> dict[str, str]:
    environment = source.copy()
    for variable in FORBIDDEN_ENVIRONMENT_VARIABLES:
        environment.pop(variable, None)

    node_tools = runtime_dir / "node-tools" / "node_modules" / ".bin"
    available = [str(node_tools)]
    for variable in ("ProgramFiles", "ProgramFiles(x86)"):
        root = source.get(variable)
        if root:
            node_install = Path(root) / "nodejs"
            if node_install.is_dir():
                available.append(str(node_install))
    existing = environment.get("PATH", "")
    environment["PATH"] = os.pathsep.join([*available, existing])
    environment["PYTHONUTF8"] = "1"
    environment["PYTHONUNBUFFERED"] = "1"
    environment["HF_HUB_DISABLE_TELEMETRY"] = "1"
    environment["DO_NOT_TRACK"] = "1"
    return environment

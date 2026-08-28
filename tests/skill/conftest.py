"""Test bootstrap for the live-diagnosis skill package.

The skill ships its analysis modules as *siblings* under
``.agents/skills/phone-agent-live-diagnosis/scripts`` and imports them with bare
names (``from analyze import ...``, ``from evidence import ...``). Put that
directory on ``sys.path`` so the tests can import them the same way the CLI does
(``run_diagnosis.py`` inserts the same dir at import time). ``phone_agent`` is
already editable-installed, so nothing else is needed for the production imports.
"""

from __future__ import annotations

import sys
from pathlib import Path

# tests/skill/conftest.py -> parents[2] is the repo root (worktree).
_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPTS_DIR = _REPO_ROOT / ".agents" / "skills" / "phone-agent-live-diagnosis" / "scripts"

if _SCRIPTS_DIR.is_dir() and str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

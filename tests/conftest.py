"""Where the test suite imports things from, and why the order matters.

Two rules, and both were learned the hard way.

**The integration's own directory goes on the end of ``sys.path``, never the front.** It
holds ``select.py``, because Home Assistant requires a platform to be named after its
domain, and ``select`` is also a standard library module that ``subprocess`` and
``asyncio`` import. Prepended, ours wins, and the failure is spectacular and unrelated to
anything you changed: ``partially initialized module 'subprocess' has no attribute
'PIPE'``. Appended, the standard library keeps its own name. ``pytest``'s own ``pythonpath``
setting always prepends, which is why this is done here instead.

**Anything under ``tests/homeassistant`` imports the engine through
``custom_components.mvave.engine``; the pure tests import it as ``engine``.** Both work,
and mixing them in one test is silently wrong: they are two module objects for the same
file, so their enums and dataclasses are different classes, and every ``is`` comparison
between them is False. A page built from one and resolved by the other comes back empty,
which is exactly how this was found.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
#: The pure packages import as top-level ``engine``, ``transport`` and ``devices``, which
#: is how they will import once they move to PyPI, and is what keeps the pure test job
#: honest about not needing Home Assistant.
PURE = ROOT / "custom_components" / "mvave"

for path in (str(ROOT), str(PURE)):
    if path not in sys.path:
        sys.path.append(path)

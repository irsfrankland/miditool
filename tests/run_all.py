"""Run every test module in its own process: python -m tests.run_all

Tk on Windows keeps some of its windows and menus after a window is destroyed, and a process may only hold
10,000 such user objects. The window tests build hundreds of windows, so one `unittest discover` run
eventually fails with "No more menus can be allocated". A process per module stays well under the limit.
Pass module names (for example test_gui) to run only those.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def main(names: list[str]) -> int:
    modules = names or sorted(path.stem for path in HERE.glob("test_*.py"))
    failed = []
    for module in modules:
        print(f"== {module}", flush=True)
        result = subprocess.run([sys.executable, "-m", "unittest", f"tests.{module}"], cwd=HERE.parent)
        if result.returncode:
            failed.append(module)
    print("\nFailed: " + ", ".join(failed) if failed else "\nAll test modules passed.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

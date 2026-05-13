#!/usr/bin/env python3
"""
build_release.py — build a standalone Rule Lint executable for the current
platform using PyInstaller.

Usage:
    python3 -m venv .venv
    source .venv/bin/activate            # macOS / Linux
    # .venv\\Scripts\\activate           # Windows
    pip install pyinstaller

    python3 build_release.py

Output:
    dist/RuleLint              (Linux)
    dist/RuleLint.exe          (Windows)
    dist/RuleLint.app          (macOS bundle)

PyInstaller does not cross-compile — build each target platform on a host of
that platform. CI builds are configured in .github/workflows/build.yml.

See docs/PACKAGING.md for distribution notes.
"""

import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SPEC = HERE / "rule_lint.spec"
DIST = HERE / "dist"
BUILD = HERE / "build"
CATALOGUE = HERE / "rule_catalogue.py"
GENERATOR = HERE / "gen_rwf_catalogue.py"


def ensure_catalogue() -> None:
    """If a sibling src/eq.c exists and is newer than the bundled catalogue,
    regenerate it. Skipped otherwise — the catalogue ships in the repo so a
    fresh clone can build without needing eq.c."""
    eqc = HERE / "src" / "eq.c"
    if not eqc.exists():
        if not CATALOGUE.exists():
            print(f"warning: {CATALOGUE.name} missing and src/eq.c not "
                  f"present — bundled catalogue will be empty",
                  file=sys.stderr)
        return
    needs = (not CATALOGUE.exists()
             or eqc.stat().st_mtime > CATALOGUE.stat().st_mtime)
    if needs:
        print("Regenerating rule_catalogue.py from src/eq.c …")
        subprocess.check_call([sys.executable, str(GENERATOR)])


def have_pyinstaller() -> bool:
    try:
        import PyInstaller  # noqa: F401
        return True
    except ImportError:
        return False


def main() -> int:
    if not have_pyinstaller():
        print("PyInstaller not found. Install it first:", file=sys.stderr)
        print("    pip install pyinstaller", file=sys.stderr)
        return 2

    ensure_catalogue()

    for d in (BUILD, DIST):
        if d.exists():
            shutil.rmtree(d)

    cmd = [sys.executable, "-m", "PyInstaller", "--clean", str(SPEC)]
    print("Running:", " ".join(cmd))
    result = subprocess.run(cmd, cwd=HERE)
    if result.returncode != 0:
        return result.returncode

    print()
    print("Build succeeded. Output:")
    for entry in sorted(DIST.iterdir()):
        size_mb = (entry.stat().st_size / (1024 * 1024)
                   if entry.is_file() else None)
        if size_mb:
            print(f"  {entry.relative_to(HERE)}  ({size_mb:.1f} MB)")
        else:
            print(f"  {entry.relative_to(HERE)}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())

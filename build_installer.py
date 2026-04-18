"""Build the Spot Key standalone installer.

Usage:
    uv run python build_installer.py

Requires:
    - Nuitka:     uv pip install nuitka
    - Inno Setup: winget install JRSoftware.InnoSetup

Produces:
    Output/SpotKeySetup-{version}.exe
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BUILD_DIR = ROOT / "build"
DIST_DIR = BUILD_DIR / "spot_key.dist"

def _find_iscc() -> str:
    """Locate the Inno Setup compiler."""
    candidates = [
        Path.home() / "AppData/Local/Programs/Inno Setup 6/ISCC.exe",
        Path("C:/Program Files (x86)/Inno Setup 6/ISCC.exe"),
        Path("C:/Program Files/Inno Setup 6/ISCC.exe"),
    ]
    for p in candidates:
        if p.exists():
            return str(p)
    # Fall back to PATH
    iscc = shutil.which("iscc") or shutil.which("ISCC")
    if iscc:
        return iscc
    print("ERROR: Inno Setup not found. Install with: winget install JRSoftware.InnoSetup")
    sys.exit(1)


def main() -> None:
    print("=== Step 1: Nuitka standalone build ===")
    if DIST_DIR.exists():
        shutil.rmtree(DIST_DIR)

    subprocess.run(
        [
            sys.executable, "-m", "nuitka",
            "--standalone",
            "--python-flag=-m",
            "--enable-plugin=tk-inter",
            "--windows-console-mode=disable",
            f"--output-dir={BUILD_DIR}",
            "--assume-yes-for-downloads",
            "spot_key",
        ],
        cwd=ROOT,
        check=True,
    )
    print(f"\nNuitka output: {DIST_DIR}")

    print("\n=== Step 2: Inno Setup installer ===")
    iscc = _find_iscc()
    subprocess.run([iscc, str(ROOT / "installer.iss")], check=True)

    # Find the output
    output_dir = ROOT / "Output"
    installers = list(output_dir.glob("SpotKeySetup-*.exe"))
    if installers:
        print(f"\nInstaller ready: {installers[0]}")
        print(f"Size: {installers[0].stat().st_size / 1024 / 1024:.1f} MB")
    else:
        print("\nWARNING: Installer not found in Output/")
        sys.exit(1)


if __name__ == "__main__":
    main()

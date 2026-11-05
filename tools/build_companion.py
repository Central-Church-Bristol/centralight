"""Build the Companion module (M16): companion\\centralight-<version>.tgz. In Companion: Modules → Import module package,
and pick it. It holds one main.js with everything bundled in (Bitfocus's @companion-module/base too), the way the
modules Companion installs are made, so nothing needs installing on the PC that runs Companion.

    cd companion\\centralight
    npm install                 (once: @companion-module/base and esbuild, from the npm registry)
    cd ..\\..
    .venv\\Scripts\\python.exe tools\\build_companion.py

The module's tests (npm test in companion\\centralight, and tests\\test_live_m16.py) don't need any of this.
"""

from __future__ import annotations

import gzip
import io
import json
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "companion" / "centralight"
COMPANION_NODE = Path(r"C:\Program Files\Companion\resources\node-runtimes\node22\node.exe")
LICENCE_NAMES = ("LICENSE", "LICENSE.md", "LICENSE.txt", "LICENCE", "LICENCE.md", "license", "license.md")
MOMENT = 1762300800  # (every file's date in the package: 5 Nov 2025, so the same sources make the same package)


def node() -> str:
    found = str(COMPANION_NODE) if COMPANION_NODE.exists() else shutil.which("node")
    if not found:
        raise SystemExit("Node isn't installed (Companion's own, or any).")
    return found


def package_folders(meta: dict) -> list[Path]:
    """The npm packages that went into the bundle (from esbuild's list of what it read)."""
    found: dict[str, Path] = {}
    for name in meta.get("inputs", {}):
        parts = Path(name).as_posix().split("/")
        if "node_modules" not in parts:
            continue
        at = len(parts) - 1 - parts[::-1].index("node_modules")  # (the innermost node_modules)
        take = 2 if parts[at + 1].startswith("@") else 1
        folder = MODULE.joinpath(*parts[:at + 1 + take])
        found[folder.as_posix()] = folder
    return [found[key] for key in sorted(found)]


def licences(folders: list[Path], version: str) -> str:
    text = [f"CentraLight's Companion module {version} (MIT). Bundled into main.js with it:\n"]
    for folder in folders:
        info = json.loads((folder / "package.json").read_text(encoding="utf-8"))
        text.append(f"\n==== {info.get('name')} {info.get('version')} ({info.get('license', 'see below')}) ====\n")
        for name in LICENCE_NAMES:
            if (folder / name).is_file():
                text.append((folder / name).read_text(encoding="utf-8", errors="replace").strip() + "\n")
                break
        else:
            text.append("(no licence file in the package: see its package.json)\n")
    return "".join(text)


def write_package(target: Path, files: dict[str, bytes]) -> None:
    """A .tgz with everything in a pkg folder, as Companion's module packages have it (the same bytes each time)."""
    raw = io.BytesIO()
    folders = sorted({"/".join(name.split("/")[:end]) for name in files for end in range(1, name.count("/") + 1)})
    with tarfile.open(fileobj=raw, mode="w", format=tarfile.USTAR_FORMAT) as tar:
        for folder in folders:  # (pkg/ first: Companion takes the first folder as the package's top)
            info = tarfile.TarInfo(folder)
            info.type, info.mode, info.mtime = tarfile.DIRTYPE, 0o755, MOMENT
            tar.addfile(info)
        for name in sorted(files):
            info = tarfile.TarInfo(name)
            info.size, info.mode, info.mtime = len(files[name]), 0o644, MOMENT
            tar.addfile(info, io.BytesIO(files[name]))
    target.write_bytes(gzip.compress(raw.getvalue(), compresslevel=9, mtime=0))


def main() -> int:
    manifest = json.loads((MODULE / "companion" / "manifest.json").read_text(encoding="utf-8"))
    package = json.loads((MODULE / "package.json").read_text(encoding="utf-8"))
    version = manifest["version"]
    if package["version"] != version:
        print(f"package.json says {package['version']} but companion\\manifest.json says {version}: make them the same.")
        return 1
    base = MODULE / "node_modules" / "@companion-module" / "base" / "package.json"
    if not base.is_file() or not (MODULE / "node_modules" / "esbuild").is_dir():
        print("First run npm install in companion\\centralight (it fetches @companion-module/base and esbuild).")
        return 1
    api = json.loads(base.read_text(encoding="utf-8"))["version"]
    if manifest["runtime"]["apiVersion"] != api:
        print(f"companion\\manifest.json's apiVersion is {manifest['runtime']['apiVersion']}, but npm installed "
              f"@companion-module/base {api}: make them the same.")
        return 1
    with tempfile.TemporaryDirectory(prefix="cl-companion-") as folder:
        bundle, meta = Path(folder) / "main.js", Path(folder) / "meta.json"
        subprocess.run([node(), "build.js", str(bundle), str(meta)], cwd=MODULE, check=True)
        bundled = package_folders(json.loads(meta.read_text(encoding="utf-8")))
        files = {
            "pkg/companion/manifest.json": json.dumps(manifest, indent=2).encode("utf-8") + b"\n",
            "pkg/companion/HELP.md": (MODULE / "companion" / "HELP.md").read_bytes(),
            "pkg/main.js": bundle.read_bytes(),
            "pkg/main.js.LICENSE.txt": licences(bundled, version).encode("utf-8"),
            "pkg/package.json": json.dumps({"name": package["name"], "version": version, "license": package["license"],
                                            "type": "commonjs", "dependencies": {}}, indent=2).encode("utf-8") + b"\n",
        }
    target = MODULE.parent / f"centralight-{version}.tgz"
    write_package(target, files)
    print(f"Made {target.relative_to(ROOT)} ({target.stat().st_size // 1024} KB, with {len(bundled)} packages bundled).")
    return 0


if __name__ == "__main__":
    sys.exit(main())

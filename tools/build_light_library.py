"""Make CentraLight's light library (centralight/data/light_library.json.gz) from the Open Fixture Library's download.

    .venv\\Scripts\\python.exe tools\\build_light_library.py ofl_export_ofl.zip [YYYY-MM-DD]

The ZIP is https://open-fixture-library.org/download.ofl (every light, in the library's own format; MIT licence, see
centralight/data/OPEN_FIXTURE_LIBRARY.txt). The date is when it was downloaded (today if left out). It prints how
many lights and modes went in, and what was left out and why. Nothing in the ZIP is run: it's only read as JSON.
Run it again with a newer download to bring the list up to date.
"""

from __future__ import annotations

import datetime
import gzip
import io
import json
import sys
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

from centralight.light_library import LIBRARY_FILE, LibraryModel, library_data, load  # noqa: E402
from centralight.ofl import types_from_fixture  # noqa: E402


def main(zip_path: str, made: str) -> int:
    with zipfile.ZipFile(zip_path) as archive:
        names = archive.namelist()
        makers_raw = json.loads(archive.read("manufacturers.json"))
        makers = {key: str(value.get("name") or key) for key, value in makers_raw.items() if isinstance(value, dict)}
        models: list[LibraryModel] = []
        skipped: list[str] = []
        modes = 0
        for name in sorted(names):
            if not name.endswith(".json") or name == "manufacturers.json" or name.count("/") != 1:
                continue
            maker_key, _sep, _file = name.partition("/")
            data = json.loads(archive.read(name))
            maker = makers.get(maker_key, maker_key)
            kinds, why = types_from_fixture(data, maker)
            skipped += why
            if kinds:
                models.append(LibraryModel(maker, str(data.get("name") or ""), name[:-5],
                                           tuple(str(c) for c in data.get("categories") or []), tuple(kinds)))
                modes += len(kinds)
    data = library_data(models, source="Open Fixture Library", url="https://open-fixture-library.org", made=made)
    text = json.dumps(data, ensure_ascii=False, separators=(",", ":"), sort_keys=False)
    LIBRARY_FILE.parent.mkdir(parents=True, exist_ok=True)
    buffer = io.BytesIO()
    with gzip.GzipFile(filename="", mode="wb", fileobj=buffer, mtime=0, compresslevel=9) as out:  # (the same bytes
        out.write(text.encode("utf-8"))  # each time from the same download)
    LIBRARY_FILE.write_bytes(buffer.getvalue())
    check = load(LIBRARY_FILE)
    print(f"{len(models)} lights from {len({m.maker for m in models})} makers, {modes} modes "
          f"({len(text) / 1e6:.2f} MB of JSON, {LIBRARY_FILE.stat().st_size / 1e3:.0f} KB in {LIBRARY_FILE.name})")
    print(f"read back: {len(check.models)} lights, {sum(len(m.modes) for m in check.models)} modes")
    print(f"left out ({len(skipped)}):")
    for line in skipped:
        print("  " + line)
    return 0 if not check.problem and len(check.models) == len(models) else 1


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else datetime.date.today().isoformat()))

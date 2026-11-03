"""Layout and scaling check, run by tests/test_layout.py (one process per scale factor):

    .venv\\Scripts\\python.exe tests\\layout_check.py --scale 1.5 --out tests\\screens\\m0

It builds the main window with a fake engine at 1280x720, 1366x768, 1920x1080 and
2560x1440 (window sizes in logical pixels), once with the normal show and once with very
long names, and on every page checks that:
- every widget stays inside its parent
- no widget overlaps a sibling
- no widget is squashed below its minimum size
- no text is cut off, unless it's shortened with "…" and has the full text as a tooltip
It also checks the dialogs, and saves a screenshot of everything to --out.
Exit code 0 = no problems; otherwise the problems are printed.
"""

from __future__ import annotations

import argparse
import faulthandler
import os
import shutil
import sys
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SIZES = ((1280, 720), (1366, 768), (1920, 1080), (2560, 1440))
LONG_SHOW_NAME = "Central Church Bristol — Sunday Evening Service with a really very long show name"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--scale", type=float, default=1.0)
    p.add_argument("--out", type=Path, default=ROOT / "tests" / "screens" / "latest")
    p.add_argument("--fallback-fonts", action="store_true", help="Don't load the brand fonts (test Segoe UI)")
    p.add_argument("--sizes", default="", help="Only these sizes, e.g. 1280x720,1920x1080")
    return p.parse_args()


def main() -> int:
    faulthandler.enable()  # if Qt ever crashes, print where (the test shows stderr)
    args = parse_args()
    os.environ["QT_SCALE_FACTOR"] = f"{args.scale:g}"
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    from tests import guard

    guard.install()
    from tests.qt_support import process, qt_app

    app = qt_app(brand_fonts=not args.fallback_fonts)

    from tests import layout_rules
    from tests.fakes import FakeEngine
    from tests.gui_fixtures import ai_edit_reply, ai_problem_reply, busy_show_engine, long_names_show, with_inputs

    from centralight.new_show import show_from_patch
    from centralight.settings import Settings
    from centralight.ui.main_window import MainWindow
    from centralight.ui.preview_page import LookPreview
    from centralight.ui.quit_dialog import QuitDialog
    from centralight.ui.settings_dialog import SettingsDialog
    from PySide6.QtWidgets import QTabWidget

    sizes = SIZES
    if args.sizes:
        sizes = tuple(tuple(int(v) for v in item.split("x")) for item in args.sizes.split(","))
    fonts_tag = "fallback" if args.fallback_fonts else "brand"
    out = args.out / f"{fonts_tag}-{args.scale:g}x"
    if out.is_dir():  # start empty, so screenshots from older runs can't mislead anyone
        shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True, exist_ok=True)
    problems: list[str] = []
    shots = 0

    for variant, show in (("normal", with_inputs(show_from_patch())), ("long", long_names_show())):
        engine: FakeEngine = busy_show_engine(show)
        window = MainWindow(Settings(), engine.show, engine, engine.fake_output, save_files=False)
        layout_rules.show_all_chips(window)
        if variant == "long":
            window.header.set_show_name(LONG_SHOW_NAME)
        window.show()
        for width, height in sizes:
            window.resize(width, height)
            process(app, 4)
            window.bridge.poll()
            process(app, 2)
            for key in window.pages:
                states = [key]
                if key == "editor":  # the Editor four times: an element (with a shape), the NDI element (Blur), the Input tab, the Globals tab
                    window.editor_page.open_input("stage-shapes")
                    states = ["editor-element", "editor-ndi", "editor-input", "editor-globals"]
                for state in states:
                    window.show_page(key)
                    if state == "editor-element":
                        window.editor_page.select_element("E2")
                        window.editor_page._shape_from_viewport("S1")
                    elif state == "editor-ndi":
                        window.editor_page.select_element("E1")
                    elif state == "editor-input":
                        window.editor_page.select_element("")
                        window.editor_page.details.widget().findChild(QTabWidget).setCurrentIndex(0)
                    elif state == "editor-globals":
                        window.editor_page.details.widget().findChild(QTabWidget).setCurrentIndex(1)
                    process(app, 3)
                    if key == "editor":
                        window.editor_page.render_sync()
                        process(app, 2)
                    where = f"{fonts_tag} {args.scale:g}x {width}x{height} {variant} {state}"
                    problems += layout_rules.check(window, where)
                    window.grab().save(str(out / f"{width}x{height}-{variant}-{state}.png"))
                    shots += 1
            # header modes at this width are part of the check too
            problems += layout_rules.check_header(window, f"{fonts_tag} {args.scale:g}x {width}x{height} {variant} header")
        window.set_locked(True)
        process(app, 2)
        problems += layout_rules.check(window, f"{fonts_tag} {args.scale:g}x locked {variant}")
        window.grab().save(str(out / f"locked-{variant}.png"))
        window.set_locked(False)
        # the History panel, with a few steps in it (long light names in the "long" show)
        window.show_page("layout")
        page = window.layout_page
        for fixture_id, percent in (("slimpar-101", 30.0), ("slimpar-102", 50.0), ("slimpar-103", 70.0)):
            page.scene.clearSelection()
            page._items[fixture_id].setSelected(True)
            process(app)
            page.x_spin.setValue(percent)
        popup = page.undo_bar.show_history()
        process(app, 3)
        problems += layout_rules.check(popup, f"{fonts_tag} {args.scale:g}x history {variant}")
        popup.grab().save(str(out / f"history-{variant}.png"))
        shots += 1
        popup.close()
        # the AI window: Create with a reply that has problems, Edit with a good reply (preview and changes)
        long = variant == "long"
        for name, input_id, reply in (("ai-create", "", ai_problem_reply(long)),
                                      ("ai-edit", "pillars-purple", ai_edit_reply(window.show_data, long))):
            dialog = window.open_ai_dialog(input_id, reply)
            dialog.description.setPlainText("the pillars breathing purple, slowly, while the other lights follow the screen"
                                            if not input_id else "make it breathe a little faster")
            for w, h in ((1100, 640), (880, 560)):
                dialog.resize(w, h)
                process(app, 3)
                problems += layout_rules.check(dialog, f"{fonts_tag} {args.scale:g}x {name} {w}x{h} {variant}")
                dialog.grab().save(str(out / f"{name}-{w}x{h}-{variant}.png"))
                shots += 1
            dialog.reject()
            process(app, 1)
        window.editor_page.shutdown()
        window.document.close()
        window.hide()
        window.deleteLater()
        process(app, 2)

    settings = Settings(show_folder=str(ROOT))
    for name, dialog in (
        ("settings", SettingsDialog(settings, LONG_SHOW_NAME, None, ports=[("COM3", "USB Serial Port (looks like the Eurolite)"), ("COM1", "Communications Port (COM1)")],
                                    show=with_inputs(show_from_patch()))),
        ("quit", QuitDialog(None)),
        ("preview", LookPreview(None)),
    ):
        dialog.show()
        process(app, 4)
        problems += layout_rules.check(dialog, f"{fonts_tag} {args.scale:g}x dialog {name}")
        dialog.grab().save(str(out / f"dialog-{name}.png"))
        shots += 1
        dialog.close()
        process(app, 1)

    print(f"{shots} screenshots in {out}")
    if problems:
        print(f"{len(problems)} layout problems:")
        for problem in problems[:200]:
            print("  " + problem)
        return 1
    print("No layout problems.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

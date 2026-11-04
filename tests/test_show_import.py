"""M13: Import from another show… for inputs (Dashboard) and show automations (Automation tab)."""

from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

import cv2
import numpy as np

from centralight.links import read_stamps, update_stamps
from centralight.model import Automation, GlobalVar, Show
from centralight.new_show import create_show
from centralight.show_import import apply_import, copy_files, plan_import, summary
from centralight.show_io import freeze, load_show, save_whole_show


def picture(path: Path, colour=(0, 128, 255)) -> None:
    image = np.zeros((18, 32, 3), dtype=np.uint8)
    image[:] = colour
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(cv2.imencode(".png", image)[1].tobytes())


def make_shows(root: Path) -> tuple[Show, Show]:
    """"Morning" (with inputs that use a picture, a light this show lacks, a show variable, and an automation that
    sends one of them) and "Evening" (an input with the same id as one of Morning's)."""
    morning = create_show(root / "Morning", "Morning")
    evening = create_show(root / "Evening", "Evening")
    picture(Path(morning.folder) / "media" / "Candles.png")
    update_stamps(morning.folder, lambda s: s.__setitem__("media/Candles.png", {
        "original": r"G:\Drive\Candles.png", "size": 1, "modified": 2}))
    (Path(morning.folder) / "paint").mkdir()
    (Path(morning.folder) / "paint" / "0123456789ab.png").write_bytes(b"strokes")
    lights = [fx.id for fx in morning.colour_fixtures()]
    tracks = freeze([[{"id": "E1", "kind": "image", "file": "media/Candles.png", "original": r"G:\Drive\Candles.png",
                       "mask": {"exclude": [lights[0], "slimpar-999"]}}],
                     [{"id": "E2", "kind": "paint", "strokes": "paint/0123456789ab.png",
                       "opacity": {"value": 1, "expr": "glow * 0.5"}}], [], []])
    from centralight.model import Input

    candles = Input(id="candles", name="Candles", tracks=tracks, hotkey="F7")
    warm = Input(id="warm", name="Warm (Morning)")
    morning = replace(morning.with_inputs({**morning.inputs, "candles": candles, "warm": warm},
                                          morning.input_order + ("candles", "warm")),
                      globals=morning.globals + (GlobalVar("glow", "number", 0.7),))
    welcome = Automation("welcome", "Welcome", actions=freeze([{"do": "base", "input": "candles"},
                                                               {"do": "variable", "name": "glow", "value": 1}]))
    morning = morning.with_automations({"welcome": welcome}, ("welcome",))
    save_whole_show(morning)
    evening = evening.with_inputs({**evening.inputs, "warm": Input(id="warm", name="Warm (Evening)")},
                                  evening.input_order + ("warm",))
    save_whole_show(evening)
    return load_show(Path(morning.folder)), load_show(Path(evening.folder))


class CoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.morning, self.evening = make_shows(self.root)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_inputs_keep_their_ids_and_bring_their_files(self) -> None:
        plan = plan_import(self.evening, self.morning, inputs=["candles"])
        self.assertEqual(plan.files, ["media/Candles.png", "paint/0123456789ab.png"])
        self.assertEqual([g.name for g in plan.new_variables], ["glow"])
        self.assertEqual(plan.missing_lights, ["slimpar-999"])
        text = "\n".join(summary(plan, self.evening, self.morning, keep_both=False))
        self.assertIn("slimpar-999 isn't in this show, so it's ignored here", text)
        self.assertIn("glow", text)
        renamed = copy_files(plan, self.evening, self.morning)
        new = apply_import(self.evening, self.morning, plan, keep_both=False, renamed=renamed)
        candles = new.inputs["candles"]
        self.assertEqual(candles.tracks[0][0]["original"], r"G:\Drive\Candles.png")
        self.assertEqual(candles.tracks[0][0]["file"], "media/Candles.png")
        self.assertTrue((Path(self.evening.folder) / "media" / "Candles.png").is_file())
        self.assertTrue((Path(self.evening.folder) / "paint" / "0123456789ab.png").is_file())
        self.assertIn("media/Candles.png", read_stamps(self.evening.folder))  # (where the copy came from: carried)
        self.assertEqual(new.input_order[-1], "candles")
        self.assertEqual([g.name for g in new.globals if g.name == "glow"], ["glow"])
        self.assertEqual(next(g for g in new.globals if g.name == "glow").default, 0.7)

    def test_a_clash_replaces_or_keeps_both(self) -> None:
        plan = plan_import(self.evening, self.morning, inputs=["warm"])
        self.assertEqual(plan.clashes, [("input", "warm")])
        replaced = apply_import(self.evening, self.morning, plan, keep_both=False)
        self.assertEqual(replaced.inputs["warm"].name, "Warm (Morning)")
        both = apply_import(self.evening, self.morning, plan, keep_both=True)
        self.assertEqual(both.inputs["warm"].name, "Warm (Evening)")
        new_ids = [i for i in both.inputs if i not in self.evening.inputs]
        self.assertEqual(len(new_ids), 1)
        self.assertEqual(both.inputs[new_ids[0]].name, "Warm (Morning)")

    def test_a_picture_with_the_same_name_but_different_contents_gets_its_own(self) -> None:
        picture(Path(self.evening.folder) / "media" / "Candles.png", colour=(255, 0, 0))
        plan = plan_import(self.evening, self.morning, inputs=["candles"])
        renamed = copy_files(plan, self.evening, self.morning)
        self.assertEqual(renamed["media/Candles.png"], "media/Candles 2.png")
        new = apply_import(self.evening, self.morning, plan, keep_both=False, renamed=renamed)
        self.assertEqual(new.inputs["candles"].tracks[0][0]["file"], "media/Candles 2.png")

    def test_automations_bring_the_inputs_they_send(self) -> None:
        plan = plan_import(self.evening, self.morning, automations=["welcome"])
        self.assertEqual(plan.needed, {"welcome": ["candles"]})
        self.assertEqual(plan.inputs, ["candles"])
        self.assertEqual(plan.brought_along, ["candles"])
        new = apply_import(self.evening, self.morning, plan, keep_both=False, renamed=copy_files(plan, self.evening, self.morning))
        self.assertIn("welcome", new.automations)
        self.assertIn("candles", new.inputs)
        alone = plan_import(self.evening, self.morning, automations=["welcome"], bring_inputs=False)
        self.assertEqual(alone.inputs, [])
        self.assertIn("candles", "\n".join(summary(alone, self.evening, self.morning, False)))

    def test_keep_both_points_automations_at_the_new_ids(self) -> None:
        from centralight.model import Input

        evening = self.evening.with_inputs({**self.evening.inputs, "candles": Input(id="candles", name="Evening candles")})
        plan = plan_import(evening, self.morning, inputs=["candles"], automations=["welcome"])
        new = apply_import(evening, self.morning, plan, keep_both=True)
        sent = new.automations["welcome"].actions[0]["input"]
        self.assertNotEqual(sent, "candles")
        self.assertEqual(new.inputs[sent].name, "Candles")
        self.assertEqual(new.inputs["candles"].name, "Evening candles")

    def test_replacing_a_live_input_is_mentioned(self) -> None:
        plan = plan_import(self.evening, self.morning, inputs=["warm"], live={"warm"})
        self.assertIn("On the lights now", "\n".join(summary(plan, self.evening, self.morning, keep_both=False)))

    def test_keep_both_never_takes_an_id_that_is_coming_too(self) -> None:
        """This show has "warm"; the other has "warm" and "warm-morning"... and the new id for its "warm" must not be one
        of its own others."""
        from centralight.model import Input

        other = self.morning.with_inputs({**self.morning.inputs, "warm-morning": Input(id="warm-morning", name="Second")},
                                         self.morning.input_order + ("warm-morning",))
        plan = plan_import(self.evening, other, inputs=["warm", "warm-morning"])
        new = apply_import(self.evening, other, plan, keep_both=True)
        names = sorted(inp.name for inp in new.inputs.values())
        self.assertIn("Warm (Morning)", names)
        self.assertIn("Second", names)
        self.assertIn("Warm (Evening)", names)
        self.assertEqual(len(new.inputs), len(self.evening.inputs) + 2, "nothing lost")

    def test_a_renamed_automation_is_followed_by_ones_that_switch_it(self) -> None:
        switcher = Automation("switcher", "Switcher", actions=freeze([{"do": "automation", "automation": "welcome",
                                                                       "enabled": False}]))
        other = self.morning.with_automations({**self.morning.automations, "switcher": switcher},
                                              ("welcome", "switcher"))
        evening = self.evening.with_automations({"welcome": Automation("welcome", "Evening welcome")}, ("welcome",))
        plan = plan_import(evening, other, automations=["welcome", "switcher"])
        new = apply_import(evening, other, plan, keep_both=True)
        renamed = next(a for a in new.automations.values() if a.name.startswith("Welcome"))
        self.assertNotEqual(renamed.id, "welcome")
        self.assertEqual(new.automations["switcher"].actions[0]["automation"], renamed.id)
        self.assertEqual(new.automations["welcome"].name, "Evening welcome")

    def test_a_hotkey_already_used_here_is_left_off(self) -> None:
        from centralight.model import Input

        evening = self.evening.with_inputs({**self.evening.inputs, "mine": Input(id="mine", name="Mine", hotkey="F7")})
        plan = plan_import(evening, self.morning, inputs=["candles"])
        self.assertEqual(plan.hotkeys, ["candles"])
        self.assertIn("hotkey F7", "\n".join(summary(plan, evening, self.morning, keep_both=False)))
        self.assertEqual(apply_import(evening, self.morning, plan, keep_both=False).inputs["candles"].hotkey, "")

    def test_lights_the_automations_use_are_listed_too(self) -> None:
        dim = Automation("dim", "Dim", actions=freeze([{"do": "house", "light": "dimmer-99", "level": 0.2}]))
        other = self.morning.with_automations({**self.morning.automations, "dim": dim}, ("welcome", "dim"))
        plan = plan_import(self.evening, other, automations=["dim"])
        self.assertIn("dimmer-99", plan.missing_lights)


class WindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from tests.qt_support import qt_app

        cls.app = qt_app()

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.morning, self.evening = make_shows(self.root)
        self.windows = []

    def tearDown(self) -> None:
        from tests.qt_support import process

        for window in self.windows:
            window.linked_media.close()
            window._shut_down = True
            window.editor_page.shutdown()
            window.document.close()
            window.close()
            window.deleteLater()
        process(self.app)
        self._tmp.cleanup()

    def make(self):
        from centralight.settings import Settings
        from centralight.ui.main_window import MainWindow
        from tests.fakes import FakeEngine
        from tests.qt_support import process

        engine = FakeEngine(self.evening)
        window = MainWindow(Settings(), self.evening, engine, engine.fake_output, save_files=False,
                            notes_path=self.root / "fixture_map.csv", shows_root=self.root)
        from centralight.links import AllowedLinks

        window.linked_media.allowed = AllowedLinks(self.root / "allowed.json")  # (each test its own: nothing allowed yet)
        window.resize(1366, 768)
        window.show()
        process(self.app, 3)
        self.windows.append(window)
        return window, engine

    def test_dashboard_import_is_one_undo_step_and_sends_nothing(self) -> None:
        from PySide6.QtWidgets import QDialog

        from centralight.commands import SwapShow
        from tests.qt_support import process

        window, engine = self.make()
        window.dashboard.grid.new_tile.import_button.click()
        dialog = window.import_dialog
        self.assertEqual(dialog.other.name, "Morning")
        dialog.tick(["candles", "warm"])
        self.assertIn("isn't in this show", dialog.summary.text())
        self.assertTrue(dialog.replace_choice.isVisibleTo(dialog))
        dialog.keep_both.setChecked(True)
        self.assertIn(r"G:\Drive\Candles.png", dialog.summary.text())
        before = window.document.show
        submitted: list = []
        real_submit = engine.submit
        engine.submit = lambda command: (submitted.append(command), real_submit(command))[1]
        dialog.done(QDialog.DialogCode.Accepted)
        process(self.app, 2)
        show = window.document.show
        self.assertIn("candles", show.inputs)
        self.assertEqual(show.inputs["warm"].name, "Warm (Evening)")
        self.assertTrue(window.linked_media.allowed.allowed(r"G:\Drive\Candles.png"))
        self.assertTrue(submitted)
        self.assertTrue(all(isinstance(c, SwapShow) for c in submitted), submitted)  # (the show changed; nothing was sent)
        window.document.undo_newest()
        self.assertEqual(window.document.show.inputs.keys(), before.inputs.keys())
        self.assertEqual(window.document.show.globals, before.globals)

    def test_an_imported_automation_doesnt_fire_just_because_it_was_imported(self) -> None:
        from PySide6.QtWidgets import QDialog

        from centralight.show_io import automation_to_dict
        from tests.qt_support import process

        every_day = Automation("every-day", "Every day", conditions=freeze([{"type": "days", "days": ["mon", "tue", "wed",
                                                                                                    "thu", "fri", "sat", "sun"]}]),
                               actions=freeze([{"do": "base", "input": "warm", "transition": "cut"}]))
        folder = Path(self.morning.folder)
        import json

        (folder / "automations" / "every-day.json").write_text(json.dumps(automation_to_dict(every_day)), encoding="utf-8")
        window, engine = self.make()
        engine.settle(2.0, step=0.5)
        before = engine.frames.get().base
        window.show_page("automation")
        window.automation_page.import_button.click()
        dialog = window.import_dialog
        dialog.tick(["every-day"])
        dialog.done(QDialog.DialogCode.Accepted)
        process(self.app, 2)
        self.assertIn("every-day", window.document.show.automations)
        engine.settle(3.0, step=0.5)
        self.assertEqual(engine.frames.get().base, before, "its conditions are true already: it waits until they come true")

    def test_automation_tab_import(self) -> None:
        from PySide6.QtWidgets import QDialog

        from tests.qt_support import process

        window, _engine = self.make()
        window.show_page("automation")
        window.automation_page.import_button.click()
        dialog = window.import_dialog
        self.assertTrue(dialog.bring_inputs.isVisibleTo(dialog))
        dialog.tick(["welcome"])
        self.assertIn("come too", dialog.summary.text())
        dialog.done(QDialog.DialogCode.Accepted)
        process(self.app, 2)
        self.assertIn("welcome", window.document.show.automations)
        self.assertIn("candles", window.document.show.inputs)


if __name__ == "__main__":
    unittest.main()

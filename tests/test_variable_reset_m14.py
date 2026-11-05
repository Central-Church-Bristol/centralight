"""M14 item 10: each show variable keeps its last value when the show loads (as before), or goes back to its starting
value. "The show loads" is a person or a global automation loading it, or the PC having just started; not CentraLight
reopening after a quit or a crash. The reset comes before the show's "When this show loads" automations, so one of them
can still set a variable as it loads. Old files load as Keep."""

from __future__ import annotations

import time
import unittest
from dataclasses import replace

from centralight.commands import LoadShow, SetVariable
from centralight.live_state import LiveState
from centralight.model import Automation, GlobalVar, Show
from centralight.new_show import show_from_patch
from centralight.show_io import freeze, show_from_dict, show_to_dict
from tests.fakes import FakeEngine


def show_with(folder: str, *globals_: GlobalVar, automations: tuple = ()) -> Show:
    show = replace(show_from_patch(), name=folder.title(), folder=f"C:/Shows/{folder}", globals=tuple(globals_))
    return show.with_automations({a.id: a for a in automations}, tuple(a.id for a in automations)) if automations else show


KEPT = GlobalVar("level", "number", 0.2, 0.0, 1.0)
RESET = GlobalVar("mood", "number", 0.1, 0.0, 1.0, on_load="reset")


class ResetTests(unittest.TestCase):
    def values(self, engine) -> dict:
        return dict(engine._globals)

    def test_keep_and_reset_when_switching_away_and_back(self) -> None:
        church, evening = show_with("church", KEPT, RESET), show_with("evening")
        engine = FakeEngine(church)
        engine.submit(SetVariable("level", 0.7))
        engine.submit(SetVariable("mood", 0.9))
        engine.submit(LoadShow(evening))
        engine.submit(LoadShow(church))
        self.assertEqual(self.values(engine), {"level": 0.7, "mood": 0.1})  # (kept; back to its starting value)

    def test_at_pc_start_but_not_on_reopening(self) -> None:
        church = show_with("church", KEPT, RESET)
        # (saved an hour ago, before the PC started a minute ago; and saved half a minute ago: CentraLight reopening)
        before_the_start = LiveState(show=church.folder, globals={"level": 0.7, "mood": 0.9}, saved_at=time.time() - 3600)
        self.assertEqual(self.values(FakeEngine(church, live=before_the_start, pc_start=True)), {"level": 0.7, "mood": 0.1})
        reopened = replace(before_the_start, saved_at=time.time() - 30)
        self.assertEqual(self.values(FakeEngine(church, live=reopened, pc_start=True)), {"level": 0.7, "mood": 0.9})
        self.assertEqual(self.values(FakeEngine(church, live=before_the_start)), {"level": 0.7, "mood": 0.9})  # (the PC's been up a day)

    def test_the_default_show_at_pc_start_never_takes_the_last_shows_values(self) -> None:
        """Where items 5 and 10 meet: the PC starts and the default show opens, but live.json was saved with another show
        loaded. The default show starts from its own saved values (Keep) or its starting values (Reset), never the last
        show's, and loading the last show again brings its own back. (app.main hands the engine live.for_show(folder),
        which moves the last show's values to the other shows' and takes out the opened show's own.)"""
        church = show_with("church", KEPT, RESET)
        evening = show_with("evening", replace(KEPT, default=0.3), replace(RESET, default=0.4))
        saved = LiveState(show=evening.folder, globals={"level": 0.8, "mood": 0.95},
                          show_globals={church.folder: {"level": 0.7, "mood": 0.9}},
                          base={"input": "warm-white", "time": 5.0}, saved_at=time.time() - 3600)
        live = saved.for_show(church.folder)
        self.assertIsNone(live.base)  # (the last show's Base, Overlay and automations' state stay with it)
        engine = FakeEngine(church, live=live, pc_start=True)
        self.assertEqual(self.values(engine), {"level": 0.7, "mood": 0.1})  # (its own, and its starting value)
        engine.submit(SetVariable("level", 0.6))
        engine.submit(LoadShow(evening))
        self.assertEqual(self.values(engine), {"level": 0.8, "mood": 0.4})  # (the last show's own; reset as it loads)
        engine.submit(LoadShow(church))
        self.assertEqual(self.values(engine), {"level": 0.6, "mood": 0.1})

    def test_an_automation_as_the_show_loads_sets_it_after_the_reset(self) -> None:
        on_load = Automation("set-mood", "Set the mood", True, "all", freeze([{"type": "show_loads"}]),
                             freeze([{"do": "variable", "name": "mood", "value": 0.6}]))
        church, evening = show_with("church", KEPT, RESET, automations=(on_load,)), show_with("evening")
        engine = FakeEngine(church)
        engine.submit(SetVariable("mood", 0.9))
        engine.submit(LoadShow(evening))
        engine.submit(LoadShow(church))
        engine.settle(1.5)
        self.assertEqual(self.values(engine)["mood"], 0.6)

    def test_files(self) -> None:
        show = show_with("church", KEPT, RESET)
        data = show_to_dict(show)
        saved = {g["name"]: g for g in data["globals"]}
        self.assertNotIn("on_load", saved["level"])  # (left out at the default)
        self.assertEqual(saved["mood"]["on_load"], "reset")
        loaded, _warnings = show_from_dict(data)
        self.assertEqual([g.on_load for g in loaded.globals], ["keep", "reset"])
        old = dict(data, globals=[{"name": "level", "type": "number", "default": 0.2}])  # (from before M14)
        self.assertEqual(show_from_dict(old)[0].globals[0].on_load, "keep")
        odd = dict(data, globals=[{"name": "level", "type": "number", "default": 0.2, "on_load": "sometimes"}])
        self.assertEqual(show_from_dict(odd)[0].globals[0].on_load, "keep")

    def test_the_ai_guide_says_so(self) -> None:
        from centralight.ai_guide import guide_text

        text = guide_text(show_with("church", KEPT, RESET))
        self.assertIn("goes back to it each time the show loads", text)


class CardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from tests.qt_support import qt_app

        cls.app = qt_app()

    def test_the_choice_on_the_card(self) -> None:
        from centralight.ui.document import ShowDocument
        from centralight.ui.globals_panel import GlobalsPanel
        from tests.qt_support import process

        class Engine:
            def submit(self, _command) -> None:
                pass

        document = ShowDocument(show_with("church", KEPT), Engine(), save_files=False)
        self.addCleanup(document.close)
        panel = GlobalsPanel(document, lambda: "http://127.0.0.1:8746")
        self.addCleanup(panel.deleteLater)
        process(self.app)
        card = panel.cards["level"]
        self.assertEqual(card.on_load.currentData(), "keep")
        card.on_load.setCurrentIndex(card.on_load.findData("reset"))
        process(self.app)
        self.assertEqual(document.show.globals[0].on_load, "reset")
        self.assertEqual(document.undo_stack.undoText(), "Show variable level: when the show loads, go back to its starting value")
        document.undo()
        self.assertEqual(document.show.globals[0].on_load, "keep")


if __name__ == "__main__":
    unittest.main()

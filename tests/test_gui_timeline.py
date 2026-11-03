"""M6 in the window: the timeline (timed inputs, adding, moving, trimming and splitting elements, transitions,
going back to infinite), the playhead and transport, keyframes from the details panel's ◆ and the timeline's
diamonds, the Dashboard's progress bar, and a timed input looking the same in the Editor as on the lights."""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

import numpy as np
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtTest import QTest

from centralight.commands import SendToBase
from centralight.editor_render import EditorRenderer, EditorRequest
from centralight.input_edit import element_keys, find_element, set_key, set_timed
from centralight.model import END_HOLD, END_LOOP, Input, element_span
from centralight.new_show import show_from_patch
from centralight.params import keys_of
from centralight.settings import Settings
from centralight.show_io import freeze
from centralight.ui.main_window import MainWindow
from tests.fakes import FakeEngine
from tests.gui_fixtures import with_inputs
from tests.qt_support import process, qt_app


def bar_sweep() -> Input:
    """A timed "Bar sweep": a white bar crossing a deep blue background every 4 seconds, keyframed."""
    background = {"id": "E1", "kind": "paint", "fill": {"type": "colour", "colour": "#0A1A66"}, "start": 0.0, "end": 4.0}
    bar = {"id": "E2", "kind": "paint", "fill": {"type": "none"}, "start": 0.0, "end": 4.0,
           "shapes": [{"id": "S1", "type": "rect", "x": {"keys": [[0, -0.2], [4, 1.2, "linear"]]}, "y": 0.5, "w": 0.18, "h": 1.2,
                       "softness": 0.3, "colour": "#FFFFFF"}]}
    return Input(id="bar-sweep", name="Bar sweep", infinite=False, length=4.0, end=END_LOOP,
                 tracks=freeze([[background], [bar], [], []]))


class TimelineTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = qt_app()

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.windows: list[MainWindow] = []

    def tearDown(self) -> None:
        for window in self.windows:
            window._shut_down = True
            window.editor_page.shutdown()
            window.document.close()
            window.close()
            window.deleteLater()
        process(self.app)
        self._tmp.cleanup()

    def make(self, show=None, size=(1366, 768)) -> tuple[MainWindow, FakeEngine]:
        show = show or with_inputs(show_from_patch())
        inputs = dict(show.inputs)
        inputs["bar-sweep"] = bar_sweep()
        show = show.with_inputs(inputs, show.input_order + ("bar-sweep",))
        engine = FakeEngine(show)
        window = MainWindow(Settings(), engine.show, engine, engine.fake_output, save_files=False,
                            notes_path=Path(self._tmp.name) / "fixture_map.csv")
        window.resize(*size)
        window.show()
        process(self.app, 5)
        self.windows.append(window)
        return window, engine

    def editor(self, window, input_id: str):
        window.open_editor(input_id)
        page = window.editor_page
        page.set_playing(False)
        page.seek(0.0)
        process(self.app, 2)
        return page

    def element(self, window, input_id: str, element_id: str):
        return find_element(window.document.show.inputs[input_id], element_id)[2]

    def drag(self, widget, start: QPointF, end: QPointF) -> None:
        a, b = start.toPoint(), end.toPoint()
        QTest.mousePress(widget, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, a)
        for i in range(1, 6):
            QTest.mouseMove(widget, QPoint(round(a.x() + (b.x() - a.x()) * i / 5), round(a.y() + (b.y() - a.y()) * i / 5)))
        QTest.mouseRelease(widget, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, b)
        process(self.app)


class TimelineTests(TimelineTestCase):
    def test_make_it_timed_add_move_trim_split(self) -> None:
        window, _engine = self.make()
        page = self.editor(window, "warm-white")
        timeline = page.timeline
        self.assertTrue(timeline.infinite.isChecked())
        self.assertTrue(timeline.set_timed(True))
        inp = window.document.show.inputs["warm-white"]
        self.assertFalse(inp.infinite)
        self.assertTrue(timeline.length.isVisibleTo(timeline))
        timeline.length.setValue(12.0)
        self.assertEqual(window.document.show.inputs["warm-white"].length, 12.0)
        e2 = timeline.drop_element(1, "paint", start=6.0)
        self.assertEqual(element_span(self.element(window, "warm-white", e2), window.document.show.inputs["warm-white"]), (6.0, 11.0))
        canvas = timeline.canvas
        rect = canvas.bar_rect(1, self.element(window, "warm-white", e2), window.document.show.inputs["warm-white"])
        self.drag(canvas, rect.center(), rect.center() + QPointF(2 * canvas.pps, 0))  # two seconds later
        start, end = element_span(self.element(window, "warm-white", e2), window.document.show.inputs["warm-white"])
        self.assertAlmostEqual(start, 8.0, delta=0.15)
        self.assertAlmostEqual(end - start, 5.0, delta=0.01)
        self.assertTrue(window.document.undo_stack.undoText().startswith("Move Paint " + e2))
        inp = window.document.show.inputs["warm-white"]
        rect = canvas.bar_rect(1, self.element(window, "warm-white", e2), inp)
        self.drag(canvas, QPointF(rect.right() - 1, rect.center().y()), QPointF(rect.right() - 1 - canvas.pps, rect.center().y()))
        start2, end2 = element_span(self.element(window, "warm-white", e2), window.document.show.inputs["warm-white"])
        self.assertAlmostEqual(end2, end - 1.0, delta=0.15)  # trimmed by a second
        page.seek(start2 + 1.0)
        timeline.select(e2)
        second = timeline.split_selected()
        self.assertTrue(second)
        self.assertAlmostEqual(element_span(self.element(window, "warm-white", e2), window.document.show.inputs["warm-white"])[1], start2 + 1.0, places=2)
        window.undo()
        self.assertIsNone(find_element(window.document.show.inputs["warm-white"], second))

    def test_element_transitions_and_drops(self) -> None:
        window, _engine = self.make()
        page = self.editor(window, "bar-sweep")
        timeline = page.timeline
        timeline.set_element_transition("E2", "in", "wipe")
        self.assertEqual(self.element(window, "bar-sweep", "E2")["in"]["kind"], "wipe")
        timeline.set_element_transition("E2", "in", duration=2.0)
        self.assertEqual(self.element(window, "bar-sweep", "E2")["in"]["duration"], 2.0)
        timeline.set_element_transition("E2", "in", "cut")
        self.assertNotIn("in", self.element(window, "bar-sweep", "E2"))

    def test_back_to_infinite_asks_which_to_keep(self) -> None:
        window, _engine = self.make()
        page = self.editor(window, "bar-sweep")
        timeline = page.timeline
        e3 = timeline.drop_element(0, "paint", start=2.0)
        asked = []
        timeline.ask_keep = lambda crowded: asked.append(sorted(crowded)) or {0: e3}
        self.assertTrue(timeline.set_timed(False))
        self.assertEqual(asked, [[0]])
        inp = window.document.show.inputs["bar-sweep"]
        self.assertTrue(inp.infinite)
        self.assertEqual([e["id"] for e in inp.tracks[0]], [e3])
        timeline.ask_keep = lambda crowded: None  # Cancel keeps it as it was
        timeline.set_timed(True)
        e4 = timeline.drop_element(0, "paint", start=5.0)
        self.assertTrue(e4)
        self.assertFalse(timeline.set_timed(False))
        self.assertFalse(window.document.show.inputs["bar-sweep"].infinite)


class PlayheadAndKeyTests(TimelineTestCase):
    def test_transport(self) -> None:
        window, _engine = self.make()
        page = self.editor(window, "bar-sweep")
        page.seek(1.5)
        self.assertEqual(page.timeline.playhead, 1.5)
        self.assertTrue(page.timeline.time_label.text().startswith("0:01.5"))
        page.set_playing(True)
        page._play_origin -= 1.0  # a second goes by
        page._advance()
        self.assertAlmostEqual(page.playhead, 2.5, delta=0.1)
        page._play_origin -= 3.0  # it loops (bar sweep loops every 4 s)
        page._advance()
        self.assertLess(page.playhead, 4.0)
        page.set_playing(False)
        held = with_inputs(show_from_patch())
        window2, _ = self.make(held)
        page2 = self.editor(window2, "warm-white")
        page2.timeline.set_timed(True)
        page2.set_playing(True)
        page2._play_origin -= 60.0
        page2._advance()
        self.assertFalse(page2.playing)  # a timed input that doesn't loop stops at its end
        self.assertEqual(page2.playhead, window2.document.show.inputs["warm-white"].length)

    def test_keyframes_from_the_details_panel(self) -> None:
        window, _engine = self.make()
        page = self.editor(window, "warm-white")
        page.select_element("E1")
        process(self.app)
        details = page.details
        page.seek(1.0)
        details.toggle_key("element:opacity")
        raw = self.element(window, "warm-white", "E1")["opacity"]
        self.assertEqual([k[0] for k in keys_of(raw)], [1.0])
        page.seek(3.0)
        details._fields["element:opacity"].spin.setValue(25.0)  # a change at another time: a new key there
        raw = self.element(window, "warm-white", "E1")["opacity"]
        self.assertEqual([(k[0], k[1]) for k in keys_of(raw)], [(1.0, 1.0), (3.0, 0.25)])
        page.seek(2.0)
        process(self.app)
        self.assertAlmostEqual(details._fields["element:opacity"].spin.value(), 62.5, delta=0.6)  # half way, smooth
        diamonds = page.timeline.canvas.diamonds()
        self.assertEqual([round(d[0], 3) for d in diamonds], [1.0, 3.0])
        point = diamonds[1][2]
        self.drag(page.timeline.canvas, point, point + QPointF(page.timeline.canvas.pps, 0))  # one second later
        raw = self.element(window, "warm-white", "E1")["opacity"]
        self.assertAlmostEqual(keys_of(raw)[1][0], 4.0, delta=0.15)
        page.seek(1.0)
        details.toggle_key("element:opacity")  # there's a key here: it goes
        self.assertEqual(len(keys_of(self.element(window, "warm-white", "E1")["opacity"])), 1)

    def test_shape_drags_set_keys_when_keyframed(self) -> None:
        window, _engine = self.make()
        page = self.editor(window, "bar-sweep")
        page.select_element("E2")
        page._shape_from_viewport("S1")
        page.seek(2.0)
        page._shape_dragged("E2", "S1", {"x": 0.3, "y": 0.4})
        page.document.end_drag("Move S1")
        shape = self.element(window, "bar-sweep", "E2")["shapes"][0]
        self.assertEqual([k[0] for k in keys_of(shape["x"])], [0.0, 2.0, 4.0])  # x was keyframed: a key at 2 s
        self.assertEqual(shape["y"], 0.4)  # y wasn't: it just changed
        canvas = page.selected_canvas
        self.assertTrue(canvas.editable(shape))  # keyframed shapes can be dragged with the mouse too
        centre = canvas.shape_geometry(shape)[0]
        self.drag(canvas, centre, centre + QPointF(30, 0))
        moved = self.element(window, "bar-sweep", "E2")["shapes"][0]
        self.assertEqual(len(keys_of(moved["x"])), 3)  # the key at 2 s changed; no new one
        self.assertGreater(keys_of(moved["x"])[1][1], 0.3)


class PlayingTests(TimelineTestCase):
    def test_the_editor_shows_what_the_lights_show(self) -> None:
        """Bar sweep plays and loops the same on the lights as in the Editor's Result view."""
        window, engine = self.make()
        show = window.document.show
        renderer = EditorRenderer(None)
        engine.submit(SendToBase("bar-sweep", cut=True))
        last = 0.0
        addresses = {fx.id: fx.address for fx in show.colour_fixtures()}
        for t in (0.5, 1.3, 2.2, 3.7, 5.3, 9.7):  # past 4 s it loops
            engine.settle(t - last)
            last = t
            sent = engine.fake_output.last  # the DMX of the latest tick (frames for the screen go out less often)
            picture = renderer.render_now(EditorRequest(show, "bar-sweep", None, t))
            for light_id, rgb in picture.colours.items():
                address = addresses[light_id]
                on_lights = [sent[address - 1 + i] / 255 for i in range(3)]  # its red, green and blue channels
                np.testing.assert_allclose(rgb, on_lights, atol=0.02, err_msg=f"{light_id} at {t} s")

    def test_progress_on_the_tile(self) -> None:
        window, engine = self.make()
        engine.submit(SendToBase("bar-sweep", cut=True))
        engine.settle(1.0)
        window.bridge.poll()
        process(self.app)
        tile = window.dashboard.grid.tiles["bar-sweep"]
        self.assertAlmostEqual(tile.map.progress, 0.25, delta=0.05)
        self.assertIsNone(window.dashboard.grid.tiles["warm-white"].map.progress)


if __name__ == "__main__":
    unittest.main()

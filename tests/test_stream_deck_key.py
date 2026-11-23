"""23 Nov: where each input's numbered Stream Deck key sends it (Companion's "as each input says" keys, which work in
any show: Input 1 is the first tile on the Dashboard). The setting ("base", the default, or "overlay") saved and loaded
(an older input without it goes to Base), refused with a reason when it's wrong, in the AI guide and schema, the
Editor's choice with its undo, and the live connection's state, which the Companion module goes by."""

from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from centralight.ai_guide import describe_changes, guide_text, input_schema
from centralight.model import DECK_BASE, DECK_OVERLAY, OVERLAY_HELD, Input
from centralight.show_io import InputFileError, input_file, input_from_dict, input_to_dict, read_input_file, save_input
from tests import test_remote
from tests.qt_support import process
from tests.test_gui_editor import EditorTestCase
from tests.test_live_m16 import Client
from tests.test_overlay_m14 import held_show


class SettingTests(unittest.TestCase):
    def test_saved_and_loaded(self) -> None:
        plain = Input(id="warm", name="Warm")
        self.assertNotIn("stream_deck", input_to_dict(plain))  # (the default is left out of the file)
        flash = replace(plain, stream_deck=DECK_OVERLAY)
        self.assertEqual(input_to_dict(flash)["stream_deck"], "overlay")
        with tempfile.TemporaryDirectory() as folder:
            save_input(Path(folder), flash)
            loaded, warnings = read_input_file(input_file(Path(folder), "warm"))
            self.assertEqual((loaded.stream_deck, warnings), (DECK_OVERLAY, []))
            save_input(Path(folder), plain)
            self.assertEqual(read_input_file(input_file(Path(folder), "warm"))[0].stream_deck, DECK_BASE)

    def test_an_older_input_goes_to_base(self) -> None:
        # (from before 23 Nov: no "stream_deck", even on one whose Overlay copy shows only while held)
        inp, warnings = input_from_dict({"id": "old", "name": "Old", "overlay": "held", "tracks": []})
        self.assertEqual((inp.stream_deck, inp.overlay, warnings), (DECK_BASE, OVERLAY_HELD, []))

    def test_a_wrong_value_is_refused_with_a_reason(self) -> None:
        with self.assertRaises(InputFileError) as caught:
            input_from_dict({"id": "x", "name": "X", "stream_deck": "both", "tracks": []})
        self.assertIn('"stream_deck" must be one of base, overlay', str(caught.exception))

    def test_the_ai_guide_and_schema(self) -> None:
        show = held_show()
        self.assertEqual(input_schema(show)["properties"]["stream_deck"]["enum"], ["base", "overlay"])
        self.assertIn("`stream_deck`", guide_text(show))
        old = show.inputs["warm-white"]
        self.assertIn("Numbered key: Base → the Overlay", describe_changes(old, replace(old, stream_deck=DECK_OVERLAY)))


class EditorTests(EditorTestCase):
    def test_the_choice_on_the_input_tab(self) -> None:
        window, _engine = self.make()
        window.open_editor("warm-white")
        details = window.editor_page.details
        choice = details._fields["input:stream_deck"]
        self.assertEqual((choice.currentData(), choice.currentText()), (DECK_BASE, "Sends it to Base"))
        self.assertEqual([choice.itemText(i) for i in range(choice.count())], ["Sends it to Base", "Sends it to the Overlay"])
        self.assertIn("Input 1 is the first tile on the Dashboard", choice.toolTip())
        choice.setCurrentIndex(choice.findData(DECK_OVERLAY))
        process(self.app)
        self.assertEqual(window.document.show.inputs["warm-white"].stream_deck, DECK_OVERLAY)
        self.assertEqual(window.document.undo_stack.undoText(), "Warm white: numbered key to the Overlay")
        window.document.undo_stack.undo()
        process(self.app)
        self.assertEqual(window.document.show.inputs["warm-white"].stream_deck, DECK_BASE)
        self.assertEqual(details._fields["input:stream_deck"].currentData(), DECK_BASE)


class RemoteTests(test_remote.RemoteTestCase):
    def test_the_live_connection_sends_it(self) -> None:
        flash = replace(self.show.inputs["worship-blue"], stream_deck=DECK_OVERLAY)
        self.show = self.show.with_inputs({**self.show.inputs, "worship-blue": flash}, self.show.input_order)
        self.engine.show = self.show
        client = Client(self.server.port)
        self.addCleanup(client.close)
        state = client.message("state")["state"]
        sends = {i["id"]: i["stream_deck"] for i in state["inputs"]}
        self.assertEqual((sends["warm-white"], sends["worship-blue"]), ("base", "overlay"))


if __name__ == "__main__":
    unittest.main()

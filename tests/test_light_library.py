"""The light library (M19, 18 Nov): Open Fixture Library lights turned into light types, and the list CentraLight
carries. The converter is tested on small hand-made fixture files (the library's format), the list on what was built."""

from __future__ import annotations

import gzip
import json
import tempfile
import unittest
from pathlib import Path

from centralight.light_library import LIBRARY_FILE, LibraryModel, library, library_data, library_from_data, load
from centralight.ofl import role_of, types_from_fixture


def par(**more) -> dict:
    """An RGB par: dimmer, red, green, blue, and a shutter that must be open (6-10) to shine."""
    data = {
        "name": "Test Par",
        "categories": ["Color Changer"],
        "availableChannels": {
            "Dimmer": {"capability": {"type": "Intensity"}},
            "Red": {"capability": {"type": "ColorIntensity", "color": "Red"}},
            "Green": {"capability": {"type": "ColorIntensity", "color": "Green"}},
            "Blue": {"capability": {"type": "ColorIntensity", "color": "Blue"}},
            "Shutter": {"capabilities": [
                {"dmxRange": [0, 5], "type": "ShutterStrobe", "shutterEffect": "Closed"},
                {"dmxRange": [6, 10], "type": "ShutterStrobe", "shutterEffect": "Open"},
                {"dmxRange": [11, 255], "type": "ShutterStrobe", "shutterEffect": "Strobe"},
            ]},
            "Program": {"capabilities": [
                {"dmxRange": [0, 50], "type": "NoFunction"},
                {"dmxRange": [51, 150], "type": "Effect", "effectPreset": "ColorJump",
                 "switchChannels": {"Speed": "Jump speed"}},
                {"dmxRange": [151, 255], "type": "Effect", "effectPreset": "ColorFade",
                 "switchChannels": {"Speed": "Fade speed"}},
            ], "defaultValue": 0},
            "Jump speed": {"capability": {"type": "EffectSpeed", "speedStart": "slow", "speedEnd": "fast"}},
            "Fade speed": {"capability": {"type": "EffectSpeed", "speedStart": "slow", "speedEnd": "fast"}},
            "Pan": {"capability": {"type": "Pan", "angleStart": "0deg", "angleEnd": "540deg"},
                    "fineChannelAliases": ["Pan fine"], "defaultValue": "50%"},
        },
        "modes": [
            {"name": "3-channel", "channels": ["Red", "Green", "Blue"]},
            {"name": "7-channel", "channels": ["Dimmer", "Red", "Green", "Blue", "Shutter", "Program", "Speed"]},
            {"name": "Pan", "channels": ["Pan", "Pan fine", None, "Dimmer"]},
            {"name": "Effects only", "channels": ["Program"]},
        ],
    }
    data.update(more)
    return data


class ConverterTests(unittest.TestCase):
    def test_each_mode_becomes_a_type(self) -> None:
        kinds, skipped = types_from_fixture(par(), "Test Maker")
        self.assertEqual([k.name for k in kinds], ["Test Maker Test Par (3-channel)", "Test Maker Test Par (7-channel)",
                                                   "Test Maker Test Par (Pan)"])
        self.assertEqual(len(skipped), 1)
        self.assertIn("Effects only", skipped[0])  # (nothing in it gives light)
        seven = kinds[1]
        self.assertEqual([ch.role for ch in seven.channels],
                         ["dimmer", "red", "green", "blue", "strobe", "program", "program_speed"])
        self.assertEqual((seven.maker, seven.model, seven.short, seven.mode), ("Test Maker", "Test Par", "Test Par",
                                                                               "7-channel"))
        self.assertEqual(seven.channels[4].default, 8)  # (the shutter open: the middle of 6-10)
        self.assertEqual(seven.channels[6].name, "Speed")  # (a switching channel: what it is at its default)
        self.assertEqual(seven.channel_values((1.0, 0.0, 0.0)), [255, 255, 0, 0, 8, 0, 0])

    def test_fine_channels_and_percent_defaults(self) -> None:
        pan = types_from_fixture(par(), "Test Maker")[0][2]
        self.assertEqual([(ch.role, ch.name, ch.default) for ch in pan.channels],
                         [("other", "Pan", 128), ("other", "Pan fine", 0), ("other", "Not used", 0), ("dimmer", "", 0)])

    def test_the_job_over_most_of_the_range(self) -> None:
        self.assertEqual(role_of({"capabilities": [
            {"dmxRange": [0, 9], "type": "NoFunction"},
            {"dmxRange": [10, 99], "type": "ShutterStrobe", "shutterEffect": "Strobe"},
            {"dmxRange": [100, 255], "type": "Effect", "effectName": "Auto"},
        ]}), "program")
        for colour, role in (("Warm White", "white"), ("UV", "uv"), ("Lime", "lime"), ("Cyan", "other")):
            self.assertEqual(role_of({"capability": {"type": "ColorIntensity", "color": colour}}), role)
        self.assertEqual(role_of({"capability": {"type": "ColorPreset"}}), "macro")
        self.assertEqual(role_of({"capability": {"type": "Tilt"}}), "other")
        self.assertEqual(role_of({}), "other")

    def test_a_pixel_light_repeats_its_channels(self) -> None:
        data = {
            "name": "Pixel Bar",
            "categories": ["Pixel Bar"],
            "matrix": {"pixelKeys": [[["B", "A", None, "C"]]], "pixelGroups": {"Left": ["A", "B"], "Right": ["C"]}},
            "templateChannels": {
                "Red $pixelKey": {"capability": {"type": "ColorIntensity", "color": "Red"},
                                  "fineChannelAliases": ["Red $pixelKey fine"]},
                "Green $pixelKey": {"capability": {"type": "ColorIntensity", "color": "Green"}},
                "Blue $pixelKey": {"capability": {"type": "ColorIntensity", "color": "Blue"}},
            },
            "availableChannels": {"Master": {"capability": {"type": "Intensity"}}},
            "modes": [
                {"name": "ABC", "channels": [{"insert": "matrixChannels", "repeatFor": "eachPixelABC",
                                              "channelOrder": "perPixel",
                                              "templateChannels": ["Red $pixelKey", "Green $pixelKey",
                                                                   "Blue $pixelKey"]}, "Master"]},
                {"name": "Groups", "channels": [{"insert": "matrixChannels", "repeatFor": "eachPixelGroup",
                                                 "channelOrder": "perChannel",
                                                 "templateChannels": ["Red $pixelKey", "Red $pixelKey fine",
                                                                      "Green $pixelKey", "Blue $pixelKey"]}]},
            ],
        }
        abc, groups = types_from_fixture(data, "Maker")[0]
        self.assertEqual([ch.name for ch in abc.channels][:4], ["Red A", "Green A", "Blue A", "Red B"])
        self.assertEqual(abc.channel_count, 10)
        self.assertEqual(abc.channel_values((1.0, 0.5, 0.0))[:6], [255, 128, 0, 255, 128, 0])
        self.assertEqual([(ch.role, ch.name) for ch in groups.channels],
                         [("red", "Red Left"), ("red", "Red Right"), ("other", "Red Left fine"),
                          ("other", "Red Right fine"), ("green", "Green Left"), ("green", "Green Right"),
                          ("blue", "Blue Left"), ("blue", "Blue Right")])
        self.assertFalse(groups.has_dimmer)

    def test_smoke_machines_are_left_out(self) -> None:
        kinds, skipped = types_from_fixture(par(categories=["Smoke"]), "Maker")
        self.assertEqual(kinds, [])
        self.assertIn("not a light", skipped[0])

    def test_a_mode_naming_a_channel_the_file_lacks_is_left_out(self) -> None:
        data = par(modes=[{"name": "Odd", "channels": ["Red", "Green", "Blue", "Mystery"]}])
        kinds, skipped = types_from_fixture(data, "Maker")
        self.assertEqual(kinds, [])
        self.assertIn("Mystery", skipped[0])


class LibraryFileTests(unittest.TestCase):
    def test_the_file_form_round_trips(self) -> None:
        kinds, _ = types_from_fixture(par(), "Test Maker")
        model = LibraryModel("Test Maker", "Test Par", "test-maker/test-par", ("Color Changer",), tuple(kinds))
        data = json.loads(json.dumps(library_data([model], source="Test", url="https://example.test", made="2026-11-18")))
        again = library_from_data(data)
        self.assertEqual(again.models, (model,))
        self.assertEqual(again.makers(), ["Test Maker"])
        self.assertEqual(again.made, "2026-11-18")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "lib.json.gz"
            path.write_bytes(gzip.compress(json.dumps(data).encode("utf-8")))
            self.assertEqual(load(path).models, (model,))
            path.write_bytes(b"not gzip")
            self.assertIn("couldn't read", load(path).problem)
        self.assertTrue(library_from_data({"format": "nope"}).problem)

    def test_the_library_centralight_carries(self) -> None:
        self.assertTrue(LIBRARY_FILE.is_file())
        lib = library()
        self.assertEqual(lib.problem, "")
        self.assertGreater(len(lib.models), 500)
        self.assertEqual(lib.source, "Open Fixture Library")
        self.assertEqual(lib.makers(), sorted(lib.makers(), key=str.casefold))
        ids = [kind.id for model in lib.models for kind in model.modes]
        self.assertEqual(len(ids), len(set(ids)))  # (every mode its own type)
        q12 = next(m for m in lib.models if m.key == "chauvet-dj/slimpar-q12-bt")
        eight = next(k for k in q12.modes if k.mode == "8-channel")
        self.assertEqual([ch.role for ch in eight.channels],
                         ["red", "green", "blue", "amber", "strobe", "program", "program_speed", "dimmer"])
        self.assertTrue(all(kind.gives_light for model in lib.models for kind in model.modes))
        self.assertTrue((LIBRARY_FILE.parent / "OPEN_FIXTURE_LIBRARY.txt").is_file())
        self.assertNotIn("Fog Fury Jett Pro", {m.name for m in lib.models})  # (a smoke machine)


if __name__ == "__main__":
    unittest.main()

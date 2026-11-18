"""Light types (M19, 18 Nov): each channel's job, the DMX they give, show files that carry them, and the Console."""

from __future__ import annotations

import json
import random
import unittest
from dataclasses import replace

from centralight import light_types
from centralight.commands import ConsoleRelease, ConsoleSet, SendToBase
from centralight.console import ConsoleState, channel_rows, step_from
from centralight.light_types import (
    DIMMER,
    LPC018,
    SLIMPAR9,
    Channel,
    LightTypeError,
    make_type,
    type_from_dict,
    type_to_dict,
)
from centralight.model import ROLE_COLOUR, ROLE_HOUSE, Fixture, MapSpot
from centralight.new_show import show_from_patch
from centralight.output import encode_dmx, to_byte
from centralight.show_edit import add_colour_light, replace_light_type, set_light_type
from centralight.show_io import ShowError, show_from_dict, show_to_dict
from tests.fakes import FakeEngine
from tests.gui_fixtures import with_inputs

# How the encoder worked before light types (14 Nov), kept here as the yardstick for the built-in types
OLD_PROFILES = {
    "dimmer": {"channels": 1, "dimmer": 0},
    "slimpar9": {"channels": 9, "red": 0, "green": 1, "blue": 2, "dimmer": 7},
    "lpc018": {"channels": 7, "dimmer": 0, "red": 1, "green": 2, "blue": 3},
}
OLD_CHANNELS = {
    "slimpar9": ("red", "green", "blue", "amber", "macro", "strobe", "program", "dimmer", "dimmer_speed"),
    "lpc018": ("dimmer", "red", "green", "blue", "strobe", "program", "program_speed"),
    "dimmer": ("dimmer",),
}


def old_encode(fixtures, colours, levels, extras=None, size=512) -> bytes:
    dmx = bytearray(size)
    for fx in fixtures:
        prof = OLD_PROFILES[fx.profile]
        base = fx.address - 1
        if base + prof["channels"] > size:
            continue
        if fx.role == ROLE_COLOUR:
            r, g, b = colours.get(fx.id, (0.0, 0.0, 0.0))
            if "red" in prof:
                dmx[base + prof["red"]] = to_byte(r)
                dmx[base + prof["green"]] = to_byte(g)
                dmx[base + prof["blue"]] = to_byte(b)
                dmx[base + prof["dimmer"]] = 255
            else:
                dmx[base + prof["dimmer"]] = to_byte(max(r, g, b))
            more = extras.get(fx.id) if extras else None
            if more:
                offsets = {attr: index for index, attr in enumerate(OLD_CHANNELS[fx.profile])}
                for attr, value in more.items():
                    if attr in offsets:
                        dmx[base + offsets[attr]] = max(0, min(255, int(value)))
        else:
            level = levels.get(fx.id, 0.0)
            if "red" in prof:
                for part in ("red", "green", "blue"):
                    dmx[base + prof[part]] = to_byte(level)
                dmx[base + prof["dimmer"]] = 255
            else:
                dmx[base + prof["dimmer"]] = to_byte(level)
    return bytes(dmx)


def rgbw(**more) -> light_types.LightType:
    channels = [Channel("dimmer"), Channel("red"), Channel("green"), Channel("blue"), Channel("white"),
                Channel("strobe", "Shutter", 255)]
    return light_types.register(make_type("Test RGBW par (6-channel)", channels, short="RGBW", maker="Test",
                                          model="RGBW par", **more))


class BuiltInTests(unittest.TestCase):
    def test_the_built_in_types_send_exactly_what_they_did(self) -> None:
        """Random colours, levels and Console values, on the church patch plus an LPC018 and dimmers as colour
        lights and SlimPARs as house lights: byte for byte what the old encoder sent."""
        show = show_from_patch()
        fixtures = list(show.fixtures) + [
            Fixture("lpc-1", "LPC018 1", "lpc018", 77),
            Fixture("dim-colour", "Dimmer as colour", "dimmer", 84, map=MapSpot(0.5, 0.5)),
            Fixture("par-house", "SlimPAR as house", "slimpar9", 90, role=ROLE_HOUSE),
            Fixture("past-end", "Past the end", "slimpar9", 506),  # (runs past a short universe below)
        ]
        chance = random.Random(19)
        for _ in range(300):
            colours = {fx.id: tuple(chance.choice((0.0, 1.0, chance.random(), -0.2, 1.4)) for _ in range(3))
                       for fx in fixtures if chance.random() < 0.9}
            levels = {fx.id: chance.random() for fx in fixtures}
            extras = {}
            for fx in fixtures:
                names = OLD_CHANNELS[fx.profile]
                if chance.random() < 0.5:
                    extras[fx.id] = {attr: chance.choice((0, 255, chance.randint(-20, 300), chance.random() * 255))
                                     for attr in chance.sample(names + ("nope",), min(3, len(names)))}
            size = chance.choice((512, 100))
            self.assertEqual(encode_dmx(fixtures, colours, levels, size, extras),
                             old_encode(fixtures, colours, levels, extras, size))

    def test_the_built_in_types_are_what_the_console_had(self) -> None:
        self.assertEqual([attr for attr, _ in SLIMPAR9.attributes], list(OLD_CHANNELS["slimpar9"]))
        self.assertEqual([attr for attr, _ in LPC018.attributes], list(OLD_CHANNELS["lpc018"]))
        self.assertEqual([attr for attr, _ in DIMMER.attributes], ["dimmer"])
        self.assertEqual(dict(SLIMPAR9.attributes)["macro"], "Colour macro")
        self.assertEqual((SLIMPAR9.short, LPC018.short), ("SlimPAR", "LPC018"))
        for kind in (SLIMPAR9, LPC018, DIMMER):
            self.assertIs(light_types.get(kind.id), kind)
            self.assertTrue(light_types.is_built_in(kind.id))


class ChannelTests(unittest.TestCase):
    def test_white_leds_take_the_white_in_the_colour(self) -> None:
        kind = rgbw()
        self.assertEqual(kind.channel_values((1.0, 1.0, 1.0)), [255, 0, 0, 0, 255, 255])  # (white: the white LEDs)
        self.assertEqual(kind.channel_values((1.0, 0.5, 0.5)), [255, 128, 0, 0, 128, 255])  # (pink: red and white)
        self.assertEqual(kind.channel_values((0.0, 0.0, 1.0)), [255, 0, 0, 255, 0, 255])
        self.assertEqual(kind.read_back([255, 128, 0, 0, 128, 255]), {"dimmer": 255, "red": 255, "green": 128,
                                                                    "blue": 128, "strobe": 255})

    def test_a_channel_sits_at_its_normally_until_the_console_sets_it(self) -> None:
        kind = rgbw()
        self.assertEqual(kind.channel_values((0.0, 0.0, 0.0))[5], 255)  # (its shutter open: it can shine)
        self.assertEqual(kind.channel_values((0.0, 0.0, 0.0), {"strobe": 40})[5], 40)
        self.assertEqual(kind.default_of("strobe"), 255.0)
        self.assertEqual(kind.default_of("dimmer"), 255.0)

    def test_without_a_dimmer_channel_brightness_scales_the_colour(self) -> None:
        kind = make_type("RGB amber bar", [Channel("red"), Channel("green"), Channel("blue"), Channel("amber")])
        self.assertIn(("dimmer", "Dimmer"), kind.attributes)  # (the Console's brightness still works)
        self.assertEqual(kind.channel_values((1.0, 0.5, 0.0)), [255, 128, 0, 0])
        self.assertEqual(kind.channel_values((1.0, 0.5, 0.0), {"dimmer": 51, "amber": 200}), [51, 26, 0, 40])
        self.assertEqual(kind.read_back([51, 26, 0, 40])["dimmer"], 255)

    def test_a_light_without_colour_shows_a_level(self) -> None:
        white = make_type("White par", [Channel("dimmer"), Channel("white"), Channel("strobe", "", 8)])
        self.assertFalse(white.has_colour)
        self.assertEqual([attr for attr, _ in white.attributes], ["dimmer", "strobe"])
        self.assertEqual(white.channel_values((0.2, 0.6, 0.4)), [153, 255, 8])  # (the brightest, on its dimmer)
        self.assertEqual(white.read_back([153, 255, 8]), {"dimmer": 153, "strobe": 8})
        uv = make_type("UV bar", [Channel("uv"), Channel("program")])
        self.assertEqual(uv.channel_values((0.0, 0.0, 1.0), {"program": 70}), [255, 70])
        self.assertEqual(uv.house_values(0.5), [128, 0])

    def test_a_pixel_bar_shows_one_colour_on_all_its_parts(self) -> None:
        channels = [Channel("red", f"Red {n}") for n in (1, 2)] + [Channel("green", f"Green {n}") for n in (1, 2)]
        channels += [Channel("blue", "Blue 1"), Channel("blue", "Blue 2"), Channel("dimmer"), Channel("strobe"),
                     Channel("strobe"), Channel("other", "Pan"), Channel("other", "Pan")]
        bar = make_type("Bar", channels)
        self.assertEqual(bar.keys, ("red", "red", "green", "green", "blue", "blue", "dimmer", "strobe", "strobe_2",
                                    "other:pan", "other:pan_2"))
        self.assertEqual(bar.channel_values((1.0, 0.0, 0.5), {"strobe_2": 9, "other:pan_2": 7}),
                         [255, 255, 0, 0, 128, 128, 255, 0, 9, 0, 7])
        self.assertEqual(dict(bar.attributes)["strobe_2"], "Strobe 2")
        self.assertEqual(dict(bar.attributes)["other:pan_2"], "Pan 2")

    def test_rows_on_the_console_keep_one_order(self) -> None:
        bar = light_types.register(make_type("Bar", [Channel("other", "Gobo"), Channel("red"), Channel("green"),
                                                     Channel("blue"), Channel("strobe"), Channel("other", "Pan"),
                                                     Channel("uv")]))
        rows = [attr for attr, _ in channel_rows([bar.id, "slimpar9", "lpc018"])]
        self.assertEqual(rows, ["red", "green", "blue", "amber", "uv", "macro", "strobe", "program", "program_speed",
                                "dimmer", "dimmer_speed", "other:gobo", "other:pan"])


class MakingTypesTests(unittest.TestCase):
    def test_the_same_type_has_the_same_id(self) -> None:
        a = rgbw()
        self.assertEqual(rgbw(), a)
        self.assertEqual(rgbw().id, a.id)
        self.assertTrue(a.id.startswith("rgbw-"))
        other = make_type(a.name, [*a.channels[:5], Channel("strobe", "Shutter", 0)], short="RGBW", maker="Test",
                          model="RGBW par")
        self.assertNotEqual(other.id, a.id)  # (another "normally": another type)

    def test_tidy_names_and_no_normally_on_colour_channels(self) -> None:
        kind = make_type("  Par   18  ", [Channel("red", "red", 40), Channel("green"), Channel("blue"),
                                         Channel("dimmer", "Master", 9)])
        self.assertEqual(kind.name, "Par 18")
        self.assertEqual(kind.channels[0], Channel("red"))  # (named as its job, with nothing to set)
        self.assertEqual(kind.channels[3], Channel("dimmer", "Master"))

    def test_file_form_round_trips_and_is_checked(self) -> None:
        kind = rgbw(mode="6-channel")
        data = type_to_dict(kind)
        self.assertEqual(data["channels"][5], {"role": "strobe", "name": "Shutter", "default": 255})
        self.assertEqual(data["channels"][0], {"role": "dimmer"})
        self.assertEqual(type_from_dict(json.loads(json.dumps(data))), kind)
        for bad, why in (
            ({**data, "channels": []}, "no channels"),
            ({**data, "name": " "}, "needs a name"),
            ({**data, "channels": [{"role": "smoke"}]}, "isn't a job"),
            ({**data, "channels": [{"role": "dimmer", "default": 300}]}, "0 to 255"),
            ({**data, "channels": [{"role": "strobe"}, {"role": "program"}]}, "no channel that gives light"),
            ("nope", "must be an object"),
        ):
            with self.subTest(why=why), self.assertRaises(LightTypeError) as caught:
                type_from_dict(bad)
            self.assertIn(why, str(caught.exception))

    def test_names_from_maker_model_and_mode(self) -> None:
        self.assertEqual(light_types.type_name("Betopper", "LPC018", "7-channel", 7), "Betopper LPC018 (7-channel)")
        self.assertEqual(light_types.type_name("", "Par", "", 1), "Par (1 channel)")
        self.assertEqual(light_types.type_name("", "", "", 3), "Light (3 channels)")


class ShowFileTests(unittest.TestCase):
    def show_with_custom(self):
        show = show_from_patch()
        show, new_id = add_colour_light(show, kind=rgbw())
        return show, new_id

    def test_a_show_carries_its_types(self) -> None:
        show, new_id = self.show_with_custom()
        fx = show.fixture(new_id)
        self.assertEqual((fx.name, fx.address, fx.channels), ("RGBW 1", 77, 6))
        data = json.loads(json.dumps(show_to_dict(show)))
        self.assertEqual([t["id"] for t in data["light_types"]], [rgbw().id])
        again, warnings = show_from_dict(data)
        self.assertEqual(warnings, [])
        self.assertEqual(again.fixture(new_id), fx)

    def test_a_show_with_only_built_in_types_is_written_as_before(self) -> None:
        self.assertNotIn("light_types", show_to_dict(show_from_patch()))

    def test_a_type_changed_by_hand_takes_its_lights_with_it(self) -> None:
        show, new_id = self.show_with_custom()
        data = json.loads(json.dumps(show_to_dict(show)))
        data["light_types"][0]["channels"][5]["default"] = 0  # (its id now says something else)
        again, _warnings = show_from_dict(data)
        fx = again.fixture(new_id)
        self.assertNotEqual(fx.profile, rgbw().id)
        self.assertEqual(fx.kind.default_of("strobe"), 0.0)
        self.assertEqual(show_to_dict(again)["light_types"][0]["id"], fx.profile)

    def test_a_light_whose_type_is_missing_or_broken_stops_the_show_loading(self) -> None:
        show, new_id = self.show_with_custom()
        data = json.loads(json.dumps(show_to_dict(show)))
        missing = {**data, "light_types": []}
        with self.assertRaises(ShowError) as caught:
            show_from_dict(missing)
        self.assertIn("unknown profile", str(caught.exception))
        broken = json.loads(json.dumps(data))
        broken["light_types"][0]["channels"] = [{"role": "smoke"}]
        with self.assertRaises(ShowError) as caught:
            show_from_dict(broken)
        self.assertIn("isn't a job", str(caught.exception))

    def test_set_and_replace_a_type(self) -> None:
        show = show_from_patch()
        changed = set_light_type(show, ["slimpar-101", "slimpar-102"], rgbw())
        self.assertEqual([changed.fixture(i).profile for i in ("slimpar-101", "slimpar-102", "slimpar-103")],
                         [rgbw().id, rgbw().id, "slimpar9"])
        edited = make_type("Edited", [Channel("dimmer"), Channel("red"), Channel("green"), Channel("blue")])
        again = replace_light_type(changed, rgbw().id, edited)
        self.assertEqual({again.fixture(i).profile for i in ("slimpar-101", "slimpar-102")}, {edited.id})
        near_end = set_light_type(replace(show, fixtures=(replace(show.fixtures[2], address=510),)),
                                  [show.fixtures[2].id], rgbw())
        self.assertEqual(near_end.fixtures[0].address, 507)  # (its 6 channels end on 512)

    def test_add_a_light_names_it_after_its_type(self) -> None:
        show = show_from_patch()
        show, first = add_colour_light(show, kind=LPC018)
        show, second = add_colour_light(show, kind=LPC018)
        show, par = add_colour_light(show)
        self.assertEqual([show.fixture(i).name for i in (first, second, par)], ["LPC018 1", "LPC018 2", "SlimPAR 109"])
        self.assertEqual([show.fixture(i).address for i in (first, second, par)], [77, 84, 91])


class ConsoleTests(unittest.TestCase):
    def test_the_console_holds_and_releases_a_custom_type(self) -> None:
        kind = rgbw()
        show = set_light_type(with_inputs(show_from_patch()), ["slimpar-101"], kind)
        engine = FakeEngine(show)
        engine.submit(SendToBase("worship-blue", cut=True))
        engine.settle(0.1)

        def sent() -> tuple[int, ...]:
            return tuple(engine.fake_output.frames[-1][4:10])

        before = sent()
        self.assertEqual((before[0], before[5]), (255, 255))  # (full, shutter open)
        engine.submit(ConsoleSet({"slimpar-101": {"strobe": 30, "dimmer": 100, "red": 255, "green": 0, "blue": 0}}))
        self.assertEqual(sent(), (100, 255, 0, 0, 0, 30))
        engine.submit(ConsoleRelease(lights=("slimpar-101",), fade=0.0))
        engine.settle(0.1)
        self.assertEqual(sent(), before)

    def test_a_step_reads_white_back_into_the_colour(self) -> None:
        kind = rgbw()
        step = step_from(["a"], {"a": tuple(kind.channel_values((1.0, 0.5, 0.5)))}, {"a": kind.id})
        self.assertEqual(step.looks["a"], (1.0, 0.502, 0.502, 1.0))

    def test_a_type_with_another_normally_keeps_it_while_nothing_is_held(self) -> None:
        kind = rgbw()
        state = ConsoleState()
        state.set_lights({"a": kind.id})
        state.set({"a": {"strobe": 255, "dimmer": 255}}, 0.0, 0.0)  # (both at their usual: nothing extra to send)
        _rgb, raw = state.merge("a", (0.5, 0.5, 0.5), 0.1)
        self.assertEqual(raw, {})
        state.set({"a": {"strobe": 0}}, 0.0, 0.2)
        self.assertEqual(state.merge("a", (0.5, 0.5, 0.5), 0.3)[1], {"strobe": 0})


if __name__ == "__main__":
    unittest.main()

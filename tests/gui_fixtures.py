"""Shows and engines for the window tests and the layout check."""

from __future__ import annotations

import colorsys
import json
from dataclasses import replace

import numpy as np

from centralight.commands import SendToBase, SetHouse
from centralight.layouts import save_layout_as
from centralight.model import Automation, GlobalVar, HouseLight, Input, Show, Transition
from centralight.new_show import follow_screen_input, show_from_patch
from centralight.show_io import freeze, input_to_dict
from tests.fakes import FakeEngine

LONG_NAMES = {
    "slimpar-101": "SlimPAR 101 — top wash, stage left above the drum kit",
    "slimpar-104": "SlimPAR 104 — top wash, stage right by the projector screen",
    "slimpar-105": "SlimPAR 105 — side fill on the left-hand wall near the entrance",
    "slimpar-108": "SlimPAR 108 — side fill on the right-hand wall by the kitchen",
}
LONG_HOUSE = {"dimmer-1": "Festoon lights along the back wall", "dimmer-4": "Key lights for the preacher"}


def paint_input(input_id: str, name: str, colour: str, infinite: bool = True, ai: bool = False) -> Input:
    return Input(
        id=input_id,
        name=name,
        infinite=infinite,
        ai=ai,
        transition=Transition("fade", 1.0),
        tracks=freeze([[{"id": "E1", "kind": "paint", "fill": {"type": "colour", "colour": colour}}], [], [], []]),
    )


def speed_name(long: bool = False) -> str:
    return "breathing_speed_for_the_pillars" if long else "speed"


def stage_shapes_input(long: bool = False) -> Input:
    """Follow screen with a gradient and two soft shapes on top (a busy input for the Editor),
    with an expression on its opacity and two local variables."""
    tracks = freeze([
        [{"id": "E1", "kind": "ndi", "source": "Media Background Pane"}],
        [{"id": "E2", "kind": "paint", "opacity": {"value": 0.85, "expr": f"0.6 + 0.4 * wave(time, {speed_name(long)})"}, "blend": "screen",
          "fill": {"type": "linear", "from": "#7A3CFF", "to": "#00B7FF", "angle": 20},
          "shapes": [
              {"id": "S1", "type": "rect", "x": 0.3, "y": 0.5, "w": 0.18, "h": 1.2, "rotation": 12, "softness": 0.4, "colour": "#FFFFFF"},
              {"id": "S2", "type": "ellipse", "x": 0.72, "y": 0.6, "w": 0.25, "h": 0.5, "softness": 0.6, "colour": "#FF6F59"},
          ],
          "mask": {"exclude": ["slimpar-106", "slimpar-107"]}}],
        [], [],
    ])
    name = "Stage shapes over the screen, with a long name for checking" if long else "Stage shapes"
    local_vars = freeze([
        {"name": "a_really_long_local_name_check" if long else "width", "type": "number", "value": 0.18},
        {"name": "glow", "type": "colour", "value": {"value": "#FF6F59", "expr": "mix(value, accent, 0.5)"}},
    ])
    return Input(id="stage-shapes", name=name, colour="#C04DFF", transition=Transition("wipe", 1.5, 0.0, 0.3),
                 tracks=tracks, locals=local_vars, hotkey="F5")


def candlelight_input(long: bool = False) -> Input:
    """A timed input (20 s, looping) with something of everything on its timeline: a warm wash that crosses
    into amber where two elements overlap, a flickering glow with keyframes, and in / out transitions."""
    tracks = freeze([
        [{"id": "E1", "kind": "paint", "fill": {"type": "colour", "colour": "#FF8A2A"}, "start": 0.0, "end": 12.0,
          "in": {"kind": "fade", "duration": 2.0}},
         {"id": "E3", "kind": "paint", "fill": {"type": "colour", "colour": "#FFB347"}, "start": 10.0, "end": 20.0,
          "out": {"kind": "fade", "duration": 3.0}}],
        [{"id": "E2", "kind": "paint", "fill": {"type": "none"}, "start": 4.0, "end": 16.0, "blend": "screen",
          "opacity": {"keys": [[0, 0.0], [2, 0.8], [6, 0.4, "linear"], [10, 0.9]]},
          "shapes": [{"id": "S1", "type": "ellipse", "x": {"keys": [[0, 0.3], [12, 0.7]]}, "y": 0.7, "w": 0.4, "h": 0.5,
                      "softness": 0.8, "colour": "#FFE0A0"}]}],
        [], [],
    ])
    name = "Christmas candlelight for the carol service with a very long name" if long else "Candlelight"
    return Input(id="christmas", name=name, colour="#FF8A2A", infinite=False, length=20.0, end="loop",
                 transition=Transition("fade", 1.0), tracks=tracks)


def with_inputs(show: Show, long: bool = False) -> Show:
    inputs = [
        follow_screen_input(),
        stage_shapes_input(long),
        paint_input("warm-white", "Warm white", "#FFB46B"),
        paint_input("pillars-purple", "Pillars purple with a slow breathing pulse" if long else "Pillars purple", "#7A3CFF", ai=True),
        paint_input("worship-blue", "Worship blue", "#2050FF"),
        candlelight_input(long),
        paint_input("off", "All off", "#000000"),
    ]
    globals_ = (
        GlobalVar(speed_name(long), "number", 1.0, 0.0, 4.0, True),
        GlobalVar("accent", "colour", "#C04DFF", dashboard=True),
        GlobalVar("party_mode_for_the_youth_night" if long else "party", "switch", False, dashboard=True),
        GlobalVar("spare", "number", 0.5),  # not on the dashboard
    )
    show = replace(show, globals=globals_).with_inputs({i.id: i for i in inputs}, tuple(i.id for i in inputs))
    automations = sample_automations(long)
    return show.with_automations({a.id: a for a in automations}, tuple(a.id for a in automations))


def sample_automations(long: bool = False) -> list[Automation]:
    """Two automations with every kind of row, for the Automation tab's tests and layout check."""
    welcome = Automation(
        "sunday-welcome", "Sunday morning welcome as people arrive for the ten o'clock service" if long else "Sunday welcome",
        conditions=freeze([{"type": "days", "days": ["sun"]}, {"type": "time", "from": "09:00", "to": "12:30"},
                           {"type": "colour_black", "invert": True}]),
        actions=freeze([{"do": "base", "input": "warm-white", "transition": {"kind": "fade", "duration": 3}},
                        {"do": "house", "light": "dimmer-4", "level": 0.7, "fade": 2},
                        {"do": "overlay", "input": "pillars-purple", "slot": 2, "state": "on", "delay": 30}]),
        on_end=freeze([{"do": "base", "input": "follow-screen"}]),
    )
    evening = Automation(
        "evening", "Evening worship", logic="any",
        conditions=freeze([{"type": "input_live", "input": "worship-blue", "where": "base"},
                           {"type": "variable", "name": speed_name(long), "compare": ">", "value": 2},
                           {"type": "dates", "from": "12-01", "to": "01-06", "year": 2026},
                           {"type": "ndi", "source": "Media Background Pane"},
                           {"type": "audio", "band": "bass", "above": 0.6, "for": 5},
                           {"type": "sun", "when": "after", "event": "sunset", "minutes": -30}]),
        actions=freeze([{"do": "variable", "name": "accent", "value": "#FF6F59"}, {"do": "master", "level": 0.8, "fade": 5},
                        {"do": "layout", "layout": "main"}, {"do": "automation", "automation": "sunday-welcome", "enabled": False},
                        {"do": "fade_black", "fade": 3, "all": True}, {"do": "overlay_off", "slot": 3}]),
    )
    return [welcome, evening]


def long_names_show() -> Show:
    show = show_from_patch(name="Central Church Bristol — Sunday Evening Service with a long name")
    fixtures = tuple(replace(fx, name=LONG_NAMES.get(fx.id, fx.name)) for fx in show.fixtures)
    house = tuple(HouseLight(h.fixture, LONG_HOUSE.get(h.fixture, h.label)) for h in show.house)
    show = replace(show, fixtures=fixtures, house=house)
    show, _layout_id = save_layout_as(show, "Christmas Eve candlelit carol service with the big tree by the stage")
    return with_inputs(show, long=True)


def rainbow_canvas(width: int = 160, height: int = 90) -> np.ndarray:
    """A picture that's a rainbow from left to right (premultiplied RGBA)."""
    canvas = np.ones((4, height, width), dtype=np.float32)
    for x in range(width):
        canvas[:3, :, x] = np.array(colorsys.hsv_to_rgb(x / width * 0.85, 0.85, 1.0), dtype=np.float32)[:, None]
    return canvas


def rainbow(engine: FakeEngine):
    """A render function giving every colour light its own bright colour (from where it is across a rainbow)."""
    made: dict = {}

    def render(_now):
        lights = engine._lights
        if made.get("key") != lights.key:
            layer = np.ones((4, lights.count), dtype=np.float32)
            for i, x in enumerate(lights.x):
                layer[:3, i] = colorsys.hsv_to_rgb(min(0.999, float(x)) * 0.85, 0.85, 1.0)
            made.update(key=lights.key, layer=layer)
        return made["layer"]

    return render


def busy_show_engine(show: Show | None = None) -> FakeEngine:
    show = show or with_inputs(show_from_patch())
    engine = FakeEngine(show)
    if "follow-screen" in show.inputs:
        engine.submit(SendToBase("follow-screen", cut=True))
    engine._render = rainbow(engine)
    engine.submit(SetHouse("dimmer-1", level=0.35, on=True))
    engine.submit(SetHouse("dimmer-4", level=0.8, on=True))
    engine.settle(1.0)
    return engine


def ai_problem_reply(long: bool = False) -> str:
    """A chat AI's reply that CentraLight can't use yet: a made-up light, an unknown function and a missing
    picture (for the AI window's problems list)."""
    light = "pillar-left-by-the-organ-and-the-choir-stalls" if long else "pillar-left"
    data = {
        "format": "centralight-input", "version": 1, "id": "pillars-breathe", "name": "Pillars breathe",
        "tracks": [
            [{"id": "E1", "kind": "paint", "fill": {"type": "colour", "colour": {"expr": "mix(#4B1A99, #C9A6FF, wobble(time))"}},
              "mask": {"exclude": [light, "slimpar-102"]}}],
            [{"id": "E2", "kind": "image", "file": "media/sunrise-over-the-hills-behind-the-church.png" if long else "media/sunrise.png"}],
            [], [],
        ],
    }
    return "Here you go! The pillars breathe purple:\n\n```json\n" + json.dumps(data, indent=2) + "\n```\n"


def ai_edit_reply(show: Show, long: bool = False) -> str:
    """A chat AI's good reply to "Edit with AI" for pillars-purple: a new name, a breathing colour, and a changed id
    (which CentraLight keeps as it was)."""
    data = input_to_dict(show.inputs["pillars-purple"])
    data["id"] = "pillars-breathing"
    data["name"] = "Pillars purple, breathing a little faster than before for the last song" if long else "Pillars breathing"
    data["tracks"][0][0]["fill"] = {"type": "colour", "colour": {"expr": "mix(#4B1A99, #C9A6FF, wave(time, 1/4))"}}
    data["transition"] = {"kind": "fade", "duration": 2}
    return "Sure, here's the faster version:\n\n```json\n" + json.dumps(data, indent=2) + "\n```\n"

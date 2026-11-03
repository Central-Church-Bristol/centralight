"""
The Central Church lights: what each one is and which DMX channels it uses.

PATCH comes from the MagicQ show "Central Church.shw" (universe 1), checked in the
room with usb_dmx_patch_test.py on 2 Nov 2026: every SlimPAR and the festoon answer
as labelled, both key lights are on channel 4, and nothing answers on channels 2
or 3 (MagicQ patches dimmers there), so those two are left out.
If a light is re-addressed, change it here: the patch test and the pixel mapper
both read this file.
"""

from __future__ import annotations

# Channel offsets from start_ch (0 = first channel). Channels not listed are sent as 0.
PROFILES = {
    # Generic 1-channel dimmer: level only
    "dimmer": {"channels": 1, "dimmer": 0},
    # Chauvet DJ SlimPAR Q12 USB, 9-ch mode: Red, Green, Blue, Amber, Colour macro,
    # Strobe, Program, Dimmer, Dimmer speed. 0 on the extras = no macro / strobe / program.
    "slimpar9": {"channels": 9, "red": 0, "green": 1, "blue": 2, "dimmer": 7},
}

# group = the MagicQ group the light is in
PATCH = [
    {"name": "Dimmer 1", "group": "Festoon", "start_ch": 1, "profile": "dimmer"},
    {"name": "Dimmer 4", "group": "Key Lights", "start_ch": 4, "profile": "dimmer"},  # both key lights
    {"name": "SlimPAR 101", "group": "Top Wash", "start_ch": 5, "profile": "slimpar9"},
    {"name": "SlimPAR 102", "group": "Top Wash", "start_ch": 14, "profile": "slimpar9"},
    {"name": "SlimPAR 103", "group": "Top Wash", "start_ch": 23, "profile": "slimpar9"},
    {"name": "SlimPAR 104", "group": "Top Wash", "start_ch": 32, "profile": "slimpar9"},
    {"name": "SlimPAR 105", "group": "Side Fill", "start_ch": 41, "profile": "slimpar9"},
    {"name": "SlimPAR 106", "group": "Pillars", "start_ch": 50, "profile": "slimpar9"},
    {"name": "SlimPAR 107", "group": "Pillars", "start_ch": 59, "profile": "slimpar9"},
    {"name": "SlimPAR 108", "group": "Side Fill", "start_ch": 68, "profile": "slimpar9"},
]


def fixture(name: str) -> dict:
    """Look up a light in PATCH by name, e.g. fixture("SlimPAR 101")."""
    for fx in PATCH:
        if fx["name"] == name:
            return fx
    raise KeyError(f"no light called {name!r} in fixtures.PATCH")


def channel_range(fx: dict) -> str:
    """'5-13' for a 9-channel light, '1' for a 1-channel dimmer."""
    lo = fx["start_ch"]
    hi = lo + PROFILES[fx["profile"]]["channels"] - 1
    return str(lo) if hi == lo else f"{lo}-{hi}"


def set_fixture(dmx: bytearray, fx: dict, rgb: tuple[int, int, int]) -> None:
    """Write one colour into a light's channels (master dimmer full on colour lights).

    A 1-channel dimmer gets the brightest of the three values.
    """
    prof = PROFILES[fx["profile"]]
    base = fx["start_ch"] - 1
    if "red" in prof:
        for part, value in zip(("red", "green", "blue"), rgb):
            dmx[base + prof[part]] = value
        dmx[base + prof["dimmer"]] = 255
    else:
        dmx[base + prof["dimmer"]] = max(rgb)

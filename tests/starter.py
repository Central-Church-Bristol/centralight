"""A show as a new one used to start, with "Follow screen": for the tests written before 6 Nov that edit or send it
(since then a new show starts with no inputs: tests/test_shows.py checks that)."""

from __future__ import annotations

from centralight.new_show import add_starter_inputs
from centralight.new_show import create_show as _create_show


def create_show(*args, **kwargs):
    return add_starter_inputs(_create_show(*args, **kwargs))

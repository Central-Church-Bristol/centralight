"""Expressions and variables: what's allowed, what's blocked, the functions, colour maths,
falling back when something's wrong, circles between locals, and which variable wins."""

from __future__ import annotations

import math
import time
import unittest

from centralight.expressions import (
    FUNCTIONS,
    MEMORY_LIMIT,
    Env,
    ExpressionError,
    check,
    compile_expression,
    continuous_phase,
    describe_value,
    evaluate_text,
    reserved_names,
)
from centralight.model import GlobalVar, Input, Show
from centralight.params import evaluate
from centralight.show_io import freeze
from centralight.variables import (
    Scope,
    check_variable_name,
    coerce_global,
    evaluate_locals,
    global_default,
    initial_globals,
    local_order,
    reconcile_globals,
    saveable,
    use_scope,
)


def run(text: str, **names) -> object:
    values = dict(names)
    t = values.pop("t", 0.0)
    value = values.pop("value", 0.0)
    return evaluate_text(text, Env(values.get, value=value, t=t, time=values.pop("time", t)))


class AllowedSyntaxTests(unittest.TestCase):
    def test_maths(self) -> None:
        self.assertEqual(run("1 + 2 * 3"), 7.0)
        self.assertEqual(run("(1 + 2) * 3"), 9.0)
        self.assertEqual(run("2 ** 3 ** 2"), 512.0)
        self.assertEqual(run("7 % 3"), 1.0)
        self.assertEqual(run("-x + +1", x=3.0), -2.0)
        self.assertAlmostEqual(run("1 / 3"), 1 / 3)

    def test_comparisons_and_logic(self) -> None:
        self.assertEqual(run("1 < x < 3", x=2.0), 1.0)
        self.assertEqual(run("1 < x < 3", x=3.0), 0.0)
        self.assertEqual(run("x == 2 and y", x=2.0, y=0.5), 0.5)
        self.assertEqual(run("0 or 0.25"), 0.25)
        self.assertEqual(run("not x", x=0.0), 1.0)
        self.assertEqual(run("1 if time < 2 else 0", time=1.0), 1.0)
        self.assertEqual(run("1 if time < 2 else 0", time=2.5), 0.0)
        self.assertEqual(run("True + true + false"), 2.0)

    def test_specials_and_audio(self) -> None:
        env = Env(None, value=0.4, t=1.5, time=7.0, audio={"bass": 0.8})
        self.assertEqual(evaluate_text("value * 2", env), 0.8)
        self.assertEqual(evaluate_text("t + time", env), 8.5)
        self.assertEqual(evaluate_text("audio.bass", env), 0.8)
        self.assertEqual(evaluate_text("audio.treble", env), 0.0)  # no audio yet: 0
        self.assertAlmostEqual(evaluate_text("pi", env), math.pi)

    def test_colour_literals_and_maths(self) -> None:
        self.assertEqual(run("#FF0000 + #0000FF"), (1.0, 0.0, 1.0))
        self.assertEqual(run("#FF8000 * 0.5"), (0.5, 128 / 255 * 0.5, 0.0))
        self.assertEqual(run("0.5 + #000000"), (0.5, 0.5, 0.5))
        self.assertEqual(run('hex("00FF00")'), (0.0, 1.0, 0.0))
        self.assertEqual(run('hex("#00FF00")'), (0.0, 1.0, 0.0))
        self.assertEqual(run("mix(#000000, #FFFFFF, 0.25)"), (0.25, 0.25, 0.25))
        self.assertEqual(run("#FF0000 == #FF0000"), 1.0)

    def test_compiled_once(self) -> None:
        self.assertIs(compile_expression("sin(time)"), compile_expression("sin(time)"))
        self.assertEqual(compile_expression("a + b * sin(time) + audio.bass").names, {"a", "b", "time"})


class BlockedSyntaxTests(unittest.TestCase):
    BLOCKED = (
        "__import__('os')",
        "().__class__",
        "x.real",
        "open('C:/Windows/win.ini')",
        "eval('1')",
        "[1, 2][0]",
        "(lambda: 1)()",
        "[x for x in (1, 2)]",
        "'text'",
        "f'{1}'",
        "(x := 2)",
        "sin(x=1)",
        "max(*(1, 2))",
        "1 // 2",
        "1 & 2",
        "~1",
        "1 if x in (1, 2) else 0",
        "x is None",
        "{1: 2}",
        "audio.__class__",
        "audio",
        "sin",
        "value * 2 #oops",
        "#FFF",
        "",
        "   ",
        "1 +",
        "x" * 401,
        "1+" * 150 + "1",  # nested far too deep
        "10 ** 1000000000000000000",
        "sin(1, 2)",
        "hex(x)",
        'hex("12345")',
    )

    def test_each_is_refused_with_a_reason(self) -> None:
        for text in self.BLOCKED:
            with self.subTest(text=text[:40]):
                with self.assertRaises(ExpressionError) as caught:
                    compile_expression(text)
                self.assertTrue(str(caught.exception))
                self.assertEqual(check(text), str(caught.exception))

    def test_unknown_names_fail_when_run(self) -> None:
        for text in ("__builtins__", "os", "nothing_here * 2"):
            with self.assertRaises(ExpressionError):
                run(text)

    def test_runtime_problems_are_errors_not_crashes(self) -> None:
        for text in ("1 / 0", "1 % 0", "(-8) ** 0.5", "10.0 ** 400", "sqrt(-1)", "1e308 * 10", "sin(#FF0000)", "#FF0000 < 1"):
            with self.subTest(text=text):
                with self.assertRaises(ExpressionError):
                    run(text)


class FunctionTests(unittest.TestCase):
    def test_maths(self) -> None:
        self.assertAlmostEqual(run("sin(pi / 2)"), 1.0)
        self.assertAlmostEqual(run("cos(0)"), 1.0)
        self.assertAlmostEqual(run("tan(0)"), 0.0)
        self.assertEqual(run("abs(-2)"), 2.0)
        self.assertEqual(run("abs(#000000 - #FF0000)"), (1.0, 0.0, 0.0))
        self.assertEqual(run("min(3, 1, 2)"), 1.0)
        self.assertEqual(run("max(3, 1, 2)"), 3.0)
        self.assertEqual(run("clamp(1.5)"), 1.0)
        self.assertEqual(run("clamp(-1, 0.2, 0.8)"), 0.2)
        self.assertEqual(run("lerp(10, 20, 0.5)"), 15.0)
        self.assertEqual(run("step(0.5, 0.4)"), 0.0)
        self.assertEqual(run("step(0.5, 0.5)"), 1.0)
        self.assertEqual(run("smoothstep(0, 1, 0.5)"), 0.5)
        self.assertEqual(run("smoothstep(0, 1, 2)"), 1.0)
        self.assertAlmostEqual(run("fract(2.75)"), 0.75)
        self.assertAlmostEqual(run("fract(-0.25)"), 0.75)
        self.assertEqual(run("floor(2.7)"), 2.0)
        self.assertEqual(run("ceil(2.2)"), 3.0)
        self.assertEqual(run("round(2.5)"), 3.0)  # halves go up (not Python's banker's rounding)
        self.assertEqual(run("sqrt(9)"), 3.0)

    def test_waves(self) -> None:
        self.assertEqual(run("pulse(0.1)"), 1.0)
        self.assertEqual(run("pulse(0.6)"), 0.0)
        self.assertEqual(run("pulse(0.3, 2, 0.25)"), 0.0)  # 0.6 of the way through a 2/s cycle
        self.assertAlmostEqual(run("saw(1.25)"), 0.25)
        self.assertAlmostEqual(run("tri(0.25)"), 0.5)
        self.assertAlmostEqual(run("tri(0.5)"), 1.0)
        self.assertEqual(run("square(0.75)"), 0.0)
        self.assertAlmostEqual(run("wave(0)"), 0.0)
        self.assertAlmostEqual(run("wave(0.5)"), 1.0)
        self.assertAlmostEqual(run("wave(0.25, 2)"), 1.0)

    def test_noise_is_smooth_and_the_same_every_time(self) -> None:
        values = [run("noise(t)", t=i / 100) for i in range(300)]
        self.assertTrue(all(0.0 <= v < 1.0 for v in values))
        self.assertLess(max(abs(a - b) for a, b in zip(values, values[1:])), 0.05)
        self.assertEqual(values, [run("noise(t)", t=i / 100) for i in range(300)])
        self.assertNotEqual(run("noise(0.5, 1)"), run("noise(0.5, 2)"))

    def test_random_is_stable_per_element(self) -> None:
        a = evaluate_text("random()", Env(element="warm/E1"))
        self.assertEqual(a, evaluate_text("random()", Env(element="warm/E1", t=5.0)))
        self.assertNotEqual(a, evaluate_text("random()", Env(element="warm/E2")))
        self.assertNotEqual(a, evaluate_text("random(1)", Env(element="warm/E1")))
        self.assertTrue(0.0 <= a < 1.0)

    def test_colours(self) -> None:
        self.assertEqual(run("rgb(1, 0.5, 2)"), (1.0, 0.5, 1.0))
        self.assertEqual(run("hsv(0)"), (1.0, 0.0, 0.0))
        self.assertEqual(run("hsv(1/3)"), (0.0, 1.0, 0.0))
        self.assertEqual(run("hsv(0, 0, 0.5)"), (0.5, 0.5, 0.5))
        self.assertEqual(run("brightness(#FF8000, 0.5)"), (0.5, 128 / 255 * 0.5, 0.0))
        self.assertEqual(describe_value(run("#FF8000")), "#FF8000")
        self.assertEqual(describe_value(run("1/3")), "0.333")

    def test_every_function_has_help(self) -> None:
        for name, function in FUNCTIONS.items():
            self.assertIn(f"{name}(", function.help)

    def test_fast_enough(self) -> None:
        compiled = compile_expression("mix(#FF0000, accent, clamp(sin(time * speed) * 0.5 + 0.5))")
        env = Env({"accent": (0.0, 0.0, 1.0), "speed": 2.0}.get, time=1.0)
        started = time.perf_counter()
        for _ in range(10_000):
            compiled.run(env)
        per_call_us = (time.perf_counter() - started) * 100  # 10 000 calls: seconds * 1e6 / 1e4
        self.assertLess(per_call_us, 50)  # a few microseconds in practice


class ParameterTests(unittest.TestCase):
    """Expressions inside parameters (what the engine draws with)."""

    def test_value_is_the_base_value(self) -> None:
        self.assertEqual(evaluate({"value": 0.3, "expr": "value * 2"}, 0.0, 1.0), 0.6)
        self.assertEqual(evaluate({"expr": "t * 2"}, 1.5, 0.0), 3.0)

    def test_broken_expressions_fall_back_to_the_base_value(self) -> None:
        self.assertEqual(evaluate({"value": 0.3, "expr": "1 / 0"}, 0.0, 1.0), 0.3)
        self.assertEqual(evaluate({"value": 0.3, "expr": "sin("}, 0.0, 1.0), 0.3)
        self.assertEqual(evaluate({"value": 0.3, "expr": "missing + 1"}, 0.0, 1.0), 0.3)
        self.assertEqual(evaluate({"value": 0.3, "expr": "#FF0000"}, 0.0, 1.0), 0.3)  # a colour where a number goes
        self.assertEqual(evaluate({"expr": "1 / 0"}, 0.0, 0.7), 0.7)  # no base value: the default

    def test_colour_parameters(self) -> None:
        self.assertEqual(evaluate({"expr": "#00FF00"}, 0.0, (0, 0, 0), "colour"), (0.0, 1.0, 0.0))
        self.assertEqual(evaluate({"expr": "0.5"}, 0.0, (0, 0, 0), "colour"), (0.5, 0.5, 0.5))  # a number is a grey
        self.assertEqual(evaluate({"value": "#FF0000", "expr": "value * 0.5"}, 0.0, (0, 0, 0), "colour"), (0.5, 0.0, 0.0))
        self.assertEqual(evaluate({"expr": "rgb(2, 0, 0) * 3"}, 0.0, (0, 0, 0), "colour"), (1.0, 0.0, 0.0))  # kept in range

    def test_variables_come_from_the_scope(self) -> None:
        scope = Scope({"width": 0.25}, {"speed": 2.0, "width": 0.9}, time=3.0)
        with use_scope(scope):
            self.assertEqual(evaluate({"expr": "width"}, 0.0, 0.0), 0.25)  # the local wins over the global
            self.assertEqual(evaluate({"expr": "speed * time"}, 0.0, 0.0), 6.0)
        self.assertEqual(evaluate({"value": 0.1, "expr": "speed"}, 0.0, 0.0), 0.1)  # outside any input: no globals


class LocalTests(unittest.TestCase):
    def make(self, *locals_) -> Input:
        return Input(id="test", name="Test", locals=freeze(list(locals_)))

    def test_order_follows_what_uses_what(self) -> None:
        inp = self.make(
            {"name": "d", "type": "number", "value": {"expr": "c * 2"}},
            {"name": "c", "type": "number", "value": 5},
            {"name": "e", "type": "number", "value": {"expr": "d + speed"}},
        )
        values, errors = evaluate_locals(inp, 0.0, {"speed": 1.0})
        self.assertEqual(values, {"c": 5.0, "d": 10.0, "e": 11.0})
        self.assertEqual(errors, {})

    def test_circles_are_caught(self) -> None:
        inp = self.make(
            {"name": "a", "type": "number", "value": {"value": 1.0, "expr": "b + 1"}},
            {"name": "b", "type": "number", "value": {"value": 2.0, "expr": "a * 2"}},
            {"name": "c", "type": "number", "value": {"value": 3.0, "expr": "c + 1"}},
            {"name": "ok", "type": "number", "value": {"expr": "time"}},
        )
        _order, errors = local_order(inp.locals)
        self.assertEqual(set(errors), {"a", "b", "c"})
        self.assertIn("circle", errors["a"])
        values, _errors = evaluate_locals(inp, 4.0)
        self.assertEqual(values, {"a": 1.0, "b": 2.0, "c": 3.0, "ok": 4.0})  # plain values for the circle

    def test_colour_locals(self) -> None:
        inp = self.make({"name": "tint", "type": "colour", "value": {"expr": "hsv(time / 10)"}})
        values, _errors = evaluate_locals(inp, 0.0)
        self.assertEqual(values["tint"], (1.0, 0.0, 0.0))


class GlobalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.speed = GlobalVar("speed", "number", 0.5, 0.0, 2.0, True)
        self.accent = GlobalVar("accent", "colour", "#C04DFF")
        self.party = GlobalVar("party", "switch", False)
        self.show = Show(globals=(self.speed, self.accent, self.party))

    def test_defaults(self) -> None:
        self.assertEqual(global_default(self.speed), 0.5)
        self.assertEqual(global_default(self.accent), (0xC0 / 255, 0x4D / 255, 1.0))
        self.assertEqual(global_default(self.party), 0.0)

    def test_values_from_the_stream_deck(self) -> None:
        self.assertEqual(coerce_global(self.speed, "0.7"), 0.7)
        self.assertEqual(coerce_global(self.speed, "9"), 2.0)  # kept in range
        self.assertIsNone(coerce_global(self.speed, "fast"))
        self.assertEqual(coerce_global(self.accent, "FF6F59"), (1.0, 0x6F / 255, 0x59 / 255))
        self.assertEqual(coerce_global(self.accent, "#FF6F59"), (1.0, 0x6F / 255, 0x59 / 255))
        self.assertIsNone(coerce_global(self.accent, "purple"))
        self.assertEqual(coerce_global(self.party, "on"), 1.0)
        self.assertEqual(coerce_global(self.party, "toggle", current=1.0), 0.0)
        self.assertEqual(coerce_global(self.party, "toggle", current=0.0), 1.0)
        self.assertIsNone(coerce_global(self.party, "maybe"))

    def test_saved_values_and_changes_to_the_show(self) -> None:
        values = initial_globals(self.show, {"speed": 1.5, "accent": "#00FF00", "party": True, "gone": 3})
        self.assertEqual(values, {"speed": 1.5, "accent": (0.0, 1.0, 0.0), "party": 1.0})
        self.assertEqual(saveable(values), {"speed": 1.5, "accent": "#00FF00", "party": 1.0})
        narrower = Show(globals=(GlobalVar("speed", "number", 0.5, 0.0, 1.0), GlobalVar("accent", "number", 0.2), GlobalVar("new", "switch", True)))
        changed = reconcile_globals(narrower, self.show, values)
        self.assertEqual(changed, {"speed": 1.0, "accent": 0.2, "new": 1.0})  # clamped; type changed → default; new → default

    def test_names(self) -> None:
        self.assertEqual(check_variable_name("speed"), "")
        for bad in ("", "2fast", "with space", "sin", "time", "audio", "if", "x" * 40):
            self.assertNotEqual(check_variable_name(bad), "", bad)
        self.assertIn("already", check_variable_name("speed", {"speed"}))
        self.assertIn("value", reserved_names())



class SmoothRateTests(unittest.TestCase):
    """wave() and friends keep their place in the cycle when their rate changes (dragging a speed global),
    instead of jumping to wherever time * new rate happens to be."""

    def at(self, compiled, t: float, rate: float, memory, element: str = "in/E1") -> float:
        return compiled.run(Env({"speed": rate}.get, 0.0, t, t, None, element, memory))

    def test_a_steady_rate_gives_the_usual_values(self) -> None:
        for name, extra in (("wave", ""), ("saw", ""), ("tri", ""), ("square", ""), ("pulse", ", 0.3")):
            smooth = compile_expression(f"{name}(time, speed{extra})")
            fixed = compile_expression(f"{name}(time, 0.7{extra})")
            memory: dict = {}
            for i in range(200):
                t = 3.0123 + i * 0.025  # never exactly on an edge of square() or pulse()
                self.assertAlmostEqual(self.at(smooth, t, 0.7, memory), self.at(fixed, t, 0.7, None), places=6, msg=name)

    def test_a_rate_change_carries_on_from_where_it_was(self) -> None:
        compiled, memory = compile_expression("saw(time, speed)"), {}
        self.assertAlmostEqual(self.at(compiled, 101.3, 1.0, memory), 0.3)
        self.assertAlmostEqual(self.at(compiled, 101.325, 1.25, memory), 0.33125)  # on from 0.3, at the new pace
        self.assertAlmostEqual(self.at(compiled, 101.325, 1.25, None), 0.65625)  # without a memory it would jump
        self.assertAlmostEqual(self.at(compiled, 101.35, 1.25, memory), 0.3625)

    def test_time_going_back_starts_it_again(self) -> None:
        compiled, memory = compile_expression("saw(time, speed)"), {}
        self.at(compiled, 10.3, 1.0, memory)
        self.assertAlmostEqual(self.at(compiled, 10.4, 2.0, memory), 0.5)
        self.assertAlmostEqual(self.at(compiled, 0.2, 2.0, memory), 0.4)  # sent again: fract(0.2 * 2)

    def test_each_element_keeps_its_own_place(self) -> None:
        compiled, memory = compile_expression("saw(time, speed)"), {}
        self.at(compiled, 5.25, 1.0, memory, "in/E1")
        self.at(compiled, 5.25, 1.0, memory, "in/E2")
        self.assertAlmostEqual(self.at(compiled, 5.5, 2.0, memory, "in/E1"), 0.75)
        self.assertAlmostEqual(self.at(compiled, 5.5, 1.0, memory, "in/E2"), 0.5)

    def test_fixed_rates_need_no_memory(self) -> None:
        memory: dict = {}
        compile_expression("wave(time, 0.5) + saw(time)").run(Env(None, 0.0, 1.0, 1.0, None, "x", memory))
        self.assertEqual(memory, {})

    def test_the_memory_stays_small(self) -> None:
        memory: dict = {}
        for i in range(MEMORY_LIMIT + 5):
            continuous_phase(memory, ("x", i), 1.0, 1.0)
        self.assertLessEqual(len(memory), MEMORY_LIMIT)


if __name__ == "__main__":
    unittest.main()

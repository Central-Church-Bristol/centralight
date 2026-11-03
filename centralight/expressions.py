"""Expressions: small formulas that set a value, like a spreadsheet cell.

    sin(time) * 0.5 + 0.5              a slow pulse between 0 and 1
    mix(#FF0000, accent, audio.bass)   red, moving towards the show variable colour "accent" with the bass
    value * speed                      the setting's own value, times the show variable "speed"
    1 if time < 2 else 0               on for the first two seconds

A result is a number or a colour (r, g, b from 0 to 1). A number used where a colour is
needed is a grey.

Safety: the text is parsed with Python's ast module and only the things listed below are
allowed: numbers, #RRGGBB colours, + - * / % **, comparisons, and / or / not,
"a if c else b", calls to the functions in FUNCTIONS, variable names, and audio.<band>.
There are no strings (apart from colours), no attributes, no imports and no way to reach
anything else. The text is compiled once into plain Python functions (closures) and never
passed to eval().

Names are looked up in this order: the input's local variables, show variables, then
t (element time), time (input time) and value (the setting's own value before the
expression). audio.level / bass / mid / treble / beat / pitch are 0-1 (0 until audio is set up).

The cycle functions (wave, pulse, saw, tri, square) take a rate in cycles per second. While an
input runs they remember where they are in their cycle, so changing the rate (dragging a "speed"
global, say) changes the pace smoothly instead of jumping to another point in the cycle. With a
steady rate the result is exactly fract(t * rate), as you'd expect.
"""

from __future__ import annotations

import ast
import colorsys
import keyword
import math
import re
import zlib
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Union

Colour = tuple[float, float, float]
Value = Union[float, Colour]

MAX_LENGTH = 400
MEMORY_LIMIT = 2000  # remembered cycle positions per running input (only grows if formulas keep being edited)
SPECIALS = ("t", "time", "value")
AUDIO_NAMES = ("level", "bass", "mid", "treble", "beat", "pitch")
CONSTANTS = {"pi": math.pi}
COLOUR_LITERAL = re.compile(r"(?<![\"'])#([0-9A-Fa-f]{6})(?![0-9A-Za-z_])")
QUOTED = re.compile(r"\"[^\"]*\"|'[^']*'")


class ExpressionError(Exception):
    """Something wrong with an expression, in words a person can act on."""


# ---------------------------------------------------------------- values


def _is_colour(value: Any) -> bool:
    return type(value) is tuple


def _number(value: Value, what: str) -> float:
    if type(value) is tuple:
        raise ExpressionError(f"{what} needs a number, not a colour")
    return value  # type: ignore[return-value]


def _truthy(value: Value) -> bool:
    return any(v != 0.0 for v in value) if type(value) is tuple else value != 0.0


def _each(a: Value, b: Value, f: Callable[[float, float], float]) -> Value:
    """f on two numbers, or on each of r, g, b when either is a colour."""
    ta, tb = type(a) is tuple, type(b) is tuple
    if not ta and not tb:
        return f(a, b)  # type: ignore[arg-type]
    if ta and tb:
        return (f(a[0], b[0]), f(a[1], b[1]), f(a[2], b[2]))  # type: ignore[index]
    if ta:
        return (f(a[0], b), f(a[1], b), f(a[2], b))  # type: ignore[index, arg-type]
    return (f(a, b[0]), f(a, b[1]), f(a, b[2]))  # type: ignore[index, arg-type]


def _each1(a: Value, f: Callable[[float], float]) -> Value:
    return (f(a[0]), f(a[1]), f(a[2])) if type(a) is tuple else f(a)  # type: ignore[index, arg-type]


def _div(a: float, b: float) -> float:
    if b == 0.0:
        raise ExpressionError("divides by zero")
    return a / b


def _mod(a: float, b: float) -> float:
    if b == 0.0:
        raise ExpressionError("divides by zero (with %)")
    return a % b


def _pow(a: float, b: float) -> float:
    try:
        return math.pow(a, b)
    except (OverflowError, ValueError) as exc:
        raise ExpressionError("a power that can't be worked out (too big, or a negative number to a fraction)") from exc


BINARY = {
    ast.Add: lambda a, b: _each(a, b, lambda x, y: x + y),
    ast.Sub: lambda a, b: _each(a, b, lambda x, y: x - y),
    ast.Mult: lambda a, b: _each(a, b, lambda x, y: x * y),
    ast.Div: lambda a, b: _each(a, b, _div),
    ast.Mod: lambda a, b: _each(a, b, _mod),
    ast.Pow: lambda a, b: _each(a, b, _pow),
}

COMPARE = {
    ast.Lt: lambda a, b: a < b,
    ast.LtE: lambda a, b: a <= b,
    ast.Gt: lambda a, b: a > b,
    ast.GtE: lambda a, b: a >= b,
    ast.Eq: lambda a, b: a == b,
    ast.NotEq: lambda a, b: a != b,
}


# ---------------------------------------------------------------- functions


def _clamp(x: Value, lo: Value = 0.0, hi: Value = 1.0) -> Value:
    return _each(_each(x, lo, max), hi, min)


def _mix(a: Value, b: Value, amount: Value) -> Value:
    t = _number(amount, "mix's amount")
    return _each(a, b, lambda x, y: x + (y - x) * t)


def _step(edge: Value, x: Value) -> float:
    return 1.0 if _number(x, "step") >= _number(edge, "step") else 0.0


def _smoothstep(e0: Value, e1: Value, x: Value) -> float:
    a, b, v = _number(e0, "smoothstep"), _number(e1, "smoothstep"), _number(x, "smoothstep")
    if a == b:
        return 1.0 if v >= b else 0.0
    t = min(1.0, max(0.0, (v - a) / (b - a)))
    return t * t * (3.0 - 2.0 * t)


def _fract(x: float) -> float:
    return x - math.floor(x)


def _round(x: float) -> float:
    return float(math.floor(x + 0.5))


def _sqrt(x: float) -> float:
    if x < 0:
        raise ExpressionError("sqrt of a negative number")
    return math.sqrt(x)


def _phase(t: Value, rate: Value) -> float:
    return _fract(_number(t, "the time") * _number(rate, "the rate"))


def _pulse(t: Value, rate: Value = 1.0, width: Value = 0.5) -> float:
    return 1.0 if _phase(t, rate) < _number(width, "pulse's width") else 0.0


def _saw(t: Value, rate: Value = 1.0) -> float:
    return _phase(t, rate)


def _tri(t: Value, rate: Value = 1.0) -> float:
    return 1.0 - abs(2.0 * _phase(t, rate) - 1.0)


def _square(t: Value, rate: Value = 1.0) -> float:
    return 1.0 if _phase(t, rate) < 0.5 else 0.0


def _wave(t: Value, rate: Value = 1.0) -> float:
    return 0.5 - 0.5 * math.cos(2.0 * math.pi * _phase(t, rate))


# Each cycle function as a shape of its phase (0-1), for the smooth-rate version in compiled formulas
SHAPES: dict[str, Callable[..., float]] = {
    "pulse": lambda phase, width=0.5: 1.0 if phase < _number(width, "pulse's width") else 0.0,
    "saw": lambda phase: phase,
    "tri": lambda phase: 1.0 - abs(2.0 * phase - 1.0),
    "square": lambda phase: 1.0 if phase < 0.5 else 0.0,
    "wave": lambda phase: 0.5 - 0.5 * math.cos(2.0 * math.pi * phase),
}


def continuous_phase(memory: dict | None, key: Any, t: float, rate: float) -> float:
    """Where a cycle is (0-1) at t. With a memory (one per running input), it carries on from where it was the
    last time, at the new rate, so a rate change doesn't make it jump; t going backwards (a restart) starts it
    again from fract(t * rate)."""
    if memory is None:
        return _fract(t * rate)
    last = memory.get(key)
    if last is None or t < last[0]:
        phase = _fract(t * rate)
    else:
        phase = _fract(last[1] + (t - last[0]) * rate)
    if len(memory) >= MEMORY_LIMIT and key not in memory:
        memory.clear()
    memory[key] = (t, phase)
    return phase


def _hash01(*parts: Any) -> float:
    """A stable number from 0 to 1 for these parts (the same every run)."""
    return zlib.crc32("|".join(str(p) for p in parts).encode("utf-8")) / 4294967296.0


def _noise(t: Value, seed: Value = 0.0) -> float:
    """Smooth random wandering between 0 and 1 (a new random target every second)."""
    x = _number(t, "noise's time")
    s = _number(seed, "noise's seed")
    i = math.floor(x)
    f = x - i
    f = f * f * (3.0 - 2.0 * f)
    a, b = _hash01("noise", s, i), _hash01("noise", s, i + 1)
    return a + (b - a) * f


def _rgb(r: Value, g: Value, b: Value) -> Colour:
    return tuple(min(1.0, max(0.0, _number(v, "rgb"))) for v in (r, g, b))  # type: ignore[return-value]


def _hsv(h: Value, s: Value = 1.0, v: Value = 1.0) -> Colour:
    hue = _fract(_number(h, "hsv's hue"))
    sat = min(1.0, max(0.0, _number(s, "hsv's saturation")))
    val = min(1.0, max(0.0, _number(v, "hsv's brightness")))
    return colorsys.hsv_to_rgb(hue, sat, val)


def _brightness(colour: Value, amount: Value) -> Value:
    return _each(colour, _number(amount, "brightness's amount"), lambda x, y: x * y)


def _min(*values: Value) -> float:
    return min(_number(v, "min") for v in values)


def _max(*values: Value) -> float:
    return max(_number(v, "max") for v in values)


def _math1(f: Callable[[float], float], name: str) -> Callable[[Value], float]:
    return lambda x: f(_number(x, name))


@dataclass(frozen=True)
class Function:
    run: Callable[..., Value]
    least: int
    most: int
    help: str


FUNCTIONS: dict[str, Function] = {
    "sin": Function(_math1(math.sin, "sin"), 1, 1, "sin(x): the sine of x (x in radians)"),
    "cos": Function(_math1(math.cos, "cos"), 1, 1, "cos(x): the cosine of x"),
    "tan": Function(_math1(math.tan, "tan"), 1, 1, "tan(x): the tangent of x"),
    "abs": Function(lambda x: _each1(x, abs), 1, 1, "abs(x): x without its minus sign"),
    "min": Function(_min, 2, 8, "min(a, b, …): the smallest"),
    "max": Function(_max, 2, 8, "max(a, b, …): the biggest"),
    "clamp": Function(_clamp, 1, 3, "clamp(x, low=0, high=1): x kept between low and high"),
    "lerp": Function(_mix, 3, 3, "lerp(a, b, amount): from a (amount 0) to b (amount 1)"),
    "mix": Function(_mix, 3, 3, "mix(a, b, amount): the same as lerp; works on colours too"),
    "step": Function(_step, 2, 2, "step(edge, x): 0 below the edge, 1 from it up"),
    "smoothstep": Function(_smoothstep, 3, 3, "smoothstep(low, high, x): a smooth 0 → 1 between low and high"),
    "fract": Function(_math1(_fract, "fract"), 1, 1, "fract(x): the part after the decimal point"),
    "floor": Function(_math1(lambda x: float(math.floor(x)), "floor"), 1, 1, "floor(x): rounded down"),
    "ceil": Function(_math1(lambda x: float(math.ceil(x)), "ceil"), 1, 1, "ceil(x): rounded up"),
    "round": Function(_math1(_round, "round"), 1, 1, "round(x): to the nearest whole number"),
    "sqrt": Function(_math1(_sqrt, "sqrt"), 1, 1, "sqrt(x): the square root"),
    "pulse": Function(_pulse, 1, 3, "pulse(t, rate=1, width=0.5): 1 for the first part of each cycle, then 0"),
    "saw": Function(_saw, 1, 2, "saw(t, rate=1): ramps 0 → 1, then jumps back"),
    "tri": Function(_tri, 1, 2, "tri(t, rate=1): 0 → 1 → 0, in straight lines"),
    "square": Function(_square, 1, 2, "square(t, rate=1): 1 for half of each cycle, then 0"),
    "wave": Function(_wave, 1, 2, "wave(t, rate=1): a smooth 0 → 1 → 0, starting at 0. rate = cycles per second "
                                  "(0.25: one every 4 s); a changing rate (e.g. a speed show variable) changes the pace smoothly"),
    "noise": Function(_noise, 1, 2, "noise(t, seed=0): smooth random wandering between 0 and 1, to a new value each "
                                    "time t passes a whole number"),
    "random": Function(lambda *_: 0.0, 0, 1, "random(seed=0): a random 0-1 that stays the same for this element"),
    "rgb": Function(_rgb, 3, 3, "rgb(r, g, b): a colour from three 0-1 values"),
    "hsv": Function(_hsv, 1, 3, "hsv(hue, saturation=1, brightness=1): a colour; hue 0-1 goes round the rainbow"),
    "hex": Function(lambda *_: (0.0, 0.0, 0.0), 1, 1, 'hex("FF8800"): a colour from its code (#FF8800 works too)'),
    "brightness": Function(_brightness, 2, 2, "brightness(colour, amount): the colour, darker (amount < 1) or brighter"),
}


def reserved_names() -> set[str]:
    """Names variables can't have (they'd hide something an expression needs)."""
    return set(FUNCTIONS) | set(SPECIALS) | set(CONSTANTS) | {"audio"} | set(keyword.kwlist) | {"true", "false"}


# ---------------------------------------------------------------- what an expression can see


class Env:
    """Everything one evaluation can see."""

    __slots__ = ("lookup", "value", "t", "time", "audio", "element", "memory")

    def __init__(
        self,
        lookup: Callable[[str], Value | None] | None = None,
        value: Value = 0.0,
        t: float = 0.0,
        time: float | None = None,
        audio: Mapping[str, float] | None = None,
        element: str = "",
        memory: dict | None = None,
    ) -> None:
        self.lookup = lookup
        self.value = value
        self.t = t
        self.time = t if time is None else time
        self.audio = audio or {}
        self.element = element
        self.memory = memory  # the running input's cycle positions (see continuous_phase), or None

    def name(self, name: str) -> Value:
        if self.lookup is not None:
            found = self.lookup(name)
            if found is not None:
                return found
        if name == "t":
            return self.t
        if name == "time":
            return self.time
        if name == "value":
            return self.value
        if name in CONSTANTS:
            return CONSTANTS[name]
        raise ExpressionError(f'there\'s no variable called "{name}"')


# ---------------------------------------------------------------- compiling


@dataclass(frozen=True)
class Compiled:
    text: str
    run_raw: Callable[[Env], Value]
    names: frozenset[str]  # variable names it reads (not functions or audio)

    def run(self, env: Env) -> Value:
        """The result, checked: a finite number or colour. Raises ExpressionError."""
        try:
            result = self.run_raw(env)
        except ExpressionError:
            raise
        except (ArithmeticError, ValueError, TypeError) as exc:
            raise ExpressionError(f"it can't be worked out ({exc})") from exc
        if type(result) is tuple:
            if not all(math.isfinite(v) for v in result):
                raise ExpressionError("the colour isn't a real colour (infinite or not a number)")
            return result
        if not math.isfinite(result):
            raise ExpressionError("the result is infinite or not a number")
        return float(result)


def _prepare(text: str) -> str:
    """#RRGGBB colour codes become hex("RRGGBB") calls, so Python can read them."""
    return COLOUR_LITERAL.sub(lambda m: f'hex("{m.group(1)}")', text)


def _hex_colour(text: str) -> Colour:
    code = text.strip().lstrip("#")
    if not re.fullmatch(r"[0-9A-Fa-f]{6}", code):
        raise ExpressionError(f'hex() needs six hex digits like "FF8800", not "{text}"')
    return tuple(int(code[i:i + 2], 16) / 255.0 for i in (0, 2, 4))  # type: ignore[return-value]


class _Builder:
    def __init__(self) -> None:
        self.names: set[str] = set()

    def build(self, node: ast.AST) -> Callable[[Env], Value]:
        method = getattr(self, f"_{type(node).__name__}", None)
        if method is None:
            raise ExpressionError(_not_allowed(node))
        return method(node)

    def _Expression(self, node: ast.Expression):  # noqa: N802
        return self.build(node.body)

    def _Constant(self, node: ast.Constant):  # noqa: N802
        value = node.value
        if isinstance(value, bool):
            number = 1.0 if value else 0.0
        elif isinstance(value, (int, float)) and not isinstance(value, complex):
            if isinstance(value, int) and abs(value) > 10**15:
                raise ExpressionError("that number is too big")
            number = float(value)
        else:
            raise ExpressionError("only numbers and #RRGGBB colours can be written directly (no text)")
        return lambda env: number

    def _Name(self, node: ast.Name):  # noqa: N802
        name = node.id
        if name == "audio":
            raise ExpressionError("use audio.level, audio.bass, audio.mid, audio.treble, audio.beat or audio.pitch")
        if name in FUNCTIONS:
            raise ExpressionError(f"{name} is a function: write {name}(…)")
        if name in ("True", "true"):
            return lambda env: 1.0
        if name in ("False", "false"):
            return lambda env: 0.0
        if name not in CONSTANTS:
            self.names.add(name)
        return lambda env: env.name(name)

    def _Attribute(self, node: ast.Attribute):  # noqa: N802
        if isinstance(node.value, ast.Name) and node.value.id == "audio":
            band = node.attr
            if band not in AUDIO_NAMES:
                raise ExpressionError(f"audio.{band} doesn't exist: use one of " + ", ".join(f"audio.{n}" for n in AUDIO_NAMES))
            return lambda env: float(env.audio.get(band, 0.0))
        raise ExpressionError("dots are only for audio.level, audio.bass and so on")

    def _BinOp(self, node: ast.BinOp):  # noqa: N802
        op = BINARY.get(type(node.op))
        if op is None:
            raise ExpressionError(_not_allowed(node.op))
        left, right = self.build(node.left), self.build(node.right)
        return lambda env: op(left(env), right(env))

    def _UnaryOp(self, node: ast.UnaryOp):  # noqa: N802
        operand = self.build(node.operand)
        if isinstance(node.op, ast.USub):
            return lambda env: _each1(operand(env), lambda x: -x)
        if isinstance(node.op, ast.UAdd):
            return operand
        if isinstance(node.op, ast.Not):
            return lambda env: 0.0 if _truthy(operand(env)) else 1.0
        raise ExpressionError(_not_allowed(node.op))

    def _BoolOp(self, node: ast.BoolOp):  # noqa: N802
        values = [self.build(v) for v in node.values]
        if isinstance(node.op, ast.And):
            def run_and(env: Env) -> Value:
                result: Value = 1.0
                for value in values:
                    result = value(env)
                    if not _truthy(result):
                        return result
                return result
            return run_and

        def run_or(env: Env) -> Value:
            result: Value = 0.0
            for value in values:
                result = value(env)
                if _truthy(result):
                    return result
            return result
        return run_or

    def _Compare(self, node: ast.Compare):  # noqa: N802
        ops = []
        for op in node.ops:
            compare = COMPARE.get(type(op))
            if compare is None:
                raise ExpressionError(_not_allowed(op))
            ops.append((compare, type(op) in (ast.Eq, ast.NotEq)))
        first = self.build(node.left)
        rest = [self.build(c) for c in node.comparators]

        def run_compare(env: Env) -> Value:
            left = first(env)
            for (compare, equality), right_fn in zip(ops, rest):
                right = right_fn(env)
                if not equality:
                    _number(left, "a comparison")
                    _number(right, "a comparison")
                if not compare(left, right):
                    return 0.0
                left = right
            return 1.0
        return run_compare

    def _IfExp(self, node: ast.IfExp):  # noqa: N802
        test, body, other = self.build(node.test), self.build(node.body), self.build(node.orelse)
        return lambda env: body(env) if _truthy(test(env)) else other(env)

    def _Call(self, node: ast.Call):  # noqa: N802
        if not isinstance(node.func, ast.Name):
            raise ExpressionError("only the listed functions can be called, like sin(time)")
        name = node.func.id
        function = FUNCTIONS.get(name)
        if function is None:
            raise ExpressionError(f'there\'s no function called "{name}"')
        if node.keywords:
            raise ExpressionError(f"{name}() takes its values in order, without names (no =)")
        if any(isinstance(a, ast.Starred) for a in node.args):
            raise ExpressionError(_not_allowed(node.args[0]))
        count = len(node.args)
        if not function.least <= count <= function.most:
            wanted = str(function.least) if function.least == function.most else f"{function.least} to {function.most}"
            raise ExpressionError(f"{name}() takes {wanted} values, not {count}. {function.help}")
        if name == "hex":
            arg = node.args[0]
            if not isinstance(arg, ast.Constant) or not isinstance(arg.value, str):
                raise ExpressionError('hex() needs a colour code in quotes, like hex("FF8800")')
            colour = _hex_colour(arg.value)
            return lambda env: colour
        args = [self.build(a) for a in node.args]
        if name == "random":
            seed = args[0] if args else (lambda env: 0.0)
            return lambda env: _hash01("random", env.element, _number(seed(env), "random's seed"))
        if name in SHAPES and count >= 2 and not isinstance(node.args[1], ast.Constant):
            return self._cycle(SHAPES[name], args)
        run = function.run
        if count == 1:
            a = args[0]
            return lambda env: run(a(env))
        if count == 2:
            a, b = args
            return lambda env: run(a(env), b(env))
        if count == 3:
            a, b, c = args
            return lambda env: run(a(env), b(env), c(env))
        return lambda env: run(*[f(env) for f in args])


    @staticmethod
    def _cycle(shape: Callable[..., float], args: list):
        """A cycle function whose rate can change while it runs: it keeps its place in the cycle."""
        slot = object()  # this call in this formula (with the element, the key to its remembered position)
        time_of, rate_of, extra = args[0], args[1], args[2:]

        def run_cycle(env: Env) -> float:
            t = _number(time_of(env), "the time")
            rate = _number(rate_of(env), "the rate")
            phase = continuous_phase(env.memory, (env.element, slot), t, rate)
            return shape(phase, *[f(env) for f in extra])

        return run_cycle


def _not_allowed(node: Any) -> str:
    names = {
        "Attribute": "dots (apart from audio.level and friends)",
        "Subscript": "square brackets [ ]",
        "List": "lists [ ]", "Tuple": "lists of values ( , )", "Dict": "{ }", "Set": "{ }",
        "Lambda": "lambda", "NamedExpr": ":=", "FloorDiv": "//", "MatMult": "@",
        "BitAnd": "&", "BitOr": "|", "BitXor": "^", "LShift": "<<", "RShift": ">>", "Invert": "~",
        "Is": "is", "IsNot": "is not", "In": "in", "NotIn": "not in",
        "JoinedStr": "text", "ListComp": "for", "GeneratorExp": "for", "Await": "await", "Starred": "*",
    }
    kind = type(node).__name__
    return f"{names.get(kind, kind)} isn't allowed in an expression"


@lru_cache(maxsize=4096)
def compile_expression(text: str) -> Compiled:
    """Check and compile an expression (cached by its text). Raises ExpressionError."""
    if not isinstance(text, str) or not text.strip():
        raise ExpressionError("the expression is empty")
    if len(text) > MAX_LENGTH:
        raise ExpressionError(f"the expression is too long (over {MAX_LENGTH} characters)")
    source = _prepare(text.strip())
    if "#" in QUOTED.sub("", source):  # anything left would be read as a comment and quietly ignored
        raise ExpressionError("colours need # and six hex digits, like #FF8800")
    try:
        tree = ast.parse(source, mode="eval")
    except SyntaxError as exc:
        where = f" near character {exc.offset}" if exc.offset else ""
        raise ExpressionError(f"it can't be read{where}: check the brackets and the maths signs") from None
    depth = _depth(tree)
    if depth > 60:
        raise ExpressionError("the expression is nested too deeply")
    builder = _Builder()
    run = builder.build(tree)
    return Compiled(text, run, frozenset(builder.names))


def _depth(node: ast.AST, level: int = 0) -> int:
    children = list(ast.iter_child_nodes(node))
    return level if not children else max(_depth(child, level + 1) for child in children)


def check(text: str) -> str:
    """'' if the expression is fine to compile, otherwise what's wrong."""
    try:
        compile_expression(text)
    except ExpressionError as exc:
        return str(exc)
    return ""


def evaluate_text(text: str, env: Env) -> Value:
    """Compile (cached) and run. Raises ExpressionError."""
    return compile_expression(text).run(env)


def describe_value(value: Value) -> str:
    """A result as a person reads it: '0.42' or '#FF8800'."""
    if type(value) is tuple:
        return "#" + "".join(f"{min(255, max(0, round(v * 255))):02X}" for v in value)
    return f"{round(float(value), 3):g}"

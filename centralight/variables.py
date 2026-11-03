"""Variables that expressions can use.

- Globals: show-wide (show.json has each one's name, type and default). Their current values
  live in the engine, are set from the Dashboard, the Stream Deck or the Editor, and are kept
  in live.json so they survive a restart.
- Locals: one input's own variables ({"name", "type", "value": a parameter}). They're worked out
  afresh at every moment of the input, in an order that lets one use another; two that use each
  other in a circle get their plain value and an error.
- Specials: t, time, value and audio.* (see expressions.py).

While an input is drawn, its Scope is "current" on that thread; the expression hook in
params.evaluate() reads it from there, so the drawing code doesn't have to pass it around.
"""

from __future__ import annotations

import re
import threading
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

from .colours import is_colour_text, parse_colour
from .expressions import AUDIO_NAMES, Env, ExpressionError, Value, compile_expression, describe_value, reserved_names
from .log import log_info
from .model import GlobalVar, Input, Show
from .params import evaluate, set_expression_hook

LOCAL_TYPES = ("number", "colour")
AUDIO_SILENT: Mapping[str, float] = MappingProxyType({name: 0.0 for name in AUDIO_NAMES})
NO_VALUES: Mapping[str, Value] = MappingProxyType({})


# ---------------------------------------------------------------- globals


def global_default(var: GlobalVar) -> Value:
    value = coerce_global(var, var.default)
    return value if value is not None else _zero(var)


def _zero(var: GlobalVar) -> Value:
    return (0.0, 0.0, 0.0) if var.type == "colour" else 0.0


def coerce_global(var: GlobalVar, raw: Any, current: Value | None = None) -> Value | None:
    """A value for this global from a number, colour text, "on"/"off"/"toggle"... or None if it
    doesn't fit. Numbers are kept within the global's range."""
    if var.type == "number":
        if isinstance(raw, str):
            try:
                raw = float(raw.strip())
            except ValueError:
                return None
        if isinstance(raw, bool) or not isinstance(raw, (int, float)) or raw != raw:
            return None
        return float(min(var.max, max(var.min, raw)))
    if var.type == "colour":
        if isinstance(raw, (tuple, list)) and len(raw) == 3:
            try:
                return tuple(min(1.0, max(0.0, float(v))) for v in raw)  # type: ignore[return-value]
            except (TypeError, ValueError):
                return None
        if isinstance(raw, str):
            text = raw.strip()
            text = text if text.startswith("#") else "#" + text
            return parse_colour(text) if is_colour_text(text) else None
        return None
    # a switch: 1.0 on, 0.0 off
    if isinstance(raw, str):
        word = raw.strip().lower()
        if word == "toggle":
            return 0.0 if (current or 0.0) else 1.0
        if word in ("on", "true", "1", "yes"):
            return 1.0
        if word in ("off", "false", "0", "no"):
            return 0.0
        return None
    if isinstance(raw, (bool, int, float)):
        return 1.0 if raw else 0.0
    return None


def initial_globals(show: Show, saved: Mapping[str, Any] | None = None) -> dict[str, Value]:
    """Each global's value: the saved one (from live.json) if it still fits, else its default."""
    values: dict[str, Value] = {}
    for var in show.globals:
        value = coerce_global(var, (saved or {}).get(var.name)) if saved and var.name in saved else None
        values[var.name] = value if value is not None else global_default(var)
    return values


def reconcile_globals(show: Show, old: Show, values: Mapping[str, Value]) -> dict[str, Value]:
    """After the show changes: keep the values of globals that are still there with the same type
    (clamped to a new range), defaults for new ones; removed ones go."""
    before = {var.name: var for var in old.globals}
    result: dict[str, Value] = {}
    for var in show.globals:
        was = before.get(var.name)
        value = values.get(var.name)
        if was is not None and was.type == var.type and value is not None:
            kept = coerce_global(var, value)
            result[var.name] = kept if kept is not None else global_default(var)
        else:
            result[var.name] = global_default(var)
    return result


def saveable(values: Mapping[str, Value]) -> dict[str, Any]:
    """Values for live.json (colours as #RRGGBB)."""
    return {name: describe_value(v) if type(v) is tuple else v for name, v in values.items()}


def check_variable_name(name: str, taken: set[str] = frozenset()) -> str:
    """'' if name can be a variable's name, otherwise why not."""
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,31}", name or ""):
        return "Use letters, digits and _ (starting with a letter), up to 32 characters"
    if name in reserved_names():
        return f'"{name}" is already used by CentraLight (a function or a special value)'
    if name in taken:
        return f'There\'s already a variable called "{name}"'
    return ""


# ---------------------------------------------------------------- what's being drawn


@dataclass
class Scope:
    """What expressions can see while one input is drawn."""

    locals: dict[str, Value] = field(default_factory=dict)
    globals: Mapping[str, Value] = NO_VALUES
    time: float = 0.0  # input time
    audio: Mapping[str, float] = AUDIO_SILENT
    element: str = ""  # "input/element": random() is stable per element

    def lookup(self, name: str) -> Value | None:
        found = self.locals.get(name)
        if found is None:
            found = self.globals.get(name)
        return found


_state = threading.local()


@contextmanager
def use_scope(scope: Scope) -> Iterator[Scope]:
    previous = getattr(_state, "scope", None)
    _state.scope = scope
    try:
        yield scope
    finally:
        _state.scope = previous


def current_scope() -> Scope | None:
    return getattr(_state, "scope", None)


def _hook(text: str, value: Any, kind: str, t: float) -> Any:
    """params.evaluate() calls this for {"expr": ...} parameters."""
    scope = current_scope()
    try:
        compiled = compile_expression(text)
        if scope is None:
            env = Env(None, value, t)
        else:
            env = Env(scope.lookup, value, t, scope.time, scope.audio, scope.element)
        result = compiled.run(env)
        if kind == "number" and type(result) is tuple:
            raise ExpressionError("this setting needs a number, but the expression gives a colour")
        return result
    except ExpressionError as exc:
        log_info(f'Expression "{text}": {exc}', key=f"expression {text}")
        raise


set_expression_hook(_hook)


# ---------------------------------------------------------------- locals


def local_defaults(kind: str) -> Value:
    return (1.0, 1.0, 1.0) if kind == "colour" else 0.0


def local_order(definitions) -> tuple[list[Mapping[str, Any]], dict[str, str]]:
    """Locals in an order where each comes after the locals its expression uses, plus errors for
    any that use each other in a circle (by name)."""
    by_name: dict[str, Mapping[str, Any]] = {}
    for item in definitions:
        name = item.get("name") if hasattr(item, "get") else None
        if isinstance(name, str) and name and name not in by_name:
            by_name[name] = item
    uses: dict[str, set[str]] = {}
    errors: dict[str, str] = {}
    for name, item in by_name.items():
        raw = item.get("value")
        text = raw.get("expr") if hasattr(raw, "get") else None
        needs: set[str] = set()
        if isinstance(text, str):
            try:
                needs = set(compile_expression(text).names) & set(by_name)
            except ExpressionError:
                needs = set()
        uses[name] = needs  # its own name in there is a circle too
    order: list[str] = []
    state: dict[str, int] = {}  # 1 = being visited, 2 = done

    def visit(name: str, path: list[str]) -> None:
        if state.get(name) == 2:
            return
        if state.get(name) == 1:
            circle = path[path.index(name):] + [name]
            for member in circle[:-1]:
                errors[member] = "uses itself in a circle: " + " → ".join(circle)
            return
        state[name] = 1
        for needed in sorted(uses[name]):
            visit(needed, path + [name])
        state[name] = 2
        order.append(name)

    for name in by_name:
        visit(name, [])
    return [by_name[name] for name in order], errors


def evaluate_locals(inp: Input, time: float, globals_: Mapping[str, Value] = NO_VALUES,
                    audio: Mapping[str, float] = AUDIO_SILENT) -> tuple[dict[str, Value], dict[str, str]]:
    """An input's local variables at input time `time`: (values by name, errors by name)."""
    if not inp.locals:
        return {}, {}
    ordered, errors = local_order(inp.locals)
    scope = Scope({}, globals_, time, audio, f"{inp.id}/locals")
    with use_scope(scope):
        for item in ordered:
            name = item["name"]
            kind = item.get("type", "number")
            kind = kind if kind in LOCAL_TYPES else "number"
            raw = item.get("value", local_defaults(kind))
            if name in errors and hasattr(raw, "get"):  # in a circle: its plain value only
                raw = {k: v for k, v in raw.items() if k != "expr"}
            scope.locals[name] = evaluate(raw, time, local_defaults(kind), kind)
    return scope.locals, errors


def scope_for(inp: Input, time: float, globals_: Mapping[str, Value] = NO_VALUES,
              audio: Mapping[str, float] = AUDIO_SILENT) -> Scope:
    """The scope for drawing an input at input time `time` (its locals worked out)."""
    values, _errors = evaluate_locals(inp, time, globals_, audio)
    return Scope(values, globals_, time, audio)

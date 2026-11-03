"""Parameters: a setting of an element that can be fixed, keyframed or (from M3) an expression.

In an input's JSON a parameter is one of:

    0.5                                   a fixed number
    "#FF6F59"                             a fixed colour
    {"keys": [[0, 0.1], [5.5, 0.9, "smooth"]]}
                                          keyframes: [seconds, value, how to get to the next one]
    {"expr": "sin(time)"}                 an expression (M3)
    {"value": 0.5, "expr": "value * 2"}   a base value plus an expression that uses it

Keyframe interpolation: "smooth" (eases in and out, the default), "linear" or "hold"
(jumps at the next key). Before the first key and after the last one the value holds.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from functools import lru_cache
from types import MappingProxyType
from typing import Any

from .colours import parse_colour

INTERPOLATIONS = ("smooth", "linear", "hold")
KEY_TOLERANCE = 0.002  # seconds: keys this close to a time count as being at it
_MAP_TYPES = (dict, MappingProxyType)


def _is_map(value: Any) -> bool:
    # much quicker than isinstance(value, Mapping) for the two types inputs actually use
    return type(value) in _MAP_TYPES or isinstance(value, Mapping)
Colour = tuple[float, float, float]

# Filled in by M3's expression engine: (expression text, base value, kind, t) -> value
ExpressionHook = Callable[[str, Any, str, float], Any]
_expression_hook: ExpressionHook | None = None


def set_expression_hook(hook: ExpressionHook | None) -> None:
    global _expression_hook
    _expression_hook = hook


def is_dynamic(param: Any) -> bool:
    """True if the value can change over time (keyframes or an expression)."""
    return _is_map(param) and ("keys" in param or "expr" in param)


def _ease(t: float) -> float:
    return t * t * (3.0 - 2.0 * t)


@lru_cache(maxsize=4096)
def _colour_text(text: str) -> tuple[float, float, float] | None:
    parsed = parse_colour(text, (-1.0, -1.0, -1.0))
    return None if parsed[0] < 0 else parsed


def _coerce(value: Any, kind: str, default: Any) -> Any:
    if kind == "colour":
        if type(value) is str:
            parsed = _colour_text(value)  # parsed once, then remembered
            return default if parsed is None else parsed
        if type(value) in (list, tuple) and len(value) == 3:
            try:
                return tuple(min(1.0, max(0.0, float(v))) for v in value)
            except (TypeError, ValueError):
                return default
        return parse_colour(value, default)
    if kind == "number":
        if type(value) in (int, float, bool):
            return float(value)
        return default
    return value if value is not None else default


def _mix(a: Any, b: Any, amount: float, kind: str) -> Any:
    if kind == "colour":
        return tuple(x + (y - x) * amount for x, y in zip(a, b))
    if kind == "number":
        return a + (b - a) * amount
    return a if amount < 1.0 else b


def keys_of(param: Any) -> list[tuple[float, Any, str]]:
    """The keyframes as (time, value, interpolation), sorted by time."""
    if not _is_map(param):
        return []
    raw = param.get("keys")
    if type(raw) not in (list, tuple):
        return []
    keys = []
    for key in raw:
        if type(key) in (list, tuple) and len(key) >= 2 and type(key[0]) in (int, float):
            interp = key[2] if len(key) >= 3 and key[2] in INTERPOLATIONS else "smooth"
            keys.append((float(key[0]), key[1], interp))
    keys.sort(key=lambda k: k[0])
    return keys


_prepared: dict[tuple[int, str], tuple[Any, list]] = {}


def _prepared_keys(param: Any, kind: str, default: Any) -> list[tuple[float, Any, str]]:
    """Keyframes with their values already converted, worked out once per parameter."""
    key = (id(param), kind)
    cached = _prepared.get(key)
    if cached is not None and cached[0] is param:
        return cached[1]
    keys = [(t, _coerce(v, kind, default), interp) for t, v, interp in keys_of(param)]
    if len(_prepared) > 8192:
        _prepared.clear()
    _prepared[key] = (param, keys)  # holding the parameter keeps its id from being reused
    return keys


def base_value(param: Any, t: float, default: Any, kind: str = "number") -> Any:
    """The value before any expression: fixed, or from keyframes at time t."""
    if _is_map(param):
        keys = _prepared_keys(param, kind, default) if "keys" in param else []
        if keys:
            if t <= keys[0][0]:
                return keys[0][1]
            for (t0, a, interp), (t1, b, _next) in zip(keys, keys[1:]):
                if t < t1:
                    if interp == "hold" or t1 <= t0:
                        return a
                    f = (t - t0) / (t1 - t0)
                    return _mix(a, b, _ease(f) if interp == "smooth" else f, kind)
            return keys[-1][1]
        if "value" in param:
            return _coerce(param["value"], kind, default)
        return default
    return _coerce(param, kind, default)


def evaluate(param: Any, t: float, default: Any, kind: str = "number") -> Any:
    """The value at time t: base value, then the expression if there is one."""
    value = base_value(param, t, default, kind)
    if _is_map(param) and isinstance(param.get("expr"), str) and _expression_hook is not None:
        try:
            return _coerce(_expression_hook(param["expr"], value, kind, t), kind, value)
        except Exception:
            return value  # a broken expression falls back to the base value (M3 shows the error)
    return value


def with_base_value(param: Any, value: Any) -> Any:
    """param with a new fixed value, keeping its expression (and keyframes) if it has one."""
    if _is_map(param) and ("expr" in param or "keys" in param):
        data = dict(param)
        if "keys" not in data:
            data["value"] = value
        return data
    return value


def with_expression(param: Any, text: str, default: Any = None) -> Any:
    """param with an expression added, changed or (text "") removed. The value it had stays as
    the base value the expression can use as `value`."""
    text = (text or "").strip()
    if _is_map(param):
        data = {k: v for k, v in param.items() if k != "expr"}
        if text:
            data["expr"] = text
            return data
        if "keys" in data:
            return data
        return data.get("value", default)
    if text:
        return {"value": default if param is None else param, "expr": text}
    return default if param is None else param


# ---------------------------------------------------------------- editing keyframes


def _encode_keys(keys: list[tuple[float, Any, str]]) -> list[list[Any]]:
    """Keys for the JSON: [seconds, value] (smooth, the default) or [seconds, value, "linear" / "hold"]."""
    return [[round(t, 4), value] if interp == "smooth" else [round(t, 4), value, interp] for t, value, interp in keys]


def key_at(param: Any, t: float) -> tuple[float, Any, str] | None:
    """The keyframe at time t (within KEY_TOLERANCE), if there is one."""
    return next((key for key in keys_of(param) if abs(key[0] - t) <= KEY_TOLERANCE), None)


def is_keyframed(param: Any) -> bool:
    return bool(keys_of(param))


def with_key(param: Any, t: float, value: Any, interp: str | None = None) -> Any:
    """param with a keyframe at t (added, or the one already there changed to this value). A fixed value
    becomes keyframes; an expression is kept (it can use the keyframed value as `value`)."""
    t = max(0.0, float(t))
    keys, found = [], False
    for kt, kv, ki in keys_of(param):
        if abs(kt - t) <= KEY_TOLERANCE and not found:
            keys.append((kt, value, interp or ki))
            found = True
        else:
            keys.append((kt, kv, ki))
    if not found:
        keys.append((t, value, interp if interp in INTERPOLATIONS else "smooth"))
    keys.sort(key=lambda k: k[0])
    data = {k: v for k, v in param.items() if k != "value"} if _is_map(param) else {}
    data["keys"] = _encode_keys(keys)
    return data


def without_key(param: Any, t: float) -> Any:
    """param without its keyframe at t. Without any keys left it goes back to a fixed value (the one that
    key had), keeping its expression."""
    keys = keys_of(param)
    removed = next((k for k in keys if abs(k[0] - t) <= KEY_TOLERANCE), None)
    if removed is None:
        return param
    left = [k for k in keys if k is not removed]
    data = {k: v for k, v in param.items() if k != "keys"}
    if left:
        data["keys"] = _encode_keys(left)
        return data
    if "expr" in data:
        data["value"] = removed[1]
        return data
    return removed[1]


def with_key_moved(param: Any, t_from: float, t_to: float) -> Any:
    """param with its keyframe at t_from moved to t_to (a key already at t_to is replaced)."""
    moving = key_at(param, t_from)
    if moving is None:
        return param
    t_to = max(0.0, float(t_to))
    keys = [k for k in keys_of(param) if abs(k[0] - moving[0]) > KEY_TOLERANCE and abs(k[0] - t_to) > KEY_TOLERANCE]
    keys.append((t_to, moving[1], moving[2]))
    keys.sort(key=lambda k: k[0])
    return {**dict(param), "keys": _encode_keys(keys)}


def with_key_interpolation(param: Any, t: float, interp: str) -> Any:
    """param with the keyframe at t getting to the next one in this way ("smooth", "linear" or "hold")."""
    if interp not in INTERPOLATIONS or key_at(param, t) is None:
        return param
    keys = [(kt, kv, interp if abs(kt - t) <= KEY_TOLERANCE else ki) for kt, kv, ki in keys_of(param)]
    return {**dict(param), "keys": _encode_keys(keys)}


def keys_shifted(param: Any, offset: float, keep: Callable[[float], bool] | None = None) -> Any:
    """param with every keyframe moved by offset seconds (and only the keys whose old time passes keep(), if
    given): used when an element is split or trimmed from the start, so its keys stay where they were."""
    if not is_keyframed(param):
        return param
    keys = [(max(0.0, kt + offset), kv, ki) for kt, kv, ki in keys_of(param) if keep is None or keep(kt)]
    if not keys:
        return param  # nothing would be left (callers add a key at the cut first, see input_edit.split_element)
    return {**dict(param), "keys": _encode_keys(keys)}


def neighbour_key(param: Any, t: float, direction: int) -> float | None:
    """The time of the next keyframe after t (direction 1) or the one before it (-1), or None."""
    times = [k[0] for k in keys_of(param)]
    if direction > 0:
        later = [kt for kt in times if kt > t + KEY_TOLERANCE]
        return min(later) if later else None
    earlier = [kt for kt in times if kt < t - KEY_TOLERANCE]
    return max(earlier) if earlier else None


def expression_of(param: Any) -> str:
    """The expression text of a parameter ('' if it hasn't one)."""
    if _is_map(param) and isinstance(param.get("expr"), str):
        return param["expr"]
    return ""

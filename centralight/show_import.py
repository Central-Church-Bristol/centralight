"""Import from another show… for inputs and show automations (M13, like the Layout tab's import of a layout).

Pick a show, tick some of its inputs (Dashboard) or show automations (Automation tab); a summary comes first:
- Ids are kept, so a Stream Deck key fires the input in whichever show is loaded. If this show already has one with
  that id, the choice is Replace or Keep both (the import then gets a new id; automations brought along with it are
  pointed at the new id).
- Lights the inputs use that this show hasn't got are listed (an input names lights by id: its excluded lights and
  house-light levels); they're simply ignored here.
- Show variables the inputs (or automations) use that this show hasn't got are added, with their defaults.
- An automation that sends inputs this show hasn't got lists them, and they come too unless that's unticked.
- Pictures and videos come along: their copies (and painted strokes and masks) are copied into this show's folder, and
  their links to originals are kept (so are the originals' records in media\\links.json).
The other show isn't loaded and nothing is sent to the lights. It's one undo step here.

This module is Qt-free: plan_import() works out what would happen, summary() says it in words, and apply_import()
makes the new Show once copy_files() has put the files in place.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Iterable, Mapping

from .automations import _unique_automation_name
from .expressions import ExpressionError, compile_expression
from .input_edit import unique_name
from .links import read_stamps, safe_media_file, update_stamps
from .log import log_info
from .model import Automation, GlobalVar, Input, Show, slugify
from .show_io import freeze, safe_relative_file, thaw

INPUT_ACTIONS = ("base", "overlay", "overlay_clear")  # automation actions that name an input
COPIED_FOLDERS = ("media/", "paint/", "masks/")  # the files an input's elements point at


@dataclass
class ImportPlan:
    """What importing would do. inputs / automations: the other show's ids being brought."""

    inputs: list[str] = field(default_factory=list)
    automations: list[str] = field(default_factory=list)
    clashes: list[tuple[str, str]] = field(default_factory=list)  # (kind "input" / "automation", id) already here
    needed: dict[str, list[str]] = field(default_factory=dict)  # automation id -> inputs it sends that this show lacks
    brought_along: list[str] = field(default_factory=list)  # inputs added because automations send them
    missing_lights: list[str] = field(default_factory=list)  # names of lights the inputs use that this show lacks
    new_variables: list[GlobalVar] = field(default_factory=list)  # show variables added (with their defaults)
    missing_layouts: list[str] = field(default_factory=list)  # layouts automations switch to that this show lacks
    files: list[str] = field(default_factory=list)  # files to copy from the other show ("media/candles.mp4")
    live: list[str] = field(default_factory=list)  # inputs here on the lights now that would be replaced
    hotkeys: list[str] = field(default_factory=list)  # imported inputs whose hotkey another input here has: dropped


def _expressions(value: Any) -> Iterable[str]:
    if isinstance(value, Mapping):
        if isinstance(value.get("expr"), str):
            yield value["expr"]
        for key, inner in value.items():
            if key != "expr":
                yield from _expressions(inner)
    elif isinstance(value, (list, tuple)):
        for inner in value:
            yield from _expressions(inner)


def _names_used(inp: Input) -> set[str]:
    """The variable names an input's formulas read (its own locals left out)."""
    names: set[str] = set()
    for text in _expressions([inp.tracks, inp.house, inp.locals]):
        try:
            names |= set(compile_expression(text).names)
        except ExpressionError:
            continue
    own = {str(item.get("name")) for item in inp.locals if isinstance(item, Mapping)}
    return names - own


def _lights_used(inp: Input) -> set[str]:
    used = set(inp.house)
    for track in inp.tracks:
        for element in track:
            mask = element.get("mask")
            if isinstance(mask, Mapping):
                used.update(light for light in mask.get("exclude", ()) or () if isinstance(light, str))
    return used


def _files_of(inp: Input) -> set[str]:
    files: set[str] = set()
    for track in inp.tracks:
        for element in track:
            for value in (element.get("file"), element.get("strokes"),
                          element.get("mask", {}).get("paint") if isinstance(element.get("mask"), Mapping) else None):
                text = safe_relative_file(value)
                if text and text.startswith(COPIED_FOLDERS) and text.count("/") == 1:
                    files.add(text)
    return files


def _inputs_sent(automation: Automation) -> list[str]:
    sent = [a.get("input") for a in (*automation.actions, *automation.on_end) if a.get("do") in INPUT_ACTIONS]
    sent += [c.get("input") for c in automation.conditions if c.get("type") == "input_live"]
    return [i for i in dict.fromkeys(sent) if isinstance(i, str) and i]


def _variables_named(automation: Automation) -> set[str]:
    names = {a.get("name") for a in (*automation.actions, *automation.on_end) if a.get("do") == "variable"}
    names |= {c.get("name") for c in automation.conditions if c.get("type") == "variable"}
    return {n for n in names if isinstance(n, str) and n}


def plan_import(show: Show, other: Show, inputs: Iterable[str] = (), automations: Iterable[str] = (),
                bring_inputs: bool = True, live: Iterable[str] = ()) -> ImportPlan:
    """What importing these inputs and automations from `other` into `show` would do. bring_inputs: inputs the
    automations send that this show lacks come too. live: inputs on the lights here now."""
    plan = ImportPlan(inputs=[i for i in dict.fromkeys(inputs) if i in other.inputs],
                      automations=[a for a in dict.fromkeys(automations) if a in other.automations])
    for automation_id in plan.automations:
        lacking = [i for i in _inputs_sent(other.automations[automation_id]) if i not in show.inputs]
        if lacking:
            plan.needed[automation_id] = lacking
            if bring_inputs:
                for input_id in lacking:
                    if input_id in other.inputs and input_id not in plan.inputs:
                        plan.inputs.append(input_id)
                        plan.brought_along.append(input_id)
    plan.clashes = [("input", i) for i in plan.inputs if i in show.inputs or i in show.input_errors]
    plan.clashes += [("automation", a) for a in plan.automations if a in show.automations]
    used_keys = {inp.hotkey.casefold(): inp.id for inp in show.inputs.values() if inp.hotkey}
    plan.hotkeys = [i for i in plan.inputs if other.inputs[i].hotkey
                    and used_keys.get(other.inputs[i].hotkey.casefold(), i) != i]
    live = set(live)
    plan.live = [i for i in plan.inputs if i in show.inputs and i in live]
    here = {fx.id for fx in show.fixtures}
    names = {fx.id: fx.name for fx in other.fixtures}
    lights: list[str] = []
    variables: set[str] = set()
    for input_id in plan.inputs:
        inp = other.inputs[input_id]
        lights += [names.get(light, light) for light in sorted(_lights_used(inp)) if light not in here]
        variables |= _names_used(inp)
        plan.files += sorted(_files_of(inp) - set(plan.files))
    for automation_id in plan.automations:
        automation = other.automations[automation_id]
        variables |= _variables_named(automation)
        lights += [names.get(a.get("light"), a.get("light")) for a in (*automation.actions, *automation.on_end)
                   if a.get("do") == "house" and isinstance(a.get("light"), str) and a.get("light") not in here]
        for action in (*automation.actions, *automation.on_end):
            layout = action.get("layout")
            if action.get("do") == "layout" and isinstance(layout, str) and show.layout(layout) is None:
                found = other.layout(layout)
                plan.missing_layouts.append(found.name if found is not None else layout)
    plan.missing_lights = list(dict.fromkeys(lights))
    have = {g.name for g in show.globals}
    plan.new_variables = [g for g in other.globals if g.name in variables and g.name not in have]
    return plan


def summary(plan: ImportPlan, show: Show, other: Show, keep_both: bool) -> list[str]:
    """The summary shown before Import, one point per line."""
    lines: list[str] = []
    count = len(plan.inputs)
    if count:
        names = ", ".join(other.inputs[i].name for i in plan.inputs)
        lines.append(f"{count} input{'s' if count != 1 else ''} from {other.name}: {names}.")
    if plan.automations:
        names = ", ".join(other.automations[a].name for a in plan.automations)
        lines.append(f"{len(plan.automations)} automation{'s' if len(plan.automations) != 1 else ''}: {names}.")
    if plan.brought_along:
        names = ", ".join(other.inputs[i].name for i in plan.brought_along)
        lines.append(f"Inputs the automations send, which this show hasn't got, come too: {names}.")
    missing = sorted({i for lacking in plan.needed.values() for i in lacking if i not in plan.inputs})
    if missing:
        lines.append(f"The automations send inputs this show hasn't got ({', '.join(missing)}): those actions do "
                     "nothing here unless the inputs are imported too.")
    if plan.clashes:
        which = ", ".join(show.inputs[i].name if kind == "input" else show.automations[i].name for kind, i in plan.clashes)
        lines.append(f"Already in this show with the same id: {which}. "
                     + ("They're kept, and the imported ones get new ids (a Stream Deck key fires this show's own)."
                        if keep_both else "The imported ones replace them (their Stream Deck keys keep working)."))
    if plan.live and not keep_both:
        names = ", ".join(show.inputs[i].name for i in plan.live)
        lines.append(f"On the lights now: {names}. Replacing changes what the lights show.")
    for input_id in plan.hotkeys:
        inp = other.inputs[input_id]
        lines.append(f"{inp.name}'s hotkey {inp.hotkey} is another input's here, so it comes without one.")
    if plan.automations:
        lines.append("Imported automations start from how things are now: one whose conditions are true already "
                     "waits until they next come true.")
    for light in plan.missing_lights:
        lines.append(f"{light} isn't in this show, so it's ignored here.")
    if plan.new_variables:
        lines.append("Show variables added (with their starting values): "
                     + ", ".join(g.name for g in plan.new_variables) + ".")
    if plan.missing_layouts:
        lines.append(f"This show has no layout called {', '.join(plan.missing_layouts)}, so switching to it does nothing here.")
    if plan.files:
        lines.append(f"{len(plan.files)} picture{'s' if len(plan.files) != 1 else ''}, video{'s' if len(plan.files) != 1 else ''} "
                     "or painting file" + ("s are" if len(plan.files) != 1 else " is") + " copied into this show (links to "
                     "their originals come too).")
    lines.append("Nothing is sent to the lights, and Ctrl+Z undoes it.")
    return lines


def copy_files(plan: ImportPlan, show: Show, other: Show) -> dict[str, str]:
    """Copy the files the imported inputs use into this show's folder. Returns {their name there: their name here}
    (a picture with the same name but different contents here gets a new name). Their records of where they were
    copied from (media\\links.json) come too."""
    from .link_worker import copy_name, same_contents

    renamed: dict[str, str] = {}
    if not show.folder or not other.folder:
        return renamed
    source_root, target_root = Path(other.folder), Path(show.folder)
    stamps = read_stamps(other.folder)
    taken = set(read_stamps(show.folder))
    carried: dict[str, dict] = {}
    for file in plan.files:
        source = source_root / file
        if not source.is_file():
            continue
        target = target_root / file
        name = file
        if target.exists() and not same_contents(source, target):
            if not file.startswith("media/"):
                continue  # (painted strokes and masks are named from their contents: the same name is the same file)
            name = copy_name(target_root, file.split("/", 1)[1], {t.casefold() for t in taken})
            target = target_root / name
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        renamed[file] = name
        taken.add(name)
        if file in stamps and safe_media_file(name):
            carried[name] = stamps[file]
    if carried:
        update_stamps(show.folder, lambda s: s.update(carried))
    log_info(f"Import from {other.name}: {len(renamed)} file(s) copied into {target_root.name}")
    return renamed


def _renamed_files(inp: Input, renamed: Mapping[str, str]) -> Input:
    def walk(value: Any) -> Any:
        if isinstance(value, Mapping):
            return {k: (renamed.get(v, v) if k in ("file", "strokes", "paint") and isinstance(v, str) else walk(v))
                    for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [walk(v) for v in value]
        return value

    if not renamed:
        return inp
    return replace(inp, tracks=freeze([walk(thaw(track)) for track in inp.tracks]))


def apply_import(show: Show, other: Show, plan: ImportPlan, keep_both: bool, renamed: Mapping[str, str] | None = None) -> Show:
    """The show with the import done (one undo step for the caller to apply)."""
    renamed = renamed or {}
    inputs = dict(show.inputs)
    order = list(show.input_order)
    new_ids: dict[str, str] = {}
    reserved = set(show.inputs) | set(show.input_errors) | set(plan.inputs)  # (no new id takes one that's coming)
    for input_id in plan.inputs:
        inp = _renamed_files(other.inputs[input_id], renamed)
        if input_id in plan.hotkeys:
            inp = replace(inp, hotkey="")
        if (input_id in show.inputs or input_id in show.input_errors) and keep_both:
            new_id = _free_id(slugify(inp.name, "input"), reserved)
            reserved.add(new_id)
            inp = replace(inp, id=new_id, name=unique_name(show.with_inputs(inputs), inp.name), hotkey="")
            new_ids[input_id] = new_id
        inputs[inp.id] = inp
        if inp.id not in order:
            order.append(inp.id)
    automations = dict(show.automations)
    automation_order = [a.id for a in show.ordered_automations()]
    new_automation_ids: dict[str, str] = {}
    reserved_automations = set(show.automations) | set(plan.automations)
    for automation_id in plan.automations:
        if automation_id in show.automations and keep_both:
            new_id = _free_id(slugify(other.automations[automation_id].name, "automation"), reserved_automations)
            reserved_automations.add(new_id)
            new_automation_ids[automation_id] = new_id
    for automation_id in plan.automations:
        automation = other.automations[automation_id]
        automation = replace(automation, actions=_pointed(automation.actions, new_ids, new_automation_ids),
                             on_end=_pointed(automation.on_end, new_ids, new_automation_ids),
                             conditions=_pointed(automation.conditions, new_ids, new_automation_ids))
        if automation_id in new_automation_ids:
            automation = replace(automation, id=new_automation_ids[automation_id],
                                 name=_unique_automation_name(show.with_automations(automations), automation.name))
        automations[automation.id] = automation
        if automation.id not in automation_order:
            automation_order.append(automation.id)
    show = show.with_inputs(inputs, tuple(order))
    if plan.automations:
        show = show.with_automations(automations, tuple(automation_order))
    if plan.new_variables:
        show = replace(show, globals=show.globals + tuple(plan.new_variables))
    return show


def _pointed(items: tuple, new_ids: Mapping[str, str], new_automation_ids: Mapping[str, str] | None = None) -> tuple:
    """Actions (or conditions) pointed at the imported inputs' (and automations') new ids."""
    new_automation_ids = new_automation_ids or {}
    pointed = []
    for item in items:
        if item.get("input") in new_ids:
            item = freeze({**thaw(item), "input": new_ids[item["input"]]})
        if item.get("do") == "automation" and item.get("automation") in new_automation_ids:
            item = freeze({**thaw(item), "automation": new_automation_ids[item["automation"]]})
        pointed.append(item)
    return tuple(pointed)


def _free_id(base: str, taken: set[str]) -> str:
    candidate, n = base, 2
    while candidate in taken:
        candidate = f"{base[:60]}-{n}"
        n += 1
    return candidate


def imported_automation_ids(show_before: Show, show_after: Show) -> tuple[str, ...]:
    """The automations an import added or replaced (for the engine: they start from how things are now)."""
    return tuple(i for i, a in show_after.automations.items() if show_before.automations.get(i) != a)

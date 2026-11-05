"""The Editor page (Fusion-style): build and change an input.

    ┌ Editing: <name>      [Show on lights] [Send to Base] [Done] ┐
    │ LIVE: changes go to the lights            (when it's on air) │
    ├──────────┬──────────────────────────────────┬────────────────┤
    │ Elements │  Selected        │  Result        │ Details        │
    │ Paint    │  (the element)   │  (the input)   │ (the selected  │
    │ Image    │                  │  light strip   │  element, or   │
    │ NDI      ├──────────────────────────────────┤  the input's   │
    │ Trans-   │  Timeline: Track 4 … Track 1      │  own settings) │
    │ itions   │                                   │                │
    └──────────┴──────────────────────────────────┴────────────────┘

Editing an input that's on Base or the Overlay changes the lights straight away (the red
LIVE banner says so); otherwise editing is "blind" until you send it or use Show on lights.

The viewports show the input at the playhead. It plays when an input is opened (so expressions
move); pause it to put keyframes at a moment (the ◆ next to each setting), drag in the ruler to
scrub. A timed input stops at its end, unless it loops.
"""

from __future__ import annotations

import time
from typing import Any

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QStackedWidget, QVBoxLayout, QWidget

from ..commands import SendToBase, SetSolo
from ..editor_render import EditorRenderer
from ..engine import EngineFrame
from ..assets import ASSET_H, ASSET_W, MASKS_DIR, PAINT_DIR, load_mask, load_strokes, save_asset
from ..elements import mask_shapes, media_path
from ..input_edit import (
    add_shape,
    find_element,
    invert_mask,
    set_element_value,
    set_element_values_at,
    set_excluded,
    set_shape_values_at,
    update_input,
)
from ..log import log_exception
from ..model import END_LOOP, Input, Show, element_span
from ..settings import Settings
from ..step_names import element_name, lights_change_name
from .details_panel import MIN_WIDTH as DETAILS_MIN_WIDTH
from .details_panel import DetailsPanel
from .document import SCOPE_SHOW, ShowDocument, input_scope
from .element_palette import ElementPalette
from .timeline_view import TimelineView
from .undo_bar import UndoBar
from .viewports import ResultPanel, SelectedPanel, SelectedViewport, ViewportCanvas, mask_image, rgb_image, rgba_image
from .widgets import NameTitle, ResettableSplitter

POLL_MS = 66  # pick up new pictures from the editor renderer ~15 times a second


class EditorPage(QWidget):
    done = Signal()
    new_input_requested = Signal()
    ai_requested = Signal(str)  # edit this input with AI
    console_requested = Signal(str)  # Load into Console (a Lights element, M15): the input id

    def __init__(self, document: ShowDocument, engine: Any, settings: Settings, sources: Any | None = None,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("EditorPage")
        self.document = document
        self.engine = engine
        self.settings = settings
        self.input_id = ""
        self.element_id = ""
        self.shape_id = ""
        self.mask_shape_id = ""  # the selected shape of the element's mask (M14: edited in the Selected view's Mask mode)
        self.playhead = 0.0  # the input's time shown in the viewports (seconds)
        self.playing = False  # (an input opens paused, M14: press play to see it move)
        self._play_origin = time.monotonic()  # when time 0 would have been, while playing
        self._renderer = EditorRenderer(sources, late=getattr(engine, "is_late", None))  # (fewer pictures while the engine runs late)
        self._renderer_running = False
        self._picture_version = -1
        self._live = False
        self._solo_live = False  # the engine is showing this input alone (from the last frame)
        self._drag_t: float | None = None  # the element time a drag in the Selected view began at
        self._entered = False  # the Editor is the page on screen
        self._variables = None  # global variables' values, from the engine
        show = document.show

        # --- the strip along the top
        self.editing = NameTitle("Editing: ")  # (click the name to rename the input there)
        self.editing.label.setObjectName("EditorTitle")
        self.editing.edit.setObjectName("EditorTitleEdit")
        self.editing.renamed.connect(self._rename_input)
        self.ai_button = QPushButton("Edit with AI")
        self.ai_button.setToolTip("Describe a change, and let any AI chat write it (copy, paste)")
        self.ai_button.clicked.connect(lambda: self.input_id and self.ai_requested.emit(self.input_id))
        self.solo_button = QPushButton("Show on lights")
        self.solo_button.setCheckable(True)
        self.solo_button.setToolTip("Show only this input on the lights while you edit (switches off when you leave the Editor)")
        self.solo_button.toggled.connect(self._solo_toggled)
        self.send_button = QPushButton("Send to Base")
        self.send_button.setProperty("accent", True)
        self.send_button.setToolTip("Put this input on Base, with its transition")
        self.send_button.clicked.connect(self._send)
        self.done_button = QPushButton("Done")
        self.done_button.setToolTip("Back to the Dashboard")
        self.done_button.clicked.connect(self.done.emit)
        self.undo_bar = UndoBar(document.undo_group)
        strip = QHBoxLayout()
        strip.setSpacing(8)
        strip.addWidget(self.editing, 1)
        strip.addWidget(self.undo_bar)
        strip.addSpacing(6)
        strip.addWidget(self.ai_button)
        strip.addWidget(self.solo_button)
        strip.addWidget(self.send_button)
        strip.addWidget(self.done_button)

        self.live_banner = QFrame()
        self.live_banner.setObjectName("LiveBanner")
        live_text = QLabel("LIVE: this input is on the lights, so changes show straight away")
        live_text.setObjectName("LiveBannerText")
        banner_layout = QHBoxLayout(self.live_banner)
        banner_layout.setContentsMargins(12, 4, 12, 4)
        banner_layout.addWidget(live_text)
        self.live_banner.setVisible(False)

        # --- the body
        self.palette = ElementPalette()
        self.selected_canvas = SelectedViewport(show)
        self.selected_panel = SelectedPanel(self.selected_canvas)
        self.result_canvas = ViewportCanvas(show)
        self.result_panel = ResultPanel(self.result_canvas, show)
        for panel, key in ((self.selected_panel, "editor_selected_lights"), (self.result_panel, "editor_result_lights")):
            panel.lights.setChecked(bool(settings.ui.get(key, True)))
            panel.lights_toggled.connect(lambda on, k=key: self.settings.ui.__setitem__(k, on))
        self.viewports = ResettableSplitter(Qt.Orientation.Horizontal, self._default_viewport_split)
        self.viewports.addWidget(self.selected_panel)
        self.viewports.addWidget(self.result_panel)
        self.timeline = TimelineView(document, settings.ndi_default)
        self.centre = ResettableSplitter(Qt.Orientation.Vertical, self._default_centre_split)
        self.centre.addWidget(self.viewports)
        self.centre.addWidget(self.timeline)
        self.centre.setStretchFactor(0, 3)
        self.centre.setStretchFactor(1, 2)
        provider = (lambda: sources.available()) if sources is not None and hasattr(sources, "available") else (lambda: [])
        self.details = DetailsPanel(
            document, provider,
            context=lambda: (self.variables(), self.playhead),
            url_base=lambda: f"http://127.0.0.1:{settings.remote_port}",
        )
        self.details.tab_changed.connect(lambda _index: self._sync_scope())
        self.body = ResettableSplitter(Qt.Orientation.Horizontal, self._default_body_split)
        self.body.addWidget(self.palette)
        self.body.addWidget(self.centre)
        self.body.addWidget(self.details)
        self.body.setStretchFactor(0, 0)
        self.body.setStretchFactor(1, 1)
        self.body.setStretchFactor(2, 0)

        empty = QWidget()
        empty_layout = QVBoxLayout(empty)
        empty_layout.addStretch(1)
        heading = QLabel("Nothing to edit yet")
        heading.setObjectName("BigTitle")
        heading.setAlignment(Qt.AlignmentFlag.AlignCenter)
        text = QLabel("Click the pencil on an input's tile on the Dashboard, or start a new input.")
        text.setObjectName("Hint")
        text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        text.setWordWrap(True)
        new = QPushButton("New input")
        new.setProperty("accent", True)
        new.clicked.connect(self.new_input_requested.emit)
        empty_layout.addWidget(heading)
        empty_layout.addWidget(text)
        empty_layout.addWidget(new, 0, Qt.AlignmentFlag.AlignHCenter)
        empty_layout.addStretch(2)
        self.stack = QStackedWidget()
        self.stack.addWidget(empty)
        self.stack.addWidget(self.body)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 14)
        layout.setSpacing(8)
        layout.addLayout(strip)
        layout.addWidget(self.live_banner)
        layout.addWidget(self.stack, 1)

        # --- wiring
        self.timeline.element_selected.connect(self.select_element)
        self.details.shape_selected.connect(self._shape_from_details)
        self.details.mask_shape_selected.connect(self._mask_shape_from_details)
        self.details.element_removed.connect(lambda: self.select_element(""))
        self.selected_canvas.shape_selected.connect(self._shape_from_viewport)
        self.selected_canvas.shape_remove_requested.connect(self._remove_shape_by_key)
        self.selected_canvas.drag_started.connect(self._drag_started)
        self.selected_canvas.shapes_changing.connect(self._shape_dragged)
        self.selected_canvas.shapes_changed.connect(self._drag_ended)
        self.selected_canvas.shape_drawn.connect(self._shape_drawn)
        self.selected_canvas.light_clicked.connect(self._toggle_light)
        self.selected_canvas.load_picture = self._picture_now
        self.selected_canvas.painted.connect(self._painted)
        self.selected_canvas.transform_changing.connect(self._picture_dragged)
        self.selected_panel.mode_changed.connect(self._mask_mode_changed)
        self.selected_panel.invert_requested.connect(self.invert_mask)
        self.palette.element_clicked.connect(self.timeline.add_to_first_empty)
        self.palette.transition_clicked.connect(self.timeline.set_transition_kind)
        document.changed.connect(self._show_changed)
        self.timeline.seek_requested.connect(self.seek)
        self.timeline.play_toggled.connect(self.set_playing)
        self.details.seek_requested.connect(self.seek)
        self.details.timed_requested.connect(lambda timed: self.timeline.set_timed(timed))
        self.details.console_requested.connect(self.console_requested.emit)

        self._poll = QTimer(self)
        self._poll.setInterval(POLL_MS)
        self._poll.timeout.connect(self._tick)
        self._sync_buttons()

    # ------------------------------------------------------------ splits

    def _default_body_split(self) -> list[int]:
        total = max(1, self.body.width())
        left, right = 180, min(400, max(DETAILS_MIN_WIDTH, total // 4))
        return [left, total - left - right, right]

    def _default_viewport_split(self) -> list[int]:
        half = max(1, self.viewports.width()) // 2
        return [half, half]

    def _default_centre_split(self) -> list[int]:
        total = max(1, self.centre.height())
        timeline = min(max(self.timeline.minimumSizeHint().height(), round(total * 0.38)), total - 200)
        return [total - timeline, timeline]

    def apply_split(self, sizes: dict | None) -> None:
        sizes = sizes if isinstance(sizes, dict) else {}
        for name, splitter, default in (
            ("body", self.body, self._default_body_split),
            ("viewports", self.viewports, self._default_viewport_split),
            ("centre", self.centre, self._default_centre_split),
        ):
            splitter.apply_sizes(sizes.get(name))

    def splits(self) -> dict:
        return {"body": self.body.sizes(), "viewports": self.viewports.sizes(), "centre": self.centre.sizes()}

    # ------------------------------------------------------------ what's being edited

    @property
    def show_data(self) -> Show:
        return self.document.show

    def current_input(self) -> Input | None:
        return self.show_data.inputs.get(self.input_id) if self.input_id else None

    def open_input(self, input_id: str) -> None:
        if input_id not in self.show_data.inputs:
            return
        if self.solo_button.isChecked() and input_id != self.input_id:
            self.solo_button.setChecked(False)
        self.input_id = input_id
        self.element_id = ""
        self.shape_id = ""
        self.mask_shape_id = ""
        self.playhead, self.playing, self._play_origin = 0.0, False, time.monotonic()  # (opens paused at the start)
        if self._entered:
            self.document.set_scope(self.undo_scope())
        self.timeline.set_input(input_id)
        self._push_time()
        self.details.set_target(input_id, None, None)
        self._sync_scope()
        self.selected_canvas.set_element(None, None)
        self.selected_panel.set_paint_tools(False)
        self._sync_buttons()
        self._request()

    def select_element(self, element_id: str) -> None:
        self.element_id = element_id or ""
        self.shape_id = ""
        self.mask_shape_id = ""
        self.timeline.set_selected(self.element_id)
        self.details.set_target(self.input_id, self.element_id or None, None)
        self._sync_scope()
        self._sync_selected_view()
        self._request()

    def _shape_from_details(self, shape_id: str) -> None:
        self.shape_id = shape_id or ""
        if shape_id and self.selected_canvas.mask_mode:
            self.selected_panel.layer.setChecked(True)  # (a Paint shape's handles are in Layer mode)
        self._sync_selected_view()

    def _mask_shape_from_details(self, shape_id: str) -> None:
        """A mask shape picked in the details panel's Mask shapes list: the Selected view goes to Mask mode, with Select,
        so its handles show."""
        self.mask_shape_id = shape_id or ""
        if shape_id and not self.selected_canvas.mask_mode:
            self.selected_panel.mask.setChecked(True)
            self.selected_panel.select_tool.setChecked(True)
        self._sync_selected_view()

    def _shape_from_viewport(self, shape_id: str) -> None:
        if self.selected_canvas.mask_mode:  # (a shape of the mask)
            self.mask_shape_id = shape_id or ""
            self.details.select_mask_shape(self.mask_shape_id or None)
        else:
            self.shape_id = shape_id or ""
            self.details.select_shape(self.shape_id or None)
        self._sync_selected_view()

    def _mask_mode_changed(self, _on: bool) -> None:
        self._sync_selected_view()  # (each mode has its own selected shape)
        self._picture_version = -1  # (the Selected view shows the mask itself in Mask mode, the element in Layer mode)
        self._request()

    def _remove_shape_by_key(self, shape_id: str) -> None:
        """Delete or Backspace in the Selected view (M14): the selected shape goes, as with Remove on the details panel
        ("Remove Rectangle 2", one undo step), a mask shape in Mask mode. Never the element itself: that's the
        timeline's Delete."""
        if not shape_id or self.show_data.read_only:
            return
        if self.selected_canvas.mask_mode:
            if shape_id == self.mask_shape_id:
                self.details.select_mask_shape(shape_id)  # (the details panel removes the shape it shows)
                self.details.remove_selected_mask_shape()
            return
        if shape_id == self.shape_id:
            self.details.select_shape(shape_id)
            self.details.remove_selected_shape()

    def _element(self):
        inp = self.current_input()
        if inp is None or not self.element_id:
            return None
        found = find_element(inp, self.element_id)
        return found[2] if found else None

    def _sync_selected_view(self) -> None:
        element = self._element()
        self.selected_canvas.time = self.element_time()
        self.selected_canvas.folder = self.show_data.folder
        self.selected_canvas.set_element(element, self.mask_shape_id if self.selected_canvas.mask_mode else self.shape_id)
        self.selected_panel.set_paint_tools(element is not None and element.get("kind") == "paint")

    def _sync_buttons(self) -> None:
        inp = self.current_input()
        self.stack.setCurrentIndex(1 if inp is not None else 0)
        self.editing.set_name(inp.name if inp is not None else None)
        self.editing.set_editable(not self.show_data.read_only)
        for button in (self.solo_button, self.send_button, self.ai_button):
            button.setEnabled(inp is not None)

    def _rename_input(self, name: str) -> None:
        """The title's name was changed where it is (one undo step, as on the Input tab)."""
        inp = self.current_input()
        if inp is not None and name.strip() and name.strip() != inp.name and not self.show_data.read_only:
            self.document.apply(update_input(self.show_data, self.input_id, name=name), f"Rename {inp.name} → {name.strip()}")

    # ------------------------------------------------------------ the playhead

    def element_time(self) -> float:
        """The selected element's own time at the playhead (keyframes count from its start)."""
        inp, element = self.current_input(), self._element()
        if inp is None or element is None or inp.infinite:
            return self.playhead
        return max(0.0, self.playhead - element_span(element, inp)[0])

    def seek(self, t: float) -> None:
        """Move the playhead (it carries on from there if it's playing)."""
        inp = self.current_input()
        t = max(0.0, float(t))
        if inp is not None and not inp.infinite:
            t = min(t, inp.length)
        self.playhead = t
        self._play_origin = time.monotonic() - t
        self._push_time()
        self._request()

    def set_playing(self, on: bool) -> None:
        inp = self.current_input()
        if on and inp is not None and not inp.infinite and inp.end != END_LOOP and self.playhead >= inp.length - 1e-6:
            self.playhead = 0.0  # at the end: play from the start again
        self.playing = bool(on)
        self._play_origin = time.monotonic() - self.playhead
        self._push_time()
        self._request()

    def _advance(self) -> None:
        """While playing, the playhead follows the clock (looping, or stopping at the end of a timed input)."""
        inp = self.current_input()
        if not self.playing or inp is None:
            return
        t = time.monotonic() - self._play_origin
        if not inp.infinite:
            length = max(inp.length, 1e-3)
            if inp.end == END_LOOP:
                t %= length
            elif t >= length:
                t, self.playing = length, False
        self.playhead = t
        self._push_time()

    def _push_time(self) -> None:
        self.timeline.set_playhead(self.playhead, self.playing)
        self.details.set_time()
        self.selected_canvas.time = self.element_time()
        if self.shape_id or self.mask_shape_id or self.selected_canvas.selected_shape:
            self.selected_canvas.update()

    def _tick(self) -> None:
        if self.playing:
            self._advance()
            self._request()
        self._poll_picture()

    def variables(self):
        """Global variables' current values (from the engine), for drawing and live values."""
        return self._variables if self._variables is not None else {}

    def _sync_scope(self) -> None:
        """Undo follows what the details show: the input being edited, or the show's history on the Globals tab
        (checked whenever the selection changes: selecting an element leaves the Globals tab)."""
        if self._entered:
            self.document.set_scope(self.undo_scope())

    def undo_scope(self) -> str:
        """The undo history the Editor uses: the input it's editing (or the show's, on the Globals tab)."""
        if not self.input_id or self.details.globals_open():
            return SCOPE_SHOW
        return input_scope(self.input_id)

    def _show_changed(self, show: Show) -> None:
        if self.input_id and self.input_id not in show.inputs:  # the input was deleted
            self.input_id = ""
            self.solo_button.setChecked(False)
            if self._entered:
                self.document.set_scope(self.undo_scope())
        element = self._element()
        if element is None:
            self.element_id = ""
            self.shape_id = ""
            self.mask_shape_id = ""
        else:  # (a shape undone away: nothing stays selected, here or in the details)
            shapes = element.get("shapes")
            shapes = [s for s in shapes if hasattr(s, "get")] if isinstance(shapes, (list, tuple)) else []
            if self.shape_id and not any(str(s.get("id")) == self.shape_id for s in shapes):
                self.shape_id = ""
            if self.mask_shape_id and not any(str(s.get("id")) == self.mask_shape_id for s in mask_shapes(element.get("mask"))):
                self.mask_shape_id = ""
        self.selected_canvas.set_show(show)
        self.result_canvas.set_show(show)
        self.result_panel.strip.set_show(show)
        self._sync_selected_view()
        self._sync_buttons()
        self._request()

    # ------------------------------------------------------------ shapes in the Selected view

    def _drag_started(self) -> None:
        self._drag_t = self.element_time()  # one moment for the whole drag, even while playing
        self.document.begin_drag()

    def _drag_ended(self, text: str) -> None:
        self._drag_t = None
        self.document.end_drag(text)

    def _key_time(self) -> float:
        return self._drag_t if self._drag_t is not None else self.element_time()

    def _shape_group(self) -> str:
        """The shapes the Selected view edits now: the Paint element's, or (in Mask mode) the mask's."""
        return "mask.shapes" if self.selected_canvas.mask_mode else "shapes"

    def _shape_dragged(self, element_id: str, shape_id: str, values: dict) -> None:
        if self.input_id:  # keyframed settings get a key at the playhead (where it was when the drag began)
            self.document.preview(set_shape_values_at(self.show_data, self.input_id, element_id, shape_id, values,
                                                      self._key_time(), self._shape_group()))

    def _shape_drawn(self, shape_type: str, values: dict) -> None:
        if not self.input_id or not self.element_id:
            return
        group = self._shape_group()
        show, shape_id = add_shape(self.show_data, self.input_id, self.element_id, shape_type,
                                   values["x"], values["y"], values["w"], values["h"], group=group,
                                   grey=self.selected_canvas.mask_grey)
        what = ("mask " if group == "mask.shapes" else "") + ("rectangle" if shape_type == "rect" else "ellipse")
        if shape_id and self.document.apply(show, f"Draw {what} {shape_id}"):
            self.selected_panel.select_tool.setChecked(True)
            self._shape_from_viewport(shape_id)

    # ------------------------------------------------------------ painting

    def _picture_now(self, what: str):
        """The selected element's painted strokes or mask as they are now (None: nothing painted yet)."""
        element = self._element()
        if element is None:
            return None
        if what == "mask":
            mask = element.get("mask")
            name = mask.get("paint") if hasattr(mask, "get") else None
            path = media_path(self.show_data.folder, name) if isinstance(name, str) else None
            return load_mask(path) if path is not None else None
        name = element.get("strokes")
        path = media_path(self.show_data.folder, name) if isinstance(name, str) else None
        return load_strokes(path) if path is not None else None

    def _painted(self, what: str, canvas, verb: str) -> None:
        """A stroke (or fill) ended: save the picture as a new file and point the element at it (one undo step;
        undo goes back to the file before, exactly)."""
        element = self._element()
        folder = self.show_data.folder
        if element is None or not folder or self.show_data.read_only:
            return
        try:
            name = save_asset(folder, MASKS_DIR if what == "mask" else PAINT_DIR, canvas.to_asset())
        except (OSError, ValueError) as exc:
            log_exception("saving painting", exc)
            return
        path = "mask.paint" if what == "mask" else "strokes"
        self.document.apply(set_element_value(self.show_data, self.input_id, self.element_id, path, name),
                            f"{element_name(element)}: {verb}")

    def invert_mask(self) -> None:
        """Invert, in Mask mode (M14): the selected element's whole mask turned round, white to black and black to
        white (its painting as a new file, its shapes' greys, keyframes and formulas too; not the ticks). One undo step,
        and again turns it back exactly (a painting that comes out white everywhere is no painting, as a mask starts)."""
        import numpy as np

        element = self._element()
        folder = self.show_data.folder
        if element is None or not self.input_id or self.show_data.read_only:
            return
        painted = self._picture_now("mask")
        flipped = 255 - (painted if painted is not None else np.full((ASSET_H, ASSET_W), 255, dtype=np.uint8))
        name = None
        if int(flipped.min()) < 255:
            if not folder:
                return
            try:
                name = save_asset(folder, MASKS_DIR, np.ascontiguousarray(flipped, dtype=np.uint8))
            except (OSError, ValueError) as exc:
                log_exception("inverting a mask", exc)
                return
        self.document.apply(invert_mask(self.show_data, self.input_id, self.element_id, name),
                            f"{element_name(element)}: invert the mask")

    def _picture_dragged(self, element_id: str, values: dict) -> None:
        """The gizmo on an Image, Video or NDI element (keyframed settings get a key at the playhead)."""
        if self.input_id:
            self.document.preview(set_element_values_at(self.show_data, self.input_id, element_id, values, self._key_time()))

    def _toggle_light(self, light_id: str) -> None:
        element = self._element()
        if element is None:
            return
        mask = element.get("mask")
        old = set(mask.get("exclude", ()) or ()) if hasattr(mask, "get") else set()
        excluded = old ^ {light_id}
        names = {fx.id: fx.name for fx in self.show_data.colour_fixtures()}
        self.document.apply(set_excluded(self.show_data, self.input_id, self.element_id, sorted(excluded)),
                            lights_change_name(element_name(element), old, excluded, names))

    # ------------------------------------------------------------ solo, send, live

    def _solo_toggled(self, on: bool) -> None:
        self.engine.submit(SetSolo(self.input_id if on and self.input_id else None))

    def _send(self) -> None:
        if self.input_id:
            self.engine.submit(SendToBase(self.input_id))

    def set_frame(self, frame: EngineFrame) -> None:
        if frame.variables is not self._variables:  # a global changed: the viewports follow
            self._variables = frame.variables
            self._request()
        live = bool(self.input_id) and (self.input_id in frame.live_inputs or frame.solo == self.input_id)
        if live != self._live:
            self._live = live
            self.live_banner.setVisible(live)
        self._solo_live = frame.solo == self.input_id and bool(self.input_id)
        if self.solo_button.isChecked() != (frame.solo == self.input_id and bool(self.input_id)):
            blocked = self.solo_button.blockSignals(True)
            self.solo_button.setChecked(frame.solo == self.input_id and bool(self.input_id))
            self.solo_button.blockSignals(blocked)

    # ------------------------------------------------------------ rendering the viewports

    def _request(self) -> None:
        if self.input_id and self._renderer_running and self._entered:  # (not while the Editor isn't on screen)
            self._renderer.request(self.show_data, self.input_id, self.element_id or None, self.playhead, self.variables())

    def _poll_picture(self) -> None:
        version, picture = self._renderer.pictures.get_versioned()
        if picture is None or version == self._picture_version or picture.input_id != self.input_id:
            return
        self._picture_version = version
        self.result_canvas.set_image(rgb_image(picture.result))
        self.result_panel.strip.set_colours(picture.colours)
        if self.element_id and picture.element_id == self.element_id:
            if self.selected_canvas.mask_mode:  # (the mask itself, black and white: white everywhere with none yet)
                import numpy as np

                keep = picture.keep if picture.keep is not None else np.full((ASSET_H, ASSET_W), 255, dtype=np.uint8)
                self.selected_canvas.set_image(mask_image(keep))
            else:  # (no picture yet, e.g. an NDI feed with no signal: show nothing, not the last element's picture)
                self.selected_canvas.set_image(rgba_image(picture.selected) if picture.selected is not None else None)
            self.selected_canvas.set_patches(picture.patches)
        elif not self.element_id:
            self.selected_canvas.set_image(None)
            self.selected_canvas.set_patches(())

    def render_sync(self) -> None:
        """Draw the viewports straight away, without waiting for the render thread (tests, screenshots)."""
        if not self.input_id:
            return
        from ..editor_render import EditorRequest

        picture = self._renderer.render_now(EditorRequest(self.show_data, self.input_id, self.element_id or None, self.playhead, self.variables()))
        if picture is not None:
            self._renderer.pictures.put(picture)
            self._poll_picture()
            self.selected_canvas.drop_preview()

    def on_enter(self) -> None:
        self._entered = True
        if not self._renderer_running:
            self._renderer.start()
            self._renderer_running = True
        self._poll.start()
        self._request()

    def on_leave(self) -> None:
        """Leaving the Editor: Show on lights switches off, and the viewports stop drawing."""
        self._entered = False
        if self.solo_button.isChecked():
            self.solo_button.setChecked(False)
        elif self._solo_live:  # (a stale frame can have unticked the button while the lights still show it)
            self.engine.submit(SetSolo(None))
        self._solo_live = False
        self._poll.stop()
        if self._renderer_running:
            self._renderer.idle()  # it re-draws moving inputs a few times a second: not while nobody looks

    def shutdown(self) -> None:
        self.on_leave()
        if self._renderer_running:
            self._renderer.stop()
            self._renderer_running = False

"""The Dashboard page: the big Live view, the input grid, the layer stack and the output.

- Live: a glowing dot per light over a dim copy of the Base picture.
- Inputs: one tile per input. Click = send to Base (Shift = cut), right-click = more.
- Layers: what's on Base (and, from M2, in each overlay).
- Output: exactly what each light is sent (in Simulated mode: what *would* be sent).
"""

from __future__ import annotations

from PySide6.QtCore import QPoint, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QScrollArea,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..engine import EngineFrame
from ..model import Show
from ..output import to_byte
from ..preview import Previewer
from . import theme
from .input_grid import InputGrid
from .light_view import LightView, rgb_colour
from .variables_panel import VariablesPanel
from .widgets import ElidedLabel, Fader, Panel, ResettableSplitter, ToggleSwitch, discard

PREVIEW_REFRESH_MS = 2000  # idle tiles re-render this often (NDI previews follow the feed)


class Swatch(QWidget):
    """A small rounded square of colour."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._colour = QColor("#000000")
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

    def sizeHint(self) -> QSize:  # noqa: N802
        side = self.fontMetrics().height()
        return QSize(side, side)

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return self.sizeHint()

    def set_colour(self, colour: QColor) -> None:
        if colour != self._colour:
            self._colour = colour
            self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(QColor(theme.BORDER_STRONG))
        painter.setBrush(self._colour)
        painter.drawRoundedRect(self.rect().adjusted(0, 0, -1, -1), 3, 3)
        painter.end()


class NotesPanel(QFrame):
    """The loaded show's notes (M13), in a small panel that folds away (click its title; remembered). Hidden when
    there are none. Long notes scroll inside it."""

    folded = Signal(bool)  # True = folded away

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Panel")
        self.toggle = QToolButton()
        self.toggle.setObjectName("PanelTitle")
        self.toggle.setText("SHOW NOTES")
        self.toggle.setCheckable(True)
        self.toggle.setChecked(True)
        self.toggle.setArrowType(Qt.ArrowType.DownArrow)
        self.toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.toggle.setAutoRaise(True)
        self.toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        self.toggle.setToolTip("Fold the notes away (or open them). Show ▾ → Notes… changes them.")
        self.toggle.toggled.connect(self._folded)
        self.text = QLabel()
        self.text.setObjectName("NotesText")
        self.text.setWordWrap(True)
        self.text.setTextFormat(Qt.TextFormat.PlainText)
        self.text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setMaximumHeight(140)
        self.scroll.setWidget(self.text)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 8, 14, 12)
        layout.setSpacing(6)
        layout.addWidget(self.toggle, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(self.scroll)
        self.setVisible(False)

    def set_notes(self, notes: str) -> None:
        if self.text.text() != notes:
            self.text.setText(notes)
        self.setVisible(bool(notes))

    def _folded(self, open_: bool) -> None:
        self.scroll.setVisible(open_)
        self.toggle.setArrowType(Qt.ArrowType.DownArrow if open_ else Qt.ArrowType.RightArrow)
        self.folded.emit(not open_)

    def set_folded(self, folded: bool) -> None:
        if self.toggle.isChecked() == folded:
            self.toggle.setChecked(not folded)


class OutputRow(QFrame):
    """One light in the Output panel: swatch, name, DMX channels and the values sent."""

    def __init__(self, name: str, channels: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Row")
        self.swatch = Swatch()
        self.name = ElidedLabel(name)
        self.name.setObjectName("RowName")
        self.channels = QLabel(f"ch {channels}")
        self.channels.setObjectName("RowDetail")
        self.value = QLabel("")
        self.value.setObjectName("RowDetail")
        self.value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.value.setMinimumWidth(self.value.fontMetrics().horizontalAdvance("255 255 255") + 4)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 4)
        layout.setSpacing(8)
        layout.addWidget(self.swatch)
        layout.addWidget(self.name, 1)
        layout.addWidget(self.channels)
        layout.addWidget(self.value)


class LayerRow(QFrame):
    """One row of the layer stack: the slot's name and what's in it. Overlay rows also have
    an on/off switch and an opacity fader."""

    switched = Signal(int, bool)  # slot, on
    opacity_moved = Signal(int, float)  # slot, level

    def __init__(self, slot: str, number: int = 0, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Row")
        self.number = number
        self.slot = QLabel(slot)
        self.slot.setObjectName("RowName")
        self.content = ElidedLabel("—")
        self.content.setObjectName("RowDetail")
        self.content.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(8)
        top.addWidget(self.slot)
        top.addWidget(self.content, 1)
        self.switch: ToggleSwitch | None = None
        self.opacity: Fader | None = None
        self.locked = False  # the padlock is on
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 5, 0, 5)
        layout.setSpacing(4)
        layout.addLayout(top)
        if number:
            self.switch = ToggleSwitch()
            self.switch.setToolTip(f"Overlay {number} on / off")
            self.switch.clicked.connect(lambda *_: self.switched.emit(self.number, self.switch.isChecked()))
            top.addWidget(self.switch)
            controls = QHBoxLayout()
            controls.setContentsMargins(0, 0, 0, 0)
            controls.setSpacing(8)
            caption = QLabel("Opacity")
            caption.setObjectName("RowDetail")
            self.opacity = Fader()
            self.opacity.setMaximumWidth(16777215)
            self.opacity.setToolTip(f"How see-through Overlay {number} is")
            self.opacity.moved.connect(lambda level: self.opacity_moved.emit(self.number, level))
            self.opacity_text = QLabel("100%")
            self.opacity_text.setObjectName("RowDetail")
            self.opacity_text.setFixedWidth(self.opacity_text.fontMetrics().horizontalAdvance("100%") + 4)
            self.opacity_text.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            controls.addWidget(caption)
            controls.addWidget(self.opacity, 1)
            controls.addWidget(self.opacity_text)
            layout.addLayout(controls)

    def set_overlay(self, name: str, on: bool, opacity: float, has_input: bool) -> None:
        self.content.setText(name or "—")
        if self.switch is not None:
            if self.switch.isChecked() != on:
                self.switch.setChecked(on)
            self.switch.setEnabled(has_input and not self.locked)
        if self.opacity is not None:
            self.opacity.set_level(opacity)
            if not self.opacity.is_dragging():
                self.opacity_text.setText(f"{round(opacity * 100)}%")


class DashboardPage(QWidget):
    send_requested = Signal(str, bool)  # input id, cut
    clear_base_requested = Signal(bool)  # cut
    overlay_requested = Signal(str, int)  # input id, slot (toggle rule)
    overlay_switched = Signal(int, bool)  # slot, on (False = take it off)
    overlay_opacity = Signal(int, float)  # slot, level
    edit_requested = Signal(str)
    new_requested = Signal()
    duplicate_requested = Signal(str)
    delete_requested = Signal(str)
    variable_changed = Signal(str, object)  # global variable name, value
    ai_requested = Signal(str)  # input id to edit with AI ("" = make a new one)
    import_requested = Signal()  # Import from another show…

    def __init__(self, show: Show, sources=None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("DashboardPage")
        self.show_data = show
        self._preview_seq = -1
        self._base_id = ""
        self._locked = False
        self._previewer = Previewer(sources)
        self._preview_timer = QTimer(self)
        self._preview_timer.setInterval(PREVIEW_REFRESH_MS)
        self._preview_timer.timeout.connect(self.refresh_previews)
        self._preview_timer.start()

        live_panel = Panel("Live")
        self.light_view = LightView(show)
        self.caption = ElidedLabel("Base: Black")
        self.caption.setObjectName("Caption")
        live_panel.body.addWidget(self.light_view, 1)
        live_panel.body.addWidget(self.caption)

        inputs_panel = Panel("Inputs")
        self.grid = InputGrid(show)
        self.grid.send_requested.connect(self.send_requested.emit)
        self.grid.menu_requested.connect(self._tile_menu)
        self.grid.overlay_requested.connect(self.overlay_requested.emit)
        self.grid.edit_requested.connect(self.edit_requested.emit)
        self.grid.new_requested.connect(self.new_requested.emit)
        self.grid.ai_requested.connect(self.ai_requested.emit)
        self.grid.import_requested.connect(self.import_requested.emit)
        grid_scroll = QScrollArea()
        grid_scroll.setWidgetResizable(True)
        grid_scroll.setWidget(self.grid)
        grid_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        grid_scroll.setMinimumHeight(120)
        inputs_panel.body.addWidget(grid_scroll, 1)
        self.grid_scroll = grid_scroll

        self.left = ResettableSplitter(Qt.Orientation.Vertical, self.default_left_split)
        self.left.setObjectName("DashboardLeft")
        self.left.addWidget(live_panel)
        self.left.addWidget(inputs_panel)
        self.left.setStretchFactor(0, 3)
        self.left.setStretchFactor(1, 2)

        layers = Panel("Layers")
        self.layer_rows: dict[str, LayerRow] = {}
        for number in (4, 3, 2, 1):  # top layer first, like the timeline
            row = LayerRow(f"Overlay {number}", number)
            row.switched.connect(self.overlay_switched.emit)
            row.opacity_moved.connect(self.overlay_opacity.emit)
            layers.body.addWidget(row)
            self.layer_rows[f"overlay{number}"] = row
        base_row = LayerRow("Base")
        layers.body.addWidget(base_row)
        self.layer_rows["base"] = base_row
        base_row.content.setText("Black")

        self.output_panel = Panel("Output")
        self.output_hint = QLabel("")
        self.output_hint.setObjectName("Hint")
        self.output_hint.setWordWrap(True)
        self.output_panel.body.addWidget(self.output_hint)
        self.output_rows: dict[str, OutputRow] = {}
        self._rows_key: tuple | None = (show.fixtures, show.house)  # the lights the output rows were built for
        self._build_output_rows()

        column = QWidget()
        column.setObjectName("ScrollContent")
        column_layout = QVBoxLayout(column)
        column_layout.setContentsMargins(0, 0, 4, 0)
        column_layout.setSpacing(12)
        self.variables = VariablesPanel()
        self.variables.changed.connect(self.variable_changed.emit)
        self.variables.set_globals(show.globals)
        self.notes_panel = NotesPanel()
        self.notes_panel.set_notes(show.notes)
        column_layout.addWidget(layers)
        column_layout.addWidget(self.notes_panel)  # (below the layers: long notes never push Base and the overlays away)
        column_layout.addWidget(self.variables)
        column_layout.addWidget(self.output_panel)
        column_layout.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(column)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setMinimumWidth(300)

        self.splitter = ResettableSplitter(Qt.Orientation.Horizontal, self.default_split)
        self.splitter.setObjectName("DashboardSplitter")
        self.splitter.addWidget(self.left)
        self.splitter.addWidget(scroll)
        self.splitter.setStretchFactor(0, 1)
        self.splitter.setStretchFactor(1, 0)
        self.left.setMinimumWidth(420)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.addWidget(self.splitter)

    # ------------------------------------------------------------ splits

    def default_split(self) -> list[int]:
        total = max(1, self.splitter.width())
        right = min(420, max(320, total // 4))
        return [total - right, right]

    def default_left_split(self) -> list[int]:
        total = max(1, self.left.height())
        inputs = max(160, round(total * 0.38))
        return [total - inputs, inputs]

    def apply_split(self, sizes: dict | list | None) -> None:
        """Use remembered splitter sizes, or the defaults."""
        if isinstance(sizes, dict):
            outer, left = sizes.get("outer"), sizes.get("left")
        else:
            outer, left = sizes, None
        self.splitter.apply_sizes(outer)
        self.left.apply_sizes(left)

    def splits(self) -> dict:
        return {"outer": self.splitter.sizes(), "left": self.left.sizes()}

    # ------------------------------------------------------------ show and frames

    def set_show(self, show: Show) -> None:
        self.show_data = show
        self.notes_panel.set_notes(show.notes)
        self.light_view.set_show(show)
        self.grid.set_show(show)
        self.variables.set_globals(show.globals)
        if (show.fixtures, show.house) != self._rows_key:  # (only when the lights change, not on every edit)
            self._rows_key = (show.fixtures, show.house)
            for row in self.output_rows.values():
                self.output_panel.body.removeWidget(row)
                discard(row)
            self.output_rows.clear()
            self._build_output_rows()
        self.refresh_previews()  # (only while the Dashboard is on screen: showing it redraws them)

    def refresh_previews(self, force: bool = False) -> None:
        """Preview every input that isn't on Base (same rendering as the engine)."""
        if not force and not self.isVisible():
            return
        for inp in self.show_data.ordered_inputs():
            if inp.id != self.grid.base_id:
                self.grid.set_preview(inp.id, self._previewer.colours(self.show_data, inp))

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self.refresh_previews(force=True)

    def set_active(self, active: bool) -> None:
        """Stop re-drawing tile previews while the window is hidden (in the tray)."""
        if active and not self._preview_timer.isActive():
            self._preview_timer.start()
        elif not active:
            self._preview_timer.stop()

    def set_locked(self, locked: bool) -> None:
        self._locked = locked
        self.grid.set_locked(locked)
        self.variables.setEnabled(not locked)
        for row in self.layer_rows.values():  # the overlays' switches and opacity faders too
            row.locked = locked
            if row.switch is not None:
                row.switch.setEnabled(not locked and row.switch.isEnabled())
            if row.opacity is not None:
                row.opacity.setEnabled(not locked)

    def _build_output_rows(self) -> None:
        for fx in self.show_data.fixtures:
            label = fx.name if fx.is_colour else self.show_data.house_label(fx.id)
            row = OutputRow(label, fx.channel_range())
            self.output_panel.body.addWidget(row)
            self.output_rows[fx.id] = row

    def _tile_menu(self, input_id: str, where: QPoint) -> None:
        if self._locked:
            return
        inp = self.show_data.inputs.get(input_id)
        if inp is None:
            return
        menu = QMenu(self)
        menu.addAction("Send to Base", lambda: self.send_requested.emit(input_id, False))
        menu.addAction("Cut to Base", lambda: self.send_requested.emit(input_id, True))
        overlays = menu.addMenu("Overlay")
        for slot in (1, 2, 3, 4):
            overlays.addAction(f"Overlay {slot} on / off", lambda s=slot: self.overlay_requested.emit(input_id, s))
        menu.addSeparator()
        menu.addAction("Edit", lambda: self.edit_requested.emit(input_id))
        menu.addAction("Edit with AI…", lambda: self.ai_requested.emit(input_id))
        menu.addAction("Duplicate", lambda: self.duplicate_requested.emit(input_id))
        menu.addAction("Delete…", lambda: self.delete_requested.emit(input_id))
        if input_id == self._base_id:
            menu.addSeparator()
            menu.addAction("Stop (fade Base to black)", lambda: self.clear_base_requested.emit(False))
        menu.exec(where)
        menu.deleteLater()  # (each right-click used to leave its menus behind)

    def set_frame(self, frame: EngineFrame) -> None:
        self.variables.set_values(frame.variables)
        house_out = {fid: view.out for fid, view in frame.house.items()}
        self.light_view.set_levels(frame.colours, house_out)
        if frame.preview is not None and frame.preview_seq != self._preview_seq:
            self._preview_seq = frame.preview_seq
            data = frame.preview
            image = QImage(data.data, data.shape[1], data.shape[0], data.shape[1] * 3, QImage.Format.Format_RGB888).copy()
            self.light_view.set_picture(image)
        for fid, row in self.output_rows.items():
            if fid in frame.colours:
                rgb = frame.colours[fid]
                row.swatch.set_colour(rgb_colour(rgb))
                row.value.setText(" ".join(str(to_byte(v)) for v in rgb))
            else:
                level = house_out.get(fid, 0.0)
                grey = to_byte(level)
                row.swatch.set_colour(QColor(grey, grey, grey))
                row.value.setText(f"{round(level * 100)}%")
        if frame.output.kind == "simulated":
            self.output_hint.setText("Simulated: this is what would be sent. Nothing reaches the lights.")
        else:
            self.output_hint.setText(f"What's being sent to {frame.output.label or 'the lights'}.")
        self._base_id = frame.base_id
        base = frame.base
        if frame.base_progress is not None:
            base = f"{base} (fading in, {round(frame.base_progress * 100)}%)"
        self.layer_rows["base"].content.setText(base)
        on_names = []
        for number, view in enumerate(frame.overlays, start=1):
            row = self.layer_rows.get(f"overlay{number}")
            if row is not None:
                row.set_overlay(view.name, view.on, view.opacity, bool(view.input_id))
            if view.on and view.name:
                on_names.append(f"{number}: {view.name}")
        notes = []
        if frame.solo:
            notes.append("the Editor is showing one input on the lights")
        if frame.identify:
            notes.append("Identify is on")
        if frame.testing:
            notes.append("test colours are on")
        extra = f"  ·  {', '.join(notes)}" if notes else ""
        overlays = ", ".join(on_names) if on_names else "none"
        self.caption.setText(f"Base: {base}  ·  Overlays: {overlays}{extra}")
        self.grid.set_live(frame.base_id, tuple(v.input_id if v.on else "" for v in frame.overlays), frame.colours)
        self.grid.set_playing(frame.playing)

"""The main window: the header, the pages, and the wiring to the engine.

The window never changes the lights itself. Moving a fader or clicking an input sends a
command to the engine; what's shown comes back from the engine's frames. Changes to the
show (moving a light, renaming it...) go through the ShowDocument, which keeps the undo
history, tells the engine, and saves the files.
"""

from __future__ import annotations

import queue
import time
from collections.abc import Mapping
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any

from PySide6.QtCore import QEvent, QRect, Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QGuiApplication, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMessageBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..commands import (
    ClearBase,
    ClearFromOverlay,
    ClearOverlay,
    CountdownAction,
    QuietAutomations,
    SendToBase,
    SendToOverlay,
    ForgetShow,
    ShowLoadFailed,
    ShowLoadWaiting,
    ToBlack,
    SetHouse,
    SetAutomationsPaused,
    SetMaster,
    SetCountdownLength,
    SetDefaultShow,
    CloseShow,
    SetOverlayOn,
    SetOverlayOpacity,
    SetVariable,
)
from .. import startup
from ..automations import update_automation, upcoming
from ..links import original_problem
from ..files import read_json
from ..global_automations import GLOBAL_DIR, GLOBAL_NAME, global_folder
from ..layouts import switch_layout
from ..engine import EngineFrame
from ..input_edit import delete_input, duplicate_input, move_input, new_input, update_input
from ..live_state import LiveState
from ..log import log_exception, log_info
from ..model import NO_SHOW, Show, clean_notes, safe_text
from ..ndi_source import CONNECTED, NO_SIGNAL, SEARCHING
from ..output import device_spec, show_channels
from ..paths import documents_dir, settings_path
from ..paths import shows_root as default_shows_root
from ..settings import QUIT_ASK, QUIT_LEAVE, QUIT_OFF, Settings
from ..clshow import (
    PackageError,
    clean_leftover_imports,
    export_everything,
    export_file_name,
    export_show,
    import_globals,
    import_settings,
    import_show,
    keep_both_name,
    name_taken,
    read_package,
    size_text,
)
from ..show_import import apply_import, copy_files, imported_automation_ids
from ..show_io import SHOW_FILE, ShowError, backup_show
from ..show_io import load_show as read_show
from ..shows import (
    ShowInfo,
    delete_show,
    duplicate_show,
    find_show,
    list_shows,
    new_show,
    read_show_name,
    same_folder,
    show_id,
    to_recycle_bin,
    unique_name,
)
from . import icons
from .bridge import EngineBridge
from .colour_picker import preview_choice
from .dashboard import DashboardPage
from ..ai_guide import AIReplyError, parse_reply, write_global_guide, write_guide_files
from .ai_dialog import AIDialog
from .automation_page import AutomationPage
from .file_watcher import FolderWatcher
from ..video_source import videos
from .document import KIND_GLOBAL, SCOPE_AUTOMATIONS, SCOPE_LAYOUT, SCOPE_SHOW, ShowDocument
from .editor_page import EditorPage
from .header import PAGES, Header
from .layout_page import LayoutPage
from .no_show import NoShowPage, automations_line
from .countdown_banner import CountdownBanner
from .package_dialogs import GLOBALS_ROW, SETTINGS_ROW, ExportDialog, ImportPackageDialog, Stopped, run_with_progress
from .import_dialog import AUTOMATIONS, INPUTS, ImportItemsDialog, NotesDialog
from .linked_media import LinkedMedia
from .preview_page import LookPreview
from .quit_dialog import QuitDialog
from .settings_dialog import SettingsDialog
from .widgets import STATE_ERROR, STATE_INFO, STATE_OK, STATE_WARN, install_wheel_guard

CARD_CHECK = 20.0  # seconds: how often the No show loaded card checks which global automation loads a show next (M14)
LOAD_HOLD = 30.0  # seconds a show asked for by an automation or the Stream Deck waits for an open dialog to close
QUIT_WORDS = {QUIT_OFF: "turn the lights off", QUIT_LEAVE: "leave the lights as they are"}  # (for the session log)
LIVE_HEARTBEAT_MS = 60_000  # live.json is saved at least this often, so a restart knows when CentraLight last ran


def _live_key(state) -> dict:
    """What decides whether live.json needs saving again: everything except the clocks, which move every frame."""
    data = state.to_dict()
    data.pop("saved_at", None)
    if data.get("base") is not None:
        data["base"] = {"input": data["base"].get("input")}
    overlay = data.get("overlay")
    if isinstance(overlay, dict):
        data["overlay"] = {**overlay, "copies": [{k: v for k, v in c.items() if k != "time"} if isinstance(c, dict) else c
                                                 for c in overlay.get("copies") or ()]}
    return data


ENGINE_STUCK_S = 3.0  # no new frame for this long: the engine is stuck (the lights are frozen)
MIN_WIDTH = 1280
MIN_HEIGHT = 720
LIVE_SAVE_DELAY_MS = 1500
BACKUP_INTERVAL_MS = 10 * 60 * 1000
# Keys the app already uses: an input's hotkey can't be one of these
RESERVED_KEYS = {"Ctrl+Z", "Ctrl+Y", "Ctrl+Shift+Z", "Ctrl+1", "Ctrl+2", "Ctrl+3", "Ctrl+4", "Ctrl+,", "Ctrl+Shift+D", "Ctrl+V"}


class MainWindow(QWidget):
    def __init__(
        self,
        settings: Settings,
        show: Show,
        engine: Any,
        output: Any,
        *,
        save_files: bool = True,
        live_path: Path | None = None,
        notes_path: Path | None = None,
        remote: Any | None = None,
        global_automations: Show | None = None,
        shows_root: Path | None = None,
    ) -> None:
        """global_automations: a Show holding just the global automations (none: an empty one that isn't saved).
        shows_root: the folder the shows are in (Documents\\CentraLight Shows)."""
        super().__init__()
        self.settings = settings
        self._shows_root = Path(shows_root) if shows_root is not None else None
        self.engine = engine
        self.output = output
        self.remote = remote  # the Stream Deck remote (remote.RemoteServer), if it's running
        self.tray = None  # the tray icon (ui.tray.Tray), when the app has one
        self._quitting = False
        self.instance_server = None  # kept alive here (see app.py)
        self.power_filter = None
        self._hotkeys: list[QShortcut] = []
        self._hotkey_map: dict[str, str] = {}
        self._save_files = save_files
        self._live_path = live_path
        self._shut_down = False
        self._locked = False
        install_wheel_guard(QApplication.instance())  # the wheel only changes controls that have been clicked into
        self._last_seq: int | None = None
        self._last_new_frame = time.monotonic()
        self._engine_stuck = False
        self._engine_check = QTimer(self)
        self._engine_check.setInterval(1000)
        self._engine_check.timeout.connect(self._check_engine)
        self._engine_check.start()
        self._pending_live: LiveState | None = None
        self._saved_live: dict | None = None
        self._splits_applied = False
        self.last_frame: EngineFrame | None = None
        self.document = ShowDocument(show, engine, save_files=save_files)
        self.document.changed.connect(self._show_changed)
        self.document.loaded.connect(self._show_loaded)
        self.document.save_problem.connect(lambda _text: self._update_files_chip())
        # the global automations: app-wide, with a history and autosave of their own (in the same undo group)
        globals_ = global_automations if global_automations is not None else Show(name=GLOBAL_NAME)
        self.global_document = ShowDocument(globals_, engine, save_files=save_files, kind=KIND_GLOBAL,
                                            undo_group=self.document.undo_group)
        self.global_document.save_problem.connect(lambda _text: self._update_files_chip())
        # files that couldn't be read when the show opened, and other warnings (they used to go only to the log); the
        # global automations' too
        self._global_problems = [f"{GLOBAL_DIR}\\{warning}" for warning in globals_.warnings]
        if globals_.read_only and globals_.folder:
            self._global_problems.append(f"The global automations in {globals_.folder} couldn't be opened, so they "
                                         "won't run or be saved this time (the session log says why).")
        self._load_problems = [f"inputs\\{input_id}.json couldn't be read: {error}" for input_id, error in show.input_errors.items()]
        self._load_problems += list(show.warnings) + self._global_problems
        self._waiting_load: tuple[str, dict, float] | None = None  # a show asked for while a dialog was open (who, what, since)
        self._asked_for: str | None = None  # the global automation's name for the show being loaded (see _load_requested)
        self._native_dialogs = 0  # Windows' own dialogs open (they aren't Qt's: see _dialog_open)
        self._package_job: dict | None = None  # a .clshow import or export under way (show loads wait for it)
        self._importing = False

        self.setObjectName("Root")
        self.setWindowIcon(icons.app_icon())
        self._apply_minimum_size()

        self.header = Header(show.name, [(h.fixture, h.label) for h in show.house])
        self.lock_banner = QFrame()
        self.lock_banner.setObjectName("LockBanner")
        banner_text = QLabel("Locked: the lights controls won't respond to clicks. Click the padlock to unlock.")
        banner_text.setObjectName("LockBannerText")
        banner_layout = QHBoxLayout(self.lock_banner)
        banner_layout.setContentsMargins(14, 4, 14, 4)
        banner_layout.addWidget(banner_text)
        self.lock_banner.setVisible(False)
        # the countdown before an automatic show load or Cut / Fade to black (M13): Load now, Wait 10 min, Skip
        self.countdown_banner = CountdownBanner()
        self.countdown_banner.pressed.connect(lambda what, cid: self.engine.submit(CountdownAction(what, cid)))
        self.engine.submit(SetCountdownLength(settings.countdown))
        self.engine.submit(SetDefaultShow(settings.default_show, settings.black_fade))  # (it comes back after a schedule, M14)

        sources = getattr(engine, "sources", None)
        self.dashboard = DashboardPage(show, sources=sources)
        self.dashboard.send_requested.connect(lambda input_id, cut: self.engine.submit(SendToBase(input_id, cut=cut)))
        self.dashboard.clear_base_requested.connect(lambda cut: self.engine.submit(ClearBase(cut=cut)))
        self.dashboard.overlay_pressed.connect(lambda input_id, tag: self._locked or self.engine.submit(SendToOverlay(input_id, tag=tag)))
        # (letting go of a held Send to Overlay always clears its copy, so a flash never sticks)
        self.dashboard.overlay_held.connect(lambda _input_id, tag: self.engine.submit(ClearFromOverlay(tag=tag)))
        self.dashboard.copy_clear_requested.connect(lambda number: self._locked or self.engine.submit(ClearFromOverlay(copy=number)))
        self.dashboard.overlay_input_cleared.connect(
            lambda input_id: self._locked or self.engine.submit(ClearFromOverlay(input_id=input_id)))
        self.dashboard.overlay_switched.connect(lambda on: self._locked or self.engine.submit(SetOverlayOn(on)))
        self.dashboard.overlay_cleared.connect(lambda: self._locked or self.engine.submit(ClearOverlay()))
        self.dashboard.overlay_opacity.connect(lambda level: self._locked or self.engine.submit(SetOverlayOpacity(level)))
        self.dashboard.reorder_requested.connect(self.move_tile)
        self.dashboard.edit_requested.connect(self.open_editor)
        self.dashboard.new_requested.connect(self.new_input)
        self.dashboard.duplicate_requested.connect(self.duplicate_input)
        self.dashboard.delete_requested.connect(self.delete_input)
        self.dashboard.rename_requested.connect(self.ask_rename_input)
        self.dashboard.variable_changed.connect(lambda name, value: self.engine.submit(SetVariable(name, value)))
        self.dashboard.ai_requested.connect(self.open_ai_dialog)
        self.dashboard.import_requested.connect(lambda: self.import_from_show(INPUTS))
        self.dashboard.notes_panel.set_folded(bool(settings.ui.get("notes_folded", False)))  # (as it was left)
        self.dashboard.notes_panel.folded.connect(lambda folded: self.settings.ui.__setitem__("notes_folded", folded))
        # the colour picker's Preview tick box (M14): kept with the PC's start time, so a restart ticks it again
        preview_choice.load = lambda: settings.ui.get("colour_preview")
        preview_choice.store = lambda data: settings.ui.__setitem__("colour_preview", data)
        self.layout_page = LayoutPage(self.document, engine, notes_path=notes_path, shows_provider=self.list_shows)
        self.editor_page = EditorPage(self.document, engine, settings, sources=sources)
        self.editor_page.done.connect(lambda: self.show_page("dashboard"))
        self.editor_page.new_input_requested.connect(self.new_input)
        self.editor_page.ai_requested.connect(self.open_ai_dialog)
        self.automation_page = AutomationPage(self.document, engine, sources_provider=(
            (lambda: sources.available()) if sources is not None and hasattr(sources, "available") else None),
            global_document=self.global_document, shows_provider=self.list_shows)
        self.automation_page.set_default_show(settings.default_show)
        self.automation_page.scope_changed.connect(self._update_undo_scope)
        self.automation_page.import_requested.connect(lambda: self.import_from_show(AUTOMATIONS))
        # linked pictures and videos (M13): their originals are checked and copied on their own thread
        self.linked_media = LinkedMedia(self.document, engine, parent=self)
        self.editor_page.details.set_linked(self.linked_media)
        self.layout_page.set_linked(self.linked_media)
        if save_files:
            clean_leftover_imports(self.shows_root())  # (what an import cut short by a crash left behind)
        self.pages: dict[str, QWidget] = {
            "dashboard": self.dashboard,
            "editor": self.editor_page,
            "layout": self.layout_page,
            "automation": self.automation_page,
        }
        self.stack = QStackedWidget()
        for page in self.pages.values():
            self.stack.addWidget(page)
        self._page = ""
        # no show loaded (M14): every tab but Automation shows this card instead (the lights are off)
        self.no_show_page = NoShowPage()
        self.no_show_page.load_requested.connect(lambda folder: self.load_show(folder))
        self.no_show_page.new_requested.connect(self.ask_new_show)
        self.no_show_page.import_requested.connect(lambda: self.ask_import_package(""))
        self.stack.addWidget(self.no_show_page)
        self._card_checked = 0.0  # when the card's global automations line was last worked out
        self._card_paused: bool | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.header)
        layout.addWidget(self.lock_banner)
        layout.addWidget(self.countdown_banner)
        layout.addWidget(self.stack, 1)

        self.header.page_requested.connect(self.show_page)
        picker = self.header.show_picker
        picker.set_provider(self.list_shows, same_folder)
        picker.set_show(show.name, show.folder)
        picker.load_requested.connect(lambda folder: self.load_show(folder))
        picker.new_requested.connect(self.ask_new_show)
        picker.duplicate_requested.connect(self.ask_duplicate_show)
        picker.rename_requested.connect(self.ask_rename_show)
        picker.delete_requested.connect(self.ask_delete_show)
        picker.notes_requested.connect(self.edit_notes)
        picker.export_requested.connect(lambda everything: self.export_package(everything))
        picker.import_requested.connect(lambda: self.ask_import_package(""))
        picker.close_requested.connect(self.close_show)
        picker.notes = show.notes
        self.header.settings_requested.connect(self.open_settings)
        self.header.house_changed.connect(self._house_changed)
        self.header.master_changed.connect(lambda level: self.engine.submit(SetMaster(level)))
        self.header.black_requested.connect(
            lambda fade, everything: self.engine.submit(ToBlack(self.settings.black_fade if fade else 0.0, everything)))
        self.header.lock_toggled.connect(self.set_locked)
        self.header.chips["files"].set_clickable(True)
        self.header.chips["files"].clicked.connect(self._show_file_problems)
        self._update_files_chip()
        self.header.chips["remote"].set_clickable(True)
        self.header.chips["remote"].clicked.connect(self.open_remote_page)
        self.header.chips["automations"].set_clickable(True)
        self.header.chips["automations"].clicked.connect(lambda: self._locked or self.engine.submit(SetAutomationsPaused(None)))
        self._next_run_text = ""
        self._next_run_checked = 0.0

        for index, (key, _label) in enumerate(PAGES, start=1):
            QShortcut(QKeySequence(f"Ctrl+{index}"), self, activated=lambda k=key: self.show_page(k))
        QShortcut(QKeySequence("Ctrl+,"), self, activated=self.open_settings)
        QShortcut(QKeySequence("Ctrl+Shift+D"), self, activated=self.open_diagnostics)
        QShortcut(QKeySequence.StandardKey.Undo, self, activated=self.undo)
        QShortcut(QKeySequence.StandardKey.Redo, self, activated=self.redo)
        QShortcut(QKeySequence("Ctrl+Shift+Z"), self, activated=self.redo)
        # Ctrl+V on the Dashboard: an input from an AI. (A window shortcut: focus is often on the header's tab
        # button, outside the Dashboard. Text boxes still paste into themselves.)
        QShortcut(QKeySequence.StandardKey.Paste, self, activated=lambda: self._page == "dashboard" and self.paste_input())

        self.bridge = EngineBridge(engine, self)
        self.bridge.frame_ready.connect(self._on_frame)

        self._live_timer = QTimer(self)
        self._live_timer.setSingleShot(True)
        self._live_timer.timeout.connect(self._save_live)
        self._live_heartbeat = QTimer(self)  # (a crash or power cut is then known to the minute at the next start)
        self._live_heartbeat.setInterval(LIVE_HEARTBEAT_MS)
        self._live_heartbeat.timeout.connect(self._save_live_heartbeat)
        if save_files:
            self._live_heartbeat.start()
        self._backup_timer = QTimer(self)
        self._backup_timer.setInterval(BACKUP_INTERVAL_MS)
        self._backup_timer.timeout.connect(self._backup)
        if save_files and show.folder and not show.read_only:
            self._backup_timer.start()

        # AI: watch the show folder for inputs written outside CentraLight, and keep the AI guide up to date
        self.ai_dialog: AIDialog | None = None
        self.watcher = FolderWatcher(self.document, ask_reload=self._ask_reload,
                                     editing=lambda: self.editor_page.input_id if self._page == "editor" else "",
                                     parent=self) if save_files else None
        if self.watcher is not None:
            self.watcher.problems_changed.connect(self.dashboard.grid.set_problems)
            if self.watcher.problems:  # (files that couldn't be read when the show opened)
                self.dashboard.grid.set_problems(dict(self.watcher.problems))
        # ...and the global automations' folder, for ones written outside CentraLight
        self.global_watcher = FolderWatcher(self.global_document, parent=self) if save_files and globals_.folder else None
        self._guide_key = None
        self._guide_timer = QTimer(self)
        self._guide_timer.setSingleShot(True)
        self._guide_timer.setInterval(2000)
        self._guide_timer.timeout.connect(self._write_guide)
        if save_files:
            self._guide_timer.start()

        self._update_title(show)
        self._update_hotkeys(show)
        self._update_remote_chip()
        self._follow_no_show(show)
        self.show_page(settings.page if settings.page in self.pages else "dashboard")
        self._restore_geometry()
        self.bridge.poll()

    @property
    def show_data(self) -> Show:
        return self.document.show

    # ------------------------------------------------------------ pages, lock, undo

    def show_page(self, key: str) -> None:
        page = self.pages.get(key)
        if page is None:
            return
        if self._page == "layout" and key != "layout":
            self.layout_page.on_leave()
        if self._page == "editor" and key != "editor":
            self.editor_page.on_leave()
        self._page = key
        card = self.document.show.no_show and key != "automation"  # (no show loaded, M14: the card instead)
        self.stack.setCurrentWidget(self.no_show_page if card else page)
        self._update_preview_wanted()
        self.header.set_page(key)
        self.settings.page = key
        if key == "layout" and self.last_frame is not None and not card:
            self.layout_page.set_frame(self.last_frame)
        self._update_undo_scope()
        if key == "editor" and not card:
            self.editor_page.on_enter()

    def _update_undo_scope(self) -> None:
        """Undo follows what's on screen: the layout, the input being edited, the show's automations or the global
        ones, or the show."""
        if self._page == "automation" and self.automation_page.global_selected():
            self.global_document.set_scope(SCOPE_AUTOMATIONS)
            return
        scopes = {"layout": SCOPE_LAYOUT, "editor": self.editor_page.undo_scope(), "automation": SCOPE_AUTOMATIONS}
        self.document.set_scope(scopes.get(self._page, SCOPE_SHOW))

    def _active_document(self) -> ShowDocument:
        """The document whose history is on screen (the global automations' on the Automation tab, when one of them
        is selected)."""
        if self._page == "automation" and self.automation_page.global_selected():
            return self.global_document
        return self.document

    def open_editor(self, input_id: str) -> None:
        self.editor_page.open_input(input_id)
        self.show_page("editor")

    # ------------------------------------------------------------ inputs

    def new_input(self) -> None:
        if self._locked or self.document.read_only:
            return
        show, input_id = new_input(self.document.show)
        if self.document.apply(show, "New input"):
            self.open_editor(input_id)

    def duplicate_input(self, input_id: str) -> None:
        inp = self.document.show.inputs.get(input_id)
        if inp is not None:
            show, _new_id = duplicate_input(self.document.show, input_id)
            self.document.apply(show, f"Duplicate {inp.name}")

    def ask_rename_input(self, input_id: str) -> None:
        """Rename… on a tile's menu."""
        inp = self.document.show.inputs.get(input_id)
        if inp is None or self._locked or self.document.read_only:
            return
        name, ok = QInputDialog.getText(self, "Rename input", "New name for this input:\n\n(Stream Deck keys and automations "
                                        "keep working: they use its id, not its name.)", text=inp.name)
        if ok:
            self.rename_input(input_id, name)

    def rename_input(self, input_id: str, name: str) -> bool:
        """One undo step, as on the Editor's title and the Input tab. False if nothing changed."""
        inp = self.document.show.inputs.get(input_id)
        if inp is None or not name.strip() or name.strip() == inp.name or self.document.read_only:
            return False
        return bool(self.document.apply(update_input(self.document.show, input_id, name=name), f"Rename {inp.name} → {name.strip()}"))

    def move_tile(self, input_id: str, index: int) -> None:
        """A tile dragged to another place on the Dashboard (index: before the tile that was there; one undo step)."""
        inp = self.document.show.inputs.get(input_id)
        if inp is None or self._locked or self.document.read_only:
            return
        show = move_input(self.document.show, input_id, index)
        if [i.id for i in show.ordered_inputs()] != [i.id for i in self.document.show.ordered_inputs()]:
            self.document.apply(show, f"Move {inp.name}")

    def delete_input(self, input_id: str) -> None:
        inp = self.document.show.inputs.get(input_id)
        if inp is None:
            return
        users = [a.name for a in self.document.show.ordered_automations()
                 if any(isinstance(item, Mapping) and item.get("input") == input_id
                        for item in (*a.conditions, *a.actions, *a.on_end))]
        used = f"\n\nThese automations use it, and would skip it: {', '.join(users)}." if users else ""
        answer = QMessageBox.question(self, "Delete input", f"Delete “{inp.name}”?{used}\n\nIf it's on Base it keeps "
                                      "playing until something replaces it; its copies on the Overlay go. Ctrl+Z brings "
                                      "it back.")
        if answer == QMessageBox.StandardButton.Yes:
            self.document.apply(delete_input(self.document.show, input_id), f"Delete {inp.name}")

    # ------------------------------------------------------------ shows

    def shows_root(self) -> Path:
        """The folder the shows are in (Documents\\CentraLight Shows)."""
        if self._shows_root is not None:
            return self._shows_root
        folder = self.document.show.folder
        return default_shows_root() if not folder else Path(folder).parent

    def list_shows(self) -> list[ShowInfo]:
        return list_shows(self.shows_root())

    def load_show(self, folder: str, who: str = "", automation: bool = False) -> bool:
        """Load another show (from the picker, or who: a global automation or the Stream Deck; automation: it was a
        global automation). The lights freeze on what they show until one of its inputs is sent; its automations are
        checked straight away. True if it loaded."""
        if same_folder(folder, self.document.show.folder):
            return False
        if self._locked and not who:
            return False
        try:
            show = read_show(Path(folder))
        except (ShowError, OSError) as exc:
            log_info(f"{who or 'Load a show'}: couldn't load the show in {folder} ({exc})")
            if self._asked_for is not None:
                self.engine.submit(ShowLoadFailed(self._asked_for))  # (a global automation was waiting for it)
            if not who:
                QMessageBox.warning(self, "Load a show", f"CentraLight couldn't load the show in\n{folder}\n\n{exc}")
            return False
        for warning in show.warnings:
            log_info(f"Show warning: {warning}")
        if not show.read_only:
            try:
                backup_show(Path(folder))
            except OSError as exc:
                log_exception("show backup", exc)
        self._leave_show()
        self.document.load(show, automation)
        log_info(f"{who + ': ' if who else ''}show “{show.name}” loaded")
        return True

    def _leave_show(self) -> None:
        """Before another show loads (or none): what belonged to this one ends."""
        self._leave_live_modes()  # (Identify, test colours, the Editor's and the AI window's "on the lights" end)
        dialog = getattr(self, "ai_dialog", None)
        if dialog is not None:
            try:
                dialog.close()  # (it belonged to the other show)
            except RuntimeError:
                pass
            self.ai_dialog = None

    def close_show(self) -> None:
        """Show ▾ → None (lights off) (M14): the lights fade off over Fade to black's time (the Festoon and Key lights
        too), then the show closes and the tabs say "No show loaded" (the engine asks for that once they're off)."""
        if self._locked or self.document.show.no_show:
            return
        self.engine.submit(CloseShow(self.settings.black_fade, "Show menu"))

    def _close_now(self, who: str, automation: bool = False) -> bool:
        """No show from now on (M14): every DMX channel at 0, the global automations carry on. automation: a global
        automation's (the default show None, after a schedule ended), not a person's. True if it closed."""
        if self.document.show.no_show:
            return False
        self._leave_show()
        self.document.load(NO_SHOW, automation)
        log_info(f"{who or 'Show menu'}: the show closed (no show loaded, the lights are off)")
        return True

    def _new_show_template(self) -> Show:
        """What a new show takes its lights and layouts from: the loaded show, or with none loaded (M14) the last one
        that was (failing that, the church's standard lights)."""
        show = self.document.show
        if not show.no_show:
            return show
        folder = self.settings.show_folder
        if folder and Path(folder).is_dir():
            try:
                return read_show(Path(folder))
            except (ShowError, OSError) as exc:
                log_info(f"New show: the last show ({folder}) couldn't be read for its lights ({exc})")
        from ..new_show import show_from_patch

        return show_from_patch()

    def _show_loaded(self, show: Show) -> None:
        """Another show is loaded: the window follows (its files watched, its problems shown, it opens next time)."""
        if self.watcher is not None:
            self.watcher.start(show)
        self._load_problems = [f"inputs\\{input_id}.json couldn't be read: {error}" for input_id, error in show.input_errors.items()]
        self._load_problems += list(show.warnings) + self._global_problems
        self._update_files_chip()
        self.header.set_show_name(show.name, show.folder)
        if self._page == "editor" and not self.editor_page.input_id:
            self.show_page("dashboard")  # (the input being edited isn't in this show)
        else:
            self._update_undo_scope()
            if self._page == "editor" and self.isVisible():
                self.editor_page.on_enter()  # (its viewports draw this show's input now)
        if show.folder and not show.read_only:
            self.settings.show_folder = show.folder  # (live.json says it too: it opens next time)
            self._save_settings()
        self._guide_key = None
        if self._save_files:
            self._guide_timer.start()
            if show.folder and not show.read_only:
                self._backup_timer.start()
        self._follow_no_show(show)
        wanted = self.no_show_page if show.no_show and self._page != "automation" else self.pages.get(self._page)
        if wanted is not None and self.stack.currentWidget() is not wanted:
            self.show_page(self._page)  # (the card, or the tab itself again)

    def _follow_no_show(self, show: Show) -> None:
        """No show loaded, or a show loaded (M14): the header's controls that need one, and the card's lists."""
        self.header.set_no_show(show.no_show)
        if show.no_show:
            if not self.header.house_controls:  # (opened with no show: the show loaded last's house lights, greyed out)
                house = self._last_house_lights()
                if house:
                    self.header.set_house(house)
            self._refresh_card(force=True)

    def _last_house_lights(self) -> list[tuple[str, str]]:
        """No show loaded when CentraLight opens (M14): the Festoon and Key lights of the show loaded last, (fixture id,
        name), from its show.json, so the header shows them greyed out at the levels they come back at. [] if there's
        none, or it can't be read."""
        folder = self.settings.show_folder
        if not folder:
            return []
        try:
            data = read_json(Path(folder) / SHOW_FILE)
        except (OSError, ValueError):
            return []
        items = data.get("house") if isinstance(data, dict) else None
        if not isinstance(items, list):
            return []
        return [(item["fixture"], safe_text(str(item.get("label") or item["fixture"])))
                for item in items if isinstance(item, dict) and isinstance(item.get("fixture"), str) and item["fixture"]]

    def _refresh_card(self, frame: EngineFrame | None = None, force: bool = False) -> None:
        """No show loaded (M14): the card's shows, and whether the global automations will load one (every
        CARD_CHECK seconds, and at once when they're paused or resumed)."""
        frame = frame if frame is not None else self.last_frame
        paused = bool(frame.automations.paused) if frame is not None else False
        now = time.monotonic()
        if not force and paused == self._card_paused and now - self._card_checked < CARD_CHECK:
            return
        self._card_checked, self._card_paused = now, paused
        try:
            shows = self.list_shows()
        except OSError as exc:
            log_info(f"No show loaded: the shows couldn't be listed ({exc})", key="no-show-list")
            shows = []
        self.no_show_page.set_shows(shows)
        state, text = automations_line(self.global_document.show, shows, paused, datetime.now())
        self.no_show_page.set_automations(state, text)

    def _dialog_open(self) -> bool:
        """A dialog is waiting for an answer: a question, a name to type, a file to pick, or Windows' own (deleting a
        show). A show loading then would change what it's about."""
        return QApplication.activeModalWidget() is not None or self._native_dialogs > 0 or self._package_job is not None

    def _close_dialog(self, who: str) -> None:
        if self._package_job is not None:  # (a .clshow import or export: it's stopped, leaving nothing half done)
            if not self._package_job.get("stop"):
                self._package_job["stop"] = True
                log_info(f"{who}: the import or export under way was stopped, so the show it asked for could load")
            return
        widget = QApplication.activeModalWidget()
        if widget is None:
            return  # (Windows' own question, deleting a show: that's waited for)
        log_info(f"{who}: the dialog that was open (“{widget.windowTitle()}”) was closed, so the show it asked for "
                 "could load")
        if isinstance(widget, QDialog):
            widget.reject()
        else:
            widget.close()

    def _load_safely(self, who: str, action: Mapping[str, Any]) -> None:
        try:
            self._load_requested(who, action)
        except Exception as exc:  # (never let one request stop the rest)
            log_exception("loading a show", exc)
            if action.get("_scope") == "global":
                self.engine.submit(ShowLoadFailed(str(action.get("show", "")).strip()))

    def _load_requested(self, who: str, action: Mapping[str, Any]) -> None:
        """A global automation (a show id or name) or the Stream Deck (a folder) asks for a show (or, M14, for none: the
        lights have faded off, so the show closes)."""
        if action.get("do") == "close_show":
            self._close_now(who, automation=action.get("_scope") == "global")
            return
        wanted = str(action.get("folder") or action.get("show") or "").strip()
        is_global = action.get("_scope") == "global"
        # (the engine waits for a global automation's show: it's told if it can't load, by the name it asked for)
        self._asked_for = str(action.get("show", "")).strip() if is_global else None
        try:
            info = None
            if action.get("folder"):
                info = next((s for s in self.list_shows() if same_folder(s.folder, wanted)), None)
            if info is None:
                info = find_show(self.shows_root(), wanted)
            if info is None:
                log_info(f"{who}: there's no show called {wanted!r} to load")
                if self._asked_for is not None:
                    self.engine.submit(ShowLoadFailed(self._asked_for))
                return
            if same_folder(info.folder, self.document.show.folder):
                if self._asked_for is not None:  # (it's loaded already: the loaded show's automations carry on)
                    self.engine.submit(ShowLoadFailed(self._asked_for))
                return
            self.load_show(info.folder, who=who or "Automation", automation=is_global)
        finally:
            self._asked_for = None

    def ask_new_show(self) -> None:
        if self._locked:
            return
        if self.document.show.no_show:
            about = ("(It has the lights and layouts of the show loaded last, and “Follow screen”. It opens straight away; "
                     "the lights stay off until you send one of its inputs.)")
        else:
            about = ("(It has this show's lights and layouts, and “Follow screen”. It opens straight away; the lights hold "
                     "what they show until you send one of its inputs.)")
        name, ok = QInputDialog.getText(self, "New show", f"Name for the new show:\n\n{about}",
                                        text=unique_name(self.shows_root(), "New show"))
        if not ok or not name.strip():
            return
        self.document.flush()
        try:
            folder = new_show(self._new_show_template(), self.shows_root(), name)
        except (ShowError, OSError) as exc:
            log_exception("making a new show", exc)
            QMessageBox.warning(self, "New show", f"CentraLight couldn't make the show:\n{exc}")
            return
        self.load_show(str(folder))

    def ask_duplicate_show(self) -> None:
        if self._locked:
            return
        show = self.document.show
        if not show.folder:
            return
        name, ok = QInputDialog.getText(
            self, "Duplicate show", f"Name for the copy of “{show.name}”:\n\n(Everything in it is copied, media too. Its "
            "inputs keep their ids, so Stream Deck keys work in both. It doesn't open by itself: load it from the Show "
            "menu when you want it.)",
            text=unique_name(self.shows_root(), f"{show.name} copy"))
        if not ok or not name.strip() or not same_folder(self.document.show.folder, show.folder):
            return
        self.document.flush()
        self.document.wait_saved()
        try:
            folder = duplicate_show(Path(show.folder), self.shows_root(), name)
        except (ShowError, OSError) as exc:
            log_exception("copying the show", exc)
            QMessageBox.warning(self, "Duplicate show", f"CentraLight couldn't copy the show:\n{exc}")
            return
        if self._save_files:
            self._guide_timer.start()  # (the global automations' guide lists the shows)
        QMessageBox.information(self, "Duplicate show", f"Copied as “{read_show_name(folder)}”.\n\nLoad it from the "
                                "Show menu when you want it.")

    def ask_rename_show(self) -> None:
        if self._locked or self.document.read_only:
            return
        show = self.document.show
        name, ok = QInputDialog.getText(
            self, "Rename show", "New name for this show:\n\n(Its folder keeps its name, so Stream Deck keys that load it "
            "keep working.)", text=show.name)
        if not ok or not name.strip() or not same_folder(self.document.show.folder, show.folder):
            return
        show = self.document.show
        new_name = unique_name(self.shows_root(), name, ignore=show.folder)
        if new_name != show.name:
            self.document.apply(replace(show, name=new_name), f"Rename show {show.name} → {new_name}")

    def ask_delete_show(self, folder: str) -> None:
        if self._locked:
            return
        info = next((s for s in self.list_shows() if same_folder(s.folder, folder)), None)
        if info is None or same_folder(folder, self.document.show.folder):
            return
        answer = QMessageBox.question(
            self, "Delete show", f"Delete the show “{info.name}”?\n\nIts folder goes to the Recycle Bin, so you can "
            f"put it back from there.\n\n{info.folder}")
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._native_dialogs += 1  # (Windows may ask before deleting something too big for the Recycle Bin)
        try:
            delete_show(Path(folder), self.document.show.folder, owner=int(self.winId()))
        except (ShowError, OSError) as exc:
            log_exception("deleting a show", exc)
            QMessageBox.warning(self, "Delete show", f"CentraLight couldn't delete the show:\n{exc}")
            return
        finally:
            self._native_dialogs -= 1
        self.document.forget_show(folder)  # (its undo histories: a show made later in the same folder starts afresh)
        self.engine.submit(ForgetShow(folder))  # (and its variables)
        if self._save_files:
            self._guide_timer.start()  # (the global automations' guide lists the shows)
        if self.document.show.no_show:
            self._refresh_card(force=True)  # (no show loaded: the card's shows)

    def current_page(self) -> str:
        current = self.stack.currentWidget()
        if current is self.no_show_page:
            return self._page or "dashboard"
        return next((k for k, w in self.pages.items() if w is current), "dashboard")

    def set_locked(self, locked: bool) -> None:
        self._locked = locked
        self.header.set_locked(locked)
        self.dashboard.set_locked(locked)
        self.layout_page.setEnabled(not locked)
        self.editor_page.setEnabled(not locked)
        self.automation_page.setEnabled(not locked)
        self.no_show_page.setEnabled(not locked)
        self.header.show_picker.setEnabled(not locked)
        self.lock_banner.setVisible(locked)
        self.countdown_banner.set_locked(locked)

    def _show_name_for(self, wanted: str) -> str:
        """A show's name ("Evening") from what a "Load a show" action says (its id or name), for the countdown banner."""
        info = find_show(self.shows_root(), wanted, self.list_shows()) if wanted else None
        return info.name if info is not None else wanted

    @property
    def locked(self) -> bool:
        return self._locked

    def undo(self) -> None:
        if self._locked:
            return
        if self._page == "dashboard":  # no history on screen there: the newest step, whichever history it's in
            self.document.undo_newest()
        else:
            self._active_document().undo()

    def redo(self) -> None:
        if self._locked:
            return
        if self._page == "dashboard":
            self.document.redo_newest()
        else:
            self._active_document().redo()

    def _save_problem(self) -> str:
        """What couldn't be saved (the show's files, or the global automations'), or ""."""
        return self.document.problem or self.global_document.problem

    def _update_files_chip(self) -> None:
        problem = self._save_problem()
        if problem:
            self.header.set_chip("files", STATE_ERROR, "Not saved", problem)
        elif self._load_problems:
            listed = "\n".join(self._load_problems[:6]) + ("\n…" if len(self._load_problems) > 6 else "")
            self.header.set_chip("files", STATE_WARN, "File problems", f"When the show opened:\n{listed}\n\nClick for the full list.")
        else:
            self.header.set_chip("files", None)

    def _show_file_problems(self) -> None:
        text = self._save_problem() or ("When the show opened, CentraLight couldn't use everything in its files:\n\n"
                                         + "\n".join(f"• {p}" for p in self._load_problems)
                                         + "\n\nWhat it couldn't read was left out. Fix the file (or ask the AI that wrote it), "
                                         "and CentraLight picks it up by itself.")
        QMessageBox.information(self, "Files", text)
        if not self._save_problem():
            self._load_problems = []  # seen: the chip goes
            self._global_problems = []
            self._update_files_chip()

    def _show_changed(self, show: Show) -> None:
        set_trim = getattr(self.output, "set_trim", None)
        if set_trim is not None:  # a light added or moved past the last channel sent so far gets DMX at once
            set_trim(show_channels(show))  # (no show, M14: all 512 channels, at 0)
        self._update_hotkeys(show)
        if self._save_files and self._guide_inputs(show) != self._guide_key:
            self._guide_timer.start()  # the lights or globals changed: rewrite the AI guide soon
        self.dashboard.set_show(show)
        if not show.no_show:  # (no show, M14: the last show's house lights stay on the header, greyed out)
            self.header.set_house([(h.fixture, h.label) for h in show.house])
        self.header.set_show_name(show.name, show.folder)
        self.header.show_picker.notes = show.notes
        self._update_title(show)

    def _update_title(self, show: Show) -> None:
        if show.no_show:
            self.setWindowTitle("CentraLight — No show loaded")
            return
        suffix = " (read-only)" if show.read_only else ""
        self.setWindowTitle(f"CentraLight — {show.name}{suffix}")

    # ------------------------------------------------------------ engine

    def _house_changed(self, fixture: str, level: float | None, on: bool | None) -> None:
        self.engine.submit(SetHouse(fixture, level=level, on=on))

    def _on_frame(self, frame: EngineFrame) -> None:
        if frame.seq != self._last_seq:  # (a stuck engine sends no new frames: see _check_engine)
            self._last_seq = frame.seq
            self._last_new_frame = time.monotonic()
            if self._engine_stuck:
                self._engine_stuck = False
                self.header.set_chip("engine", None)
        self.last_frame = frame
        self._do_engine_requests()
        self._save_live_soon(frame)
        self.countdown_banner.set_view(frame.countdown, self._show_name_for)
        if self.tray is not None:
            self.tray.set_paused(frame.automations.paused)
            status = "No show loaded (the lights are off)" if self.document.show.no_show else f"Base: {frame.base}"
            self.tray.set_status(status + (" · automations paused" if frame.automations.paused else ""))
        if not self.isVisible():
            return  # hidden in the tray: nothing to draw
        self.header.set_frame(frame)
        self._update_ndi_chip(frame)
        self._update_audio_chip(frame)
        self.dashboard.set_frame(frame)
        self.editor_page.set_frame(frame)
        self.automation_page.set_frame(frame)
        self._update_automations_chip(frame)
        if self.document.show.no_show:
            self._refresh_card(frame)
        if self._page == "layout":
            self.layout_page.set_frame(frame)

    def _save_live_soon(self, frame: EngineFrame) -> None:
        if frame.live is not None and self._save_files:
            data = _live_key(frame.live)
            if data != self._saved_live:
                self._pending_live = frame.live
                if not self._live_timer.isActive():
                    self._live_timer.start(LIVE_SAVE_DELAY_MS)

    def _update_audio_chip(self, frame: EngineFrame) -> None:
        if frame.audio_state == "listening":
            level = round(float(frame.audio.get("level", 0.0)) * 100)
            self.header.set_chip("audio", STATE_OK, "Audio", f"Listening to {frame.audio_detail}\nLoudness now {level} %")
        elif frame.audio_state == "error":
            self.header.set_chip("audio", STATE_ERROR, "Audio problem", frame.audio_detail)
        else:
            self.header.set_chip("audio", None)

    def restart_audio(self) -> None:
        """Settings changed what to listen to (or how smoothly): listen again."""
        from ..audio import AudioCapture

        old = getattr(self.engine, "audio", None)
        if old is not None:
            old.stop()
        s = self.settings
        capture = AudioCapture(s.audio_source, s.audio_attack, s.audio_release)
        capture.start()
        self.engine.audio = capture

    def _update_ndi_chip(self, frame: EngineFrame) -> None:
        statuses = list(frame.ndi.values())
        if not statuses:
            self.header.set_chip("ndi", None)
            return
        order = {CONNECTED: 0, SEARCHING: 1, NO_SIGNAL: 2}
        worst = max(statuses, key=lambda s: order.get(s.state, 3))
        name = worst.found_name or worst.wanted
        if worst.state == CONNECTED:
            w, h = worst.size
            self.header.set_chip("ndi", STATE_OK, f"NDI · {worst.fps:.0f} fps", f"{name}\n{w}×{h}")
        elif worst.state == NO_SIGNAL:
            self.header.set_chip("ndi", STATE_WARN, "NDI · no signal", f"{name}\n{worst.message}")
        elif worst.state == SEARCHING:
            self.header.set_chip("ndi", STATE_WARN, "NDI · searching", worst.message or f"Looking for {worst.wanted}")
        else:
            self.header.set_chip("ndi", STATE_ERROR, "NDI error", worst.message)

    def _save_live_heartbeat(self) -> None:
        if self._shut_down:
            return  # (shutdown saves the last state itself: lights off, if it turned them off)
        if self.last_frame is not None and self.last_frame.live is not None and self._save_files:
            self._pending_live = self.last_frame.live
            self._save_live()

    def _save_live(self) -> None:
        state = self._pending_live
        if state is None:
            return
        try:
            state.save(self._live_path)
            self._saved_live = _live_key(state)
        except OSError as exc:
            log_exception("saving live.json", exc)

    def _backup(self) -> None:
        try:
            backup_show(Path(self.show_data.folder))
        except OSError as exc:
            log_exception("show backup", exc)

    # ------------------------------------------------------------ settings

    def open_settings(self) -> None:
        from ..audio import list_sources

        dialog = SettingsDialog(self.settings, self.show_data.folder, self, show=self.show_data, audio_sources=list_sources(),
                                audio_values=lambda: dict(self.last_frame.audio) if self.last_frame is not None else {},
                                shows=self.list_shows())
        result = dialog.exec()
        try:
            self._settings_closed(dialog, result)
        finally:
            dialog.deleteLater()  # (closed dialogs used to stay, hidden, with their audio meter's timer running)

    def _settings_closed(self, dialog: SettingsDialog, result: int) -> None:
        if dialog.preview_requested:
            self.open_preview()
            return
        if dialog.diagnostics_requested:
            self.open_diagnostics()
            return
        if result != QDialog.DialogCode.Accepted:
            return
        before = self._output_key()
        remote_before = self._remote_key()
        startup_before = self.settings.start_with_windows
        clshow_before = self.settings.clshow_files
        audio_before = (self.settings.audio_source, self.settings.audio_attack, self.settings.audio_release)
        dialog.apply_to(self.settings)
        self.engine.submit(SetCountdownLength(self.settings.countdown))
        self.engine.submit(SetDefaultShow(self.settings.default_show, self.settings.black_fade))
        self.automation_page.set_default_show(self.settings.default_show)
        if (self.settings.audio_source, self.settings.audio_attack, self.settings.audio_release) != audio_before:
            log_info(f"Audio source: {self.settings.audio_source or 'off'}")
            self.restart_audio()
        self.editor_page.timeline.ndi_default = self.settings.ndi_default
        if self._output_key() != before:
            log_info(f"Output changed to {self.settings.output}")
            self.output.set_spec(device_spec(self.settings, show_channels(self.show_data)))
        if self.settings.start_with_windows != startup_before and startup.managed():
            trouble = startup.sync(self.settings.start_with_windows)
            if trouble:
                QMessageBox.warning(self, "Start with Windows", trouble)
        if self.settings.clshow_files != clshow_before and startup.managed():
            from .. import filetype

            trouble = filetype.sync(self.settings.clshow_files)
            if trouble:
                QMessageBox.warning(self, "Open .clshow files", trouble)
        if self._remote_key() != remote_before and self.remote is not None:
            self.remote.port, self.remote.lan, self.remote.key = self._remote_key()
            self.remote.start()
            self._update_remote_chip()
        self._save_settings()

    def _check_engine(self) -> None:
        """Every second: has the engine stopped sending frames? (Its watchdog restarts a thread that died, but a
        tick stuck inside a call can't be replaced: the lights freeze, so the window says so.)"""
        running = getattr(self.engine, "is_running", None)
        if self._shut_down or not callable(running) or not running():
            return
        quiet = time.monotonic() - self._last_new_frame
        if quiet > ENGINE_STUCK_S and not self._engine_stuck:
            self._engine_stuck = True
            log_info(f"The engine hasn't sent a frame for {quiet:.0f} s")
            self.header.set_chip("engine", STATE_ERROR, "Not responding",
                                 "The lights engine has stopped updating, so the lights are frozen.\n"
                                 "If this doesn't clear in a few seconds, quit CentraLight (tray → Quit) and start it again.\n"
                                 "Diagnostics (Ctrl+Shift+D) has the details.")

    def frame_age(self) -> float:
        """Seconds since the engine's last new frame."""
        return time.monotonic() - self._last_new_frame

    def open_diagnostics(self):
        """How CentraLight is doing right now (not modal: it can stay open while you work)."""
        from .diagnostics import DiagnosticsDialog

        if getattr(self, "diagnostics", None) is not None and self.diagnostics.isVisible():
            self.diagnostics.raise_()
            self.diagnostics.activateWindow()
            return self.diagnostics
        self.diagnostics = DiagnosticsDialog(lambda: self.last_frame, self, age_provider=self.frame_age,
                                             timing_provider=getattr(self.engine, "tick_timing", None))
        self.diagnostics.show()
        return self.diagnostics

    def open_preview(self) -> None:
        preview = LookPreview(self)
        preview.exec()

    def _output_key(self) -> tuple:
        s = self.settings
        return (s.output, s.usb_port, s.full_universe, s.artnet_ip, s.artnet_universe)

    def _remote_key(self) -> tuple:
        return (self.settings.remote_port, self.settings.remote_lan, self.settings.remote_key)

    # ------------------------------------------------------------ AI

    @staticmethod
    def _guide_inputs(show: Show) -> tuple:
        return (show.name, show.fixtures, show.house, show.globals, show.folder, show.notes)

    def _write_guide(self) -> None:
        show = self.show_data
        self._guide_key = self._guide_inputs(show)
        try:
            written = write_guide_files(show)
            folder = self.global_document.show.folder
            # (and the global automations' own, with the shows' ids; not with no show loaded, M14: its house lights
            # are the ones the guide names, and it keeps the last show's)
            if folder and not self.global_document.read_only and not show.no_show:
                written += write_global_guide(Path(folder), [(s.id, s.name) for s in self.list_shows()],
                                              [h.label for h in show.house])
        except Exception as exc:
            log_exception("writing the AI guide", exc)
            return
        if written:
            log_info(f"AI guide updated: {', '.join(written)}")

    # ------------------------------------------------------------ .clshow files (M13)

    def export_package(self, everything: bool = False, target: str | None = None, media: bool | None = None):
        """Show ▾ → Export… (one show) or Export everything… (every show, the global automations and the settings).
        target / media: given by tests (no questions); otherwise asked. Returns the ExportReport, or None."""
        if self._locked or self._package_job is not None:
            return None
        show = self.document.show
        if not everything and not show.folder:
            self._message("Export", "This show isn't saved in a folder, so it can't be exported.", warn=True)
            return None
        if target is None:
            dialog = ExportDialog(self, show.name, everything)
            try:
                if dialog.exec() != QDialog.DialogCode.Accepted:
                    return None
                media = not dialog.leave_out.isChecked()
            finally:
                dialog.deleteLater()
            suggested = documents_dir() / export_file_name("CentraLight everything" if everything else show.name)
            chosen, _ = QFileDialog.getSaveFileName(self, "Export everything" if everything else f"Export “{show.name}”",
                                                    str(suggested), "CentraLight show (*.clshow)")
            if not chosen:
                return None
            target = chosen if chosen.lower().endswith(".clshow") else chosen + ".clshow"
        if original_problem(str(target)):  # (checked as text first: a name like COM3 is never opened)
            self._message("Export", f"{Path(str(target)).name} can't be saved there: {original_problem(str(target))}.", warn=True)
            return None
        media = True if media is None else media
        for document in (self.document, self.global_document):  # (the files as they are now, everything saved)
            document.flush()
            document.wait_saved(3.0)
        if self._save_files:
            self._save_settings()
        root = self.shows_root()
        try:
            if everything:
                report = self._run_job("Exporting everything…", lambda progress: export_everything(
                    root, target, media=media, settings_file=settings_path() if self._save_files else None, progress=progress))
            else:
                report = self._run_job(f"Exporting “{show.name}”…", lambda progress: export_show(
                    show.folder, target, media=media, progress=progress))
        except Stopped:
            log_info("Export stopped")
            return None
        except (PackageError, OSError) as exc:
            log_exception("exporting a .clshow file", exc)
            self._message("Export", f"It couldn't be exported: {exc}", warn=True)
            return None
        notes = "\n".join(f"• {line}" for line in report.skipped[:8])
        self._message("Export", f"Saved {Path(report.target).name} ({size_text(report.size)}) in {Path(report.target).parent}."
                      + (f"\n\nLeft out:\n{notes}" if notes else ""))
        return report

    def _run_job(self, title: str, work, can_cancel: bool = True):
        """Slow work for a .clshow file, on a thread of its own with a progress window. Show loads wait for it (as for an
        open dialog); one that's waited 30 s stops it (the work leaves nothing half done), and then loads."""
        state: dict = {}
        self._package_job = state
        try:
            return run_with_progress(self, title, work, can_cancel, state=state)
        finally:
            self._package_job = None

    def ask_import_package(self, path: str = "") -> bool:
        """Show ▾ → Import…, or a .clshow file double-clicked (passed on by the second-launch ping): what's in it first,
        then Import (a new show, not loaded; Replace or Keep both when the name is taken). True if anything came in."""
        self.bring_to_front()
        if self._locked:
            self._message("Import", "CentraLight is locked: click the padlock to unlock it, then import the file.", warn=True)
            return False
        if self._package_job is not None or self._importing:
            self._message("Import", "An import or export is under way: try again when it's done.", warn=True)
            return False
        if not path:
            path, _ = QFileDialog.getOpenFileName(self, "Import a show file", str(documents_dir()), "CentraLight show (*.clshow)")
            if not path:
                return False
        path = str(path)
        problem = original_problem(path) or ("" if path.lower().endswith(".clshow") else "it isn't a .clshow file")
        if problem:  # (checked as text first: a name like COM3, or \\.\..., is never opened)
            self._message("Import", f"{Path(path).name or path} can't be imported: {problem}.", warn=True)
            return False
        self._importing = True
        try:
            try:
                package = self._run_job(f"Checking {Path(path).name}…", lambda _progress: read_package(path), can_cancel=False)
            except (PackageError, OSError, Stopped) as exc:
                log_exception(f"reading {path}", exc)
                self._message("Import", f"{Path(path).name} can't be imported: {exc}", warn=True)
                return False
            for line in package.all_problems():
                log_info(f"Import {Path(path).name}: {line}")
            dialog = ImportPackageDialog(self, package, self.shows_root(), self.document.show.folder)
            try:
                if dialog.exec() != QDialog.DialogCode.Accepted:
                    return False
                ticked, replacing = dialog.ticked(), dialog.replacing
            finally:
                dialog.deleteLater()
            return self._import_ticked(package, ticked, replacing)
        finally:
            self._importing = False

    def _import_ticked(self, package, ticked: list, replacing: bool) -> bool:
        """Import what was ticked, one at a time: one that fails is reported, and the rest still come in."""
        root = self.shows_root()
        done: list[str] = []
        failed: list[str] = []
        notes: list[str] = []
        for number, (kind, index) in enumerate(ticked):
            what = (f"the show “{package.shows[index].name}”" if kind == "show" else
                    "the global automations" if kind == GLOBALS_ROW else "the settings")
            try:
                if kind == "show":
                    done.append(self._import_one_show(package, index, root, replacing, notes))
                elif kind == GLOBALS_ROW:
                    result = import_globals(package, global_folder(root))
                    done.append(f"{len(result.added)} global automation{'s' if len(result.added) != 1 else ''}"
                                + (f" ({len(result.kept)} already here kept as they were)" if result.kept else ""))
                    if self.global_watcher is not None:
                        self.global_watcher.check()
                elif kind == SETTINGS_ROW:
                    self._take_settings(import_settings(package, settings_path()) if self._save_files
                                        else package.settings.settings)
                    done.append("the settings")
            except Stopped:
                left = [t for t in ticked[number:]]
                failed.append(f"{what} and anything after it ({len(left)} in all): the import was stopped")
                break
            except (PackageError, OSError) as exc:
                log_exception(f"importing {what} from {package.path}", exc)
                failed.append(f"{what}: {exc}")
        if done:
            log_info(f"Imported from {package.path.name}: {', '.join(done)}")
            if self.document.show.no_show:
                self._refresh_card(force=True)  # (no show loaded: the card lists an imported show at once)
        text = ("Imported " + ", ".join(done) + ". An imported show isn't loaded: Show ▾ loads it.") if done else "Nothing was imported."
        if failed:
            text += "\n\nNot imported:\n" + "\n".join(f"• {line}" for line in failed)
        if notes:
            text += "\n\n" + "\n".join(f"• {line}" for line in notes)
        self._message("Import", text, warn=bool(failed))
        return bool(done)

    def _import_one_show(self, package, index: int, root: Path, replacing: bool, notes: list[str]) -> str:
        item = package.shows[index]
        loaded = self.document.show.folder  # (read here: show loads wait while this runs)
        name, replace_folder = item.name, None
        taken = name_taken(root, item.name)
        if taken is not None:
            if replacing and not same_folder(taken.folder, loaded):
                replace_folder = taken.folder
            else:
                name = keep_both_name(root, item.name)
        parked: list[Path] = []
        made = self._run_job(f"Importing “{name}”…", lambda progress: import_show(
            package, index, root, name=name, replace_folder=replace_folder, loaded=loaded, trash=parked.append,
            progress=progress, notes=notes))
        for old in parked:  # (the replaced show: to the Recycle Bin from here, so Windows' questions belong to this window)
            self._native_dialogs += 1
            try:
                to_recycle_bin(old, owner=int(self.winId()))
            except OSError as exc:
                log_exception(f"moving {old} to the Recycle Bin", exc)
                notes.append(f"The old show is still there as “{Path(old).name}” (it couldn't go to the Recycle Bin: {exc}).")
            finally:
                self._native_dialogs -= 1
        new_id = show_id(made) if made else ""
        if item.id and new_id and new_id != item.id:
            notes.append(f"“{name}” has the id {new_id} here ({item.id} on the PC it came from): Stream Deck keys and global "
                         "automations that load it by its old id need changing.")
        return f"the show “{name}”" + (" (replacing the old one)" if replace_folder else "")

    def _take_settings(self, imported: Settings) -> None:
        """Settings from an Export everything file, for the running CentraLight too (this PC keeps its own output, port,
        start with Windows, phone page, audio source, folders and window: see clshow.KEEP_ON_THIS_PC)."""
        from dataclasses import fields

        from ..clshow import KEEP_ON_THIS_PC

        audio_before = (self.settings.audio_source, self.settings.audio_attack, self.settings.audio_release)
        for item in fields(Settings):
            if item.name not in KEEP_ON_THIS_PC:
                setattr(self.settings, item.name, getattr(imported, item.name))
        self.engine.submit(SetCountdownLength(self.settings.countdown))
        self.engine.submit(SetDefaultShow(self.settings.default_show, self.settings.black_fade))
        self.automation_page.set_default_show(self.settings.default_show)
        self.editor_page.timeline.ndi_default = self.settings.ndi_default
        if (self.settings.audio_source, self.settings.audio_attack, self.settings.audio_release) != audio_before:
            self.restart_audio()
        if self._save_files:
            self._save_settings()

    def _message(self, title: str, text: str, warn: bool = False) -> None:
        """A message that holds nothing up (not even a show an automation loads): not modal, and plain text (names in it
        come from files: never read as HTML)."""
        box = QMessageBox(QMessageBox.Icon.Warning if warn else QMessageBox.Icon.Information, title, text,
                          QMessageBox.StandardButton.Ok, self)
        box.setTextFormat(Qt.TextFormat.PlainText)
        box.setWindowModality(Qt.WindowModality.NonModal)
        box.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.last_message = box
        box.show()

    def edit_notes(self) -> NotesDialog | None:
        """Notes… : a few lines about the loaded show (what it's for). One undo step."""
        if self._locked:
            return None
        if self.document.read_only:
            self._message("Notes", "This show is read-only (it was saved by a newer CentraLight), so its notes can't be "
                          "changed.", warn=True)
            return None
        dialog = NotesDialog(self, self.document.show.name, self.document.show.notes)
        self.notes_dialog = dialog

        def finished(result: int, folder: str = self.document.show.folder) -> None:
            dialog.deleteLater()
            if result != QDialog.DialogCode.Accepted or not same_folder(folder, self.document.show.folder):
                return
            show = self.document.show
            notes = clean_notes(dialog.text())
            if notes != show.notes:
                self.document.apply(replace(show, notes=notes), "Show notes" if notes else "Clear the show notes")

        dialog.finished.connect(finished)
        dialog.open()
        return dialog

    def import_from_show(self, kind: str = INPUTS) -> ImportItemsDialog | None:
        """Import from another show… (inputs, or show automations): pick, read the summary, Import. One undo step;
        the other show isn't loaded and nothing is sent to the lights."""
        if self._locked:
            return None
        if self.document.read_only:
            self._message("Import from another show", "This show is read-only (it was saved by a newer CentraLight), so "
                          "nothing can be imported into it.", warn=True)
            return None
        shows = [info for info in self.list_shows() if not same_folder(info.folder, self.document.show.folder)]
        if not shows:
            QMessageBox.information(self, "Import from another show", "There are no other shows to import from yet.")
            return None
        live = self.last_frame.in_use if self.last_frame is not None else frozenset()
        dialog = ImportItemsDialog(self, shows, self.document.show, kind, live, self.linked_media.allowed)
        self.import_dialog = dialog
        dialog.finished.connect(lambda result, d=dialog, f=self.document.show.folder: self._import_finished(d, result, f))
        dialog.open()
        return dialog

    def _import_finished(self, dialog: ImportItemsDialog, result: int, folder: str) -> bool:
        """Import pressed: copy the files the inputs use, then make the change (one undo step)."""
        plan, other = dialog.plan, dialog.other
        keep_both, kind, links = dialog.keeping_both, dialog.kind, dialog.links()  # (read now: the dialog goes)
        dialog.deleteLater()
        if result != QDialog.DialogCode.Accepted or plan is None or other is None or not (plan.inputs or plan.automations):
            return False
        if not same_folder(folder, self.document.show.folder):
            log_info("Import from another show: another show was loaded meanwhile, so nothing was imported")
            return False
        show = self.document.show
        try:  # (on a thread of its own, with a progress window: big videos take a while, and the window keeps answering)
            renamed = self._run_job("Copying pictures and videos…", lambda _progress: copy_files(plan, show, other),
                                    can_cancel=False)
        except (OSError, Stopped) as exc:
            log_exception("copying files for an import", exc)
            self._message("Import from another show", f"The pictures and videos couldn't be copied: {exc}", warn=True)
            return False
        if not same_folder(folder, self.document.show.folder):
            return False
        show = self.document.show
        self.linked_media.allowed.allow(links)  # (the summary listed them: importing is the OK)
        count = len(plan.inputs) if kind == INPUTS else len(plan.automations)
        what = ("input" if kind == INPUTS else "automation") + ("s" if count != 1 else "")
        after = apply_import(show, other, plan, keep_both, renamed)
        quiet = imported_automation_ids(show, after)
        if quiet:  # (told first, so the engine never checks them before it knows: nothing runs just because it was imported)
            self.engine.submit(QuietAutomations(quiet))
        done = self.document.apply(after, f"Import {count} {what} from {other.name}")
        if done:
            log_info(f"Imported {count} {what} from {other.name}: {', '.join(plan.inputs + plan.automations)}")
        return done

    def open_ai_dialog(self, input_id: str = "", reply: str = "") -> AIDialog | None:
        """Create (input_id "") or edit an input with any AI chat."""
        if self._locked or self.document.read_only:
            return None
        variables = self.last_frame.variables if self.last_frame is not None else {}
        self.ai_dialog = AIDialog(self.document, self.engine, input_id, reply, variables,
                                  getattr(self.engine, "sources", None), self, linked=self.linked_media)
        self.ai_dialog.open()
        return self.ai_dialog

    def paste_input(self) -> AIDialog | None:
        """Ctrl+V on the Dashboard: if the clipboard holds an input (e.g. an AI's reply), check it and offer to add it."""
        text = QGuiApplication.clipboard().text()
        try:
            data = parse_reply(text)
        except AIReplyError:
            return None
        if not isinstance(data.get("tracks"), list):
            return None  # some other JSON, not an input
        return self.open_ai_dialog("", text)

    def _ask_reload(self, name: str) -> bool:
        answer = QMessageBox.question(
            self, "Changed outside CentraLight",
            f"“{name}” was just changed outside CentraLight (by an AI tool or a text editor), but your latest change "
            f"here isn't saved yet.\n\nLoad the changed file? (No keeps yours, and saves it over the file.)",
        )
        return answer == QMessageBox.StandardButton.Yes

    # ------------------------------------------------------------ automations

    def _update_automations_chip(self, frame: EngineFrame) -> None:
        show = self.show_data
        globals_ = self.global_document.show
        if not show.automations and not globals_.automations:
            self.header.set_chip("automations", None)
            return
        view = frame.automations
        if view.paused:
            self.header.set_chip("automations", STATE_WARN, "Automations paused",
                                 "Nothing runs by itself (global automations included) until you resume.\nClick to resume.")
            return
        now = time.monotonic()
        if now - self._next_run_checked > 30:  # working out the next run is quick, but not every frame
            self._next_run_checked = now
            found = sorted(upcoming(show, datetime.now(), days=7, limit=1) + upcoming(globals_, datetime.now(), days=7, limit=1),
                           key=lambda item: item[0])
            self._next_run_text = f"Next: {found[0][0]:%a %H:%M} {found[0][1].name}" if found else "Nothing timed in the next 7 days"
        tripped = [show.automations[aid].name for aid, s in view.statuses.items() if s.tripped and aid in show.automations]
        tripped += [globals_.automations[aid].name for aid, s in frame.global_automations.statuses.items()
                    if s.tripped and aid in globals_.automations]
        if tripped:
            self.header.set_chip("automations", STATE_ERROR, "Automations: check",
                                 f"Paused by the loop guard: {', '.join(tripped)}.\nSee the Automation tab.")
        else:
            self.header.set_chip("automations", STATE_OK, "Automations on", f"{self._next_run_text}\nClick to pause them all.")

    def _do_engine_requests(self) -> None:
        """Things automations or the Stream Deck asked for that change the show (the window does those). A show asked
        for while a dialog is open (a question, a name to type, a file to pick) loads when it closes: what the dialog
        was about stays in its own show. Only the newest such request counts, and if the dialog is still open after
        LOAD_HOLD seconds, it's closed (as if Cancel was pressed): automations win."""
        dialog_open = self._dialog_open()
        if self._waiting_load is not None:
            who, action, since = self._waiting_load
            if not dialog_open:
                self._waiting_load = None
                self._load_safely(who, action)
            elif time.monotonic() - since >= LOAD_HOLD:
                # automations win: the dialog is closed (as if Cancel was pressed), and the show loads at the next
                # frame, once nothing is left waiting for the dialog's answer
                self._close_dialog(who)
        requests = getattr(self.engine, "gui_requests", None)
        while requests is not None:
            try:
                who, action = requests.get_nowait()
            except queue.Empty:
                return
            if action.get("do") in ("load_show", "close_show"):  # (closing it too, for no show: M14)
                if action.get("do") == "load_show" and action.get("_scope") == "global":  # (the engine waits for this show: it's here, and may be held)
                    self.engine.submit(ShowLoadWaiting(str(action.get("show", "")).strip(), LOAD_HOLD + 10.0))
                if dialog_open:
                    happens = "closes" if action.get("do") == "close_show" else "loads"
                    log_info(f"{who}: the show {happens} when the dialog that's open is closed", key="load-waits-for-dialog")
                    since = self._waiting_load[2] if self._waiting_load is not None else time.monotonic()
                    self._waiting_load = (who, action, since)  # (the newest request is the one that counts)
                else:
                    self._load_safely(who, action)
                continue
            document = self.global_document if action.get("_scope") == "global" else self.document
            show = document.show
            if show.read_only:
                continue
            if action.get("do") == "layout" and document is self.document:
                layout = show.layout(str(action.get("layout", "")))
                if layout is not None and layout.id != show.active_layout:
                    self.document.apply(switch_layout(show, layout.id), f"{who}: switch to layout {layout.name}")
            elif action.get("do") == "automation":
                automation = show.automations.get(str(action.get("automation", "")))
                enabled = bool(action.get("enabled", True))
                if automation is not None and automation.enabled != enabled:
                    document.apply(update_automation(show, automation.id, enabled=enabled),
                                   f"{who}: turn “{automation.name}” {'on' if enabled else 'off'}")

    # ------------------------------------------------------------ the Stream Deck remote

    def _update_remote_chip(self) -> None:
        remote = self.remote
        if remote is None:
            self.header.set_chip("remote", None)
        elif remote.running:
            where = "this PC, and other computers with the key" if remote.lan else "this PC"
            self.header.set_chip("remote", STATE_INFO, f"Remote :{remote.port}",
                                 f"Stream Deck remote for {where}: {remote.base_url()}/\nClick to open the remote page")
        else:
            self.header.set_chip("remote", STATE_ERROR, "Remote off",
                                 f"The Stream Deck remote couldn't start: {remote.error}.\nChoose another port in Settings → Remote.")

    def open_remote_page(self) -> None:
        if self.remote is not None and self.remote.running:
            QDesktopServices.openUrl(QUrl(f"{self.remote.base_url()}/"))

    # ------------------------------------------------------------ input hotkeys

    def _update_hotkeys(self, show: Show) -> None:
        """Each input's hotkey sends it to Base (while this window is in front)."""
        wanted: dict[str, str] = {}
        for inp in show.ordered_inputs():
            text = QKeySequence(inp.hotkey).toString() if inp.hotkey else ""
            if text and text not in RESERVED_KEYS and text not in wanted:
                wanted[text] = inp.id
        if wanted == self._hotkey_map:
            return
        for shortcut in self._hotkeys:
            shortcut.setEnabled(False)
            shortcut.deleteLater()
        self._hotkeys = []
        self._hotkey_map = wanted
        for text, input_id in wanted.items():
            shortcut = QShortcut(QKeySequence(text), self)
            shortcut.activated.connect(lambda i=input_id: self.fire_hotkey(i))
            self._hotkeys.append(shortcut)

    def fire_hotkey(self, input_id: str) -> None:
        if not self._locked and input_id in self.show_data.inputs:
            self.engine.submit(SendToBase(input_id))

    def _save_settings(self) -> None:
        if not self._save_files:
            return
        try:
            self.settings.save()
        except OSError as exc:
            log_exception("saving settings", exc)

    # ------------------------------------------------------------ window size

    def _apply_minimum_size(self) -> None:
        screen = QGuiApplication.primaryScreen()
        available = screen.availableGeometry() if screen is not None else QRect(0, 0, MIN_WIDTH, MIN_HEIGHT)
        self.setMinimumSize(min(MIN_WIDTH, available.width()), min(MIN_HEIGHT, available.height()))

    def _restore_geometry(self) -> None:
        geometry = self.settings.geometry
        if geometry:
            rect = QRect(*geometry)
            if any(screen.availableGeometry().intersects(rect) for screen in QGuiApplication.screens()):
                self.setGeometry(rect)
                return
        screen = QGuiApplication.primaryScreen()
        if screen is not None:
            area = screen.availableGeometry()
            width = max(self.minimumWidth(), min(1600, area.width() - 80))
            height = max(self.minimumHeight(), min(900, area.height() - 80))
            self.resize(width, height)
            self.move(area.center().x() - width // 2, area.center().y() - height // 2)

    def changeEvent(self, event) -> None:  # noqa: N802
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange:
            self.bridge.set_slow(self.isMinimized())
            self._update_preview_wanted()
            if self.isMinimized():
                self._leave_live_modes()
            elif self.isVisible():
                self._back_in_sight()

    def _leave_live_modes(self) -> None:
        """The window is going out of sight (into the tray, or minimised): Identify, test colours and the Editor's
        Show on lights switch off. Otherwise the lights would stay on them, and nothing sent to Base (automations,
        the Stream Deck, hotkeys) would show until someone opened the window again."""
        self.layout_page.on_leave()
        self.editor_page.on_leave()
        dialog = getattr(self, "ai_dialog", None)
        if dialog is not None:
            try:
                if dialog.solo_button.isChecked():
                    dialog.solo_button.setChecked(False)  # (its "Try it on the lights" ends too)
            except RuntimeError:  # closed and gone
                pass

    def _back_in_sight(self) -> None:
        self._update_preview_wanted()
        if self._page == "editor":
            self.editor_page.on_enter()  # its viewports draw again

    def _update_preview_wanted(self) -> None:
        """The engine draws the live picture only while the Dashboard is on screen (the lights never need it)."""
        wanted = self._page == "dashboard" and self.isVisible() and not self.isMinimized() and not self._shut_down
        setter = getattr(self.engine, "set_preview_wanted", None)
        if setter is not None:
            setter(wanted)

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self._back_in_sight()
        if not self._splits_applied:
            self._splits_applied = True
            QTimer.singleShot(0, self._apply_splits)
        self.dashboard.set_active(True)
        if self.last_frame is not None:  # catch up with what happened while hidden (in the tray)
            QTimer.singleShot(0, lambda: self.last_frame is not None and self._on_frame(self.last_frame))

    def _apply_splits(self) -> None:
        self.dashboard.apply_split(self.settings.splitters.get("dashboard"))
        self.layout_page.apply_split(self.settings.splitters.get("layout"))
        self.editor_page.apply_split(self.settings.splitters.get("editor"))
        self.automation_page.apply_split(self.settings.splitters.get("automation"))

    def _remember_splits(self) -> None:
        if self._splits_applied:
            self.settings.splitters["dashboard"] = self.dashboard.splits()
            self.settings.splitters["layout"] = self.layout_page.splits()
            self.settings.splitters["editor"] = self.editor_page.splits()
            self.settings.splitters["automation"] = self.automation_page.splits()

    # ------------------------------------------------------------ quitting

    # ------------------------------------------------------------ the tray

    def attach_tray(self, tray) -> None:
        self.tray = tray
        tray.show_requested.connect(self.bring_to_front)
        tray.quit_requested.connect(self.quit_from_tray)
        tray.pause_toggled.connect(lambda paused: self.engine.submit(SetAutomationsPaused(paused)))
        tray.diagnostics_requested.connect(lambda: (self.bring_to_front(), self.open_diagnostics()))
        tray.show()

    def bring_to_front(self) -> None:
        if self.isMinimized():
            self.showNormal()
        elif self.settings.maximized and not self.isVisible():
            self.showMaximized()
        else:
            self.show()
        self.raise_()
        self.activateWindow()

    def quit_from_tray(self) -> bool:
        """Quit, after asking (the tray menu's Quit). True if it quit."""
        parent = self if self.isVisible() else None
        answer = QMessageBox.question(
            parent, "Quit CentraLight",
            "Quit CentraLight?\n\nThe lights stop following automations, and the Stream Deck stops working, "
            "until CentraLight starts again.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            log_info("Quit from the tray menu: cancelled (CentraLight carries on)")
            return False
        self._quitting = True
        if not self._quit_flow("from the tray menu"):
            self._quitting = False
            return False
        return True

    def hideEvent(self, event) -> None:  # noqa: N802
        super().hideEvent(event)
        self.dashboard.set_active(False)  # no drawing while hidden
        self._update_preview_wanted()
        self._leave_live_modes()

    def closeEvent(self, event) -> None:  # noqa: N802
        if self._shut_down:
            event.accept()
            return
        if self.tray is not None and not self._quitting:
            self._remember_geometry()
            self.hide()  # keep running in the tray
            self.tray.tell_about_hiding()
            event.ignore()
            return
        if self._quit_flow():
            event.accept()
        else:
            event.ignore()

    def _quit_flow(self, how: str = "by closing the window") -> bool:
        """Turn the lights off or leave them (asking if the setting says so), then stop. True if it quit. Each quit is
        written in the session log first, with the choice (M14: so a quit can be told from a crash)."""
        choice = self.settings.quit_action
        why = "Settings say so"
        if choice == QUIT_ASK:
            dialog = QuitDialog(self if self.isVisible() else None)
            if dialog.exec() != QDialog.DialogCode.Accepted or dialog.choice is None:
                log_info(f"Quit {how}: cancelled (CentraLight carries on)")
                return False
            choice = dialog.choice
            why = "chosen when asked" + (", and remembered" if dialog.remember.isChecked() else "")
            if dialog.remember.isChecked():
                self.settings.quit_action = choice
        log_info(f"Quit {how}: {QUIT_WORDS.get(choice, choice)} ({why})")
        self.shutdown(lights_off=(choice == QUIT_OFF))
        if self.tray is not None:
            self.tray.hide()
        QTimer.singleShot(0, QApplication.quit)
        return True

    def quit_without_asking(self, why: str) -> None:
        """Windows logging off or shutting down: stop cleanly without asking, as Settings say (turning the lights off
        only if they say so). Written in the session log first, as every quit is."""
        if self._shut_down:
            return  # (a quit that's under way already: it was written then)
        lights_off = self.settings.quit_action == QUIT_OFF
        reason = "there's no time to ask" if self.settings.quit_action == QUIT_ASK else "Settings say so"
        log_info(f"Quit, {why}: {QUIT_WORDS[QUIT_OFF] if lights_off else QUIT_WORDS[QUIT_LEAVE]} ({reason})")
        self.shutdown(lights_off=lights_off)

    def shutdown(self, lights_off: bool) -> None:
        """Stop the engine and the output, and save where everything was."""
        if self._shut_down:
            return
        self._shut_down = True
        self.bridge.stop()
        self._live_timer.stop()
        self._live_heartbeat.stop()
        self._backup_timer.stop()
        self.layout_page.on_leave()
        self.editor_page.shutdown()
        for watcher in (self.watcher, self.global_watcher):
            if watcher is not None:
                watcher.stop()
        self._guide_timer.stop()
        if self.remote is not None:
            self.remote.stop()
        state = self.engine.stop(lights_off=lights_off)
        self.output.close(blackout=lights_off)
        close_sources = getattr(getattr(self.engine, "sources", None), "close", None)
        if close_sources is not None:
            close_sources()
        videos.close()  # stop the video readers
        audio = getattr(self.engine, "audio", None)
        if audio is not None:
            audio.stop()
        self.linked_media.close()
        self.document.close()
        self.global_document.close()
        log_info(f"CentraLight closed ({'lights off' if lights_off else 'lights left as they were'})")
        if self._save_files:
            try:
                state.save(self._live_path)
            except OSError as exc:
                log_exception("saving live.json", exc)
            self._remember_geometry()
            self._save_settings()

    def _remember_geometry(self) -> None:
        self._remember_splits()
        self.settings.maximized = self.isMaximized()
        if not self.isMaximized() and not self.isMinimized():
            g = self.geometry()
            self.settings.geometry = [g.x(), g.y(), g.width(), g.height()]

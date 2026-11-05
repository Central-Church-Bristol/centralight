"""Settings: where the DMX goes, the Stream Deck remote, what Quit does, and where the files are."""

from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..model import NO_DEFAULT_SHOW
from ..paths import app_data_dir
from ..remote import new_key
from ..settings import (
    OUTPUT_ARTNET,
    OUTPUT_SIMULATED,
    OUTPUT_USB,
    QUIT_ASK,
    QUIT_LEAVE,
    QUIT_OFF,
    Settings,
)
from .widgets import ElidedLabel

QUIT_LABELS = (
    (QUIT_ASK, "Ask each time"),
    (QUIT_OFF, "Turn the lights off"),
    (QUIT_LEAVE, "Leave the lights as they are"),
)


def list_com_ports() -> list[tuple[str, str]]:
    """(device, description) for every COM port. Only lists them; nothing is opened."""
    try:
        from serial.tools import list_ports
    except ImportError:
        return []
    ports = []
    for port in list_ports.comports():
        description = port.description or ""
        if port.vid == 0x0403 and port.pid == 0x6001:
            description = f"{description} (looks like the Eurolite)"
        ports.append((port.device, description))
    return sorted(ports)


def hint(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("Hint")
    label.setWordWrap(True)
    return label


class SettingsDialog(QDialog):
    def __init__(
        self,
        settings: Settings,
        show_folder: str,
        parent: QWidget | None = None,
        ports: list[tuple[str, str]] | None = None,
        audio_sources: list[tuple[str, str]] | None = None,
        audio_values=None,
        show=None,
        shows: list | None = None,
    ) -> None:
        super().__init__(parent)
        self._show = show
        self.setWindowTitle("Settings")
        self.setModal(True)
        self.setMinimumWidth(560)
        self._show_folder = show_folder
        self.preview_requested = False
        self.diagnostics_requested = False

        # --- output
        output_box = QGroupBox("Output: where the DMX goes")
        self.output_group = QButtonGroup(self)
        self.simulated = QRadioButton("Simulated: send nothing, just show what would be sent")
        self.usb = QRadioButton("Eurolite USB-DMX512 PRO (the church's dongle)")
        self.artnet = QRadioButton("Art-Net (to MagicQ or an Art-Net node)")
        for value, button in ((OUTPUT_SIMULATED, self.simulated), (OUTPUT_USB, self.usb), (OUTPUT_ARTNET, self.artnet)):
            button.setProperty("value", value)
            self.output_group.addButton(button)
        {OUTPUT_SIMULATED: self.simulated, OUTPUT_USB: self.usb, OUTPUT_ARTNET: self.artnet}[settings.output].setChecked(True)

        self.port = QComboBox()
        self.port.addItem("Find it automatically", "")
        for device, description in ports if ports is not None else list_com_ports():
            self.port.addItem(f"{device} — {description}" if description else device, device)
        index = self.port.findData(settings.usb_port)
        if index < 0 and settings.usb_port:
            self.port.addItem(f"{settings.usb_port} (not plugged in)", settings.usb_port)
            index = self.port.count() - 1
        self.port.setCurrentIndex(max(0, index))
        self.full_universe = QCheckBox("Send all 512 channels")
        self.full_universe.setChecked(settings.full_universe)
        self.full_universe.setToolTip(
            "Normally CentraLight stops after the last light's channel (76), which is faster.\n"
            "Tick this only if the lights behave oddly."
        )
        self.artnet_ip = QLineEdit(settings.artnet_ip)
        self.artnet_universe = QSpinBox()
        self.artnet_universe.setRange(0, 32767)
        self.artnet_universe.setValue(settings.artnet_universe)

        usb_form = QFormLayout()
        usb_form.setContentsMargins(28, 0, 0, 0)
        usb_form.addRow("COM port", self.port)
        usb_form.addRow("", self.full_universe)
        artnet_form = QFormLayout()
        artnet_form.setContentsMargins(28, 0, 0, 0)
        artnet_form.addRow("Target IP", self.artnet_ip)
        artnet_form.addRow("Universe", self.artnet_universe)

        output_layout = QVBoxLayout(output_box)
        output_layout.addWidget(self.simulated)
        output_layout.addWidget(self.usb)
        output_layout.addLayout(usb_form)
        output_layout.addWidget(self.artnet)
        output_layout.addLayout(artnet_form)
        output_layout.addWidget(
            hint(
                "Only one program can use the Eurolite at a time: close MagicQ, QLC+ and the console "
                "scripts first. Leaving the Eurolite (choosing Simulated or Art-Net) turns its lights off."
            )
        )
        self.output_group.buttonToggled.connect(lambda *_: self._sync_enabled())

        # --- the screen's NDI feed: what new NDI elements (and Follow screen in a new show) follow
        ndi_box = QGroupBox("Screen feed (NDI)")
        ndi_form = QFormLayout(ndi_box)
        self.ndi_default = QLineEdit(settings.ndi_default)
        self.ndi_default.setToolTip("Any NDI source whose name contains this. New NDI elements start with it.")
        ndi_form.addRow("Feed for new NDI elements", self.ndi_default)

        # --- the remote (Stream Deck, phones)
        remote_box = QGroupBox("Remote: Stream Deck and phones")
        self.remote_port = QSpinBox()
        self.remote_port.setRange(1024, 65535)
        self.remote_port.setValue(settings.remote_port)
        self.remote_lan = QCheckBox("Allow other computers and phones on the network")
        self.remote_lan.setChecked(settings.remote_lan)
        self.remote_lan.setToolTip("They need the key below. Windows asks once whether to let CentraLight through its firewall.")
        self.remote_key = QLineEdit(settings.remote_key or new_key())
        self.remote_key.setReadOnly(True)
        self.remote_key.setToolTip("Other computers add ?key=… to every URL. The Stream Deck on this PC doesn't need it.")
        new_key_button = QPushButton("New key")
        new_key_button.setToolTip("Make a new key (phones and other computers then need the new one)")
        new_key_button.clicked.connect(lambda: self.remote_key.setText(new_key()))
        key_row = QHBoxLayout()
        key_row.addWidget(self.remote_key, 1)
        key_row.addWidget(new_key_button)
        open_remote = QPushButton("Open the remote page")
        open_remote.setToolTip("Every Stream Deck URL, and buttons for each input")
        open_remote.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(f"http://127.0.0.1:{self.remote_port.value()}/")))
        remote_form = QFormLayout()
        remote_form.addRow("Port", self.remote_port)
        remote_form.addRow("", self.remote_lan)
        remote_form.addRow("Key", key_row)
        remote_layout = QVBoxLayout(remote_box)
        remote_layout.addLayout(remote_form)
        remote_layout.addWidget(hint(
            "In the Stream Deck app, add a Website action with a URL like http://127.0.0.1:8746/api/input/warm-white "
            "and tick “GET request in background”. The remote page lists every URL."
        ))
        remote_buttons = QHBoxLayout()
        remote_buttons.addWidget(open_remote)
        remote_buttons.addStretch(1)
        remote_layout.addLayout(remote_buttons)

        # --- starting with Windows
        self.start_with_windows = QCheckBox("Start CentraLight when I sign in to Windows (in the tray, by the clock)")
        self.start_with_windows.setChecked(settings.start_with_windows)
        self.start_with_windows.setToolTip("Then the lights and automations look after themselves after a restart")
        self.clshow_files = QCheckBox("Open .clshow files with CentraLight (double-click one to import it)")
        self.clshow_files.setChecked(settings.clshow_files)
        self.clshow_files.setToolTip("Show ▾ → Export… makes .clshow files. For this Windows user only (no administrator).")

        # --- quitting
        # --- audio: what audio.level and friends listen to
        audio_box = QGroupBox("Audio: sound for audio.* in expressions")
        audio_form = QFormLayout(audio_box)
        self.audio_source = QComboBox()
        for value, label in (audio_sources or [("", "Off"), ("output", "This PC's output (what it's playing)")]):
            self.audio_source.addItem(label, value)
        index = self.audio_source.findData(settings.audio_source)
        if index < 0 and settings.audio_source:  # a device that isn't plugged in now: keep it
            self.audio_source.addItem(f"{settings.audio_source} (not found)", settings.audio_source)
            index = self.audio_source.count() - 1
        self.audio_source.setCurrentIndex(max(0, index))
        self.audio_source.setToolTip("This PC's output follows whatever it plays (nothing to plug in); or pick an input, such as a mixer feed")
        audio_form.addRow("Listen to", self.audio_source)
        self.audio_attack = QDoubleSpinBox()
        self.audio_attack.setRange(0.0, 2.0)
        self.audio_attack.setSingleStep(0.05)
        self.audio_attack.setSuffix(" s")
        self.audio_attack.setValue(settings.audio_attack)
        self.audio_attack.setToolTip("How quickly the values rise")
        self.audio_release = QDoubleSpinBox()
        self.audio_release.setRange(0.0, 3.0)
        self.audio_release.setSingleStep(0.05)
        self.audio_release.setSuffix(" s")
        self.audio_release.setValue(settings.audio_release)
        self.audio_release.setToolTip("How slowly they fall back")
        smoothing = QHBoxLayout()
        smoothing.addWidget(QLabel("Rise"))
        smoothing.addWidget(self.audio_attack)
        smoothing.addWidget(QLabel("Fall"))
        smoothing.addWidget(self.audio_release)
        smoothing.addStretch(1)
        audio_form.addRow("Smoothing", smoothing)
        self.audio_meter = AudioMeter()
        audio_form.addRow("Now", self.audio_meter)
        self._audio_values = audio_values or (lambda: {})
        self._meter_timer = QTimer(self)
        self._meter_timer.setInterval(100)
        self._meter_timer.timeout.connect(lambda: self.audio_meter.set_values(self._audio_values()))
        self._meter_timer.start()

        black_box = QGroupBox("Cut / Fade to black")
        black_form = QFormLayout(black_box)
        self.black_fade = QDoubleSpinBox()
        self.black_fade.setRange(0.1, 60.0)
        self.black_fade.setSingleStep(0.5)
        self.black_fade.setDecimals(1)
        self.black_fade.setSuffix(" s")
        self.black_fade.setValue(settings.black_fade)
        black_form.addRow("Fade to black takes", self.black_fade)
        black_form.addRow(hint("The header's buttons: a click sends the colour lights to black; holding one sends "
                               "everything, the Festoon and Key lights too. The next input lights things again. "
                               "The Stream Deck's /api/black/fade takes this time too, unless its URL gives ?seconds=."))
        self.countdown = QSpinBox()
        self.countdown.setRange(0, 60)
        self.countdown.setSuffix(" s")
        self.countdown.setSpecialValueText("None")
        self.countdown.setValue(settings.countdown)
        black_form.addRow("Countdown first", self.countdown)
        black_form.addRow(hint("Before an automation loads a show, or cuts or fades to black, a banner counts down (on the "
                               "window and the phone page): Load now, Wait 10 min or Skip. A change that's on a schedule "
                               "still lands on time: its banner starts early. Not for Run now, or just after CentraLight "
                               "starts. 0 = no countdown."))

        quit_box = QGroupBox("Starting and quitting")
        self.quit_action = QComboBox()
        for value, label in QUIT_LABELS:
            self.quit_action.addItem(label, value)
        self.quit_action.setCurrentIndex(max(0, self.quit_action.findData(settings.quit_action)))
        # the default show (M14): opens when the PC starts, and comes back after a schedule
        self.default_show = QComboBox()
        self.default_show.addItem("Last loaded", "")
        self.default_show.setItemData(0, "The PC's start opens the show that was loaded last, and nothing loads when a "
                                         "schedule ends", Qt.ItemDataRole.ToolTipRole)
        self.default_show.addItem("None (lights off)", NO_DEFAULT_SHOW)
        self.default_show.setItemData(1, "The PC's start opens no show (every light off), and when a schedule ends the "
                                         "show closes (after the countdown, the lights fade off)", Qt.ItemDataRole.ToolTipRole)
        for info in shows or ():
            self.default_show.addItem(info.name, info.id)
        if settings.default_show and self.default_show.findData(settings.default_show) < 0:
            self.default_show.addItem(f"{settings.default_show} (not there any more)", settings.default_show)
        self.default_show.setCurrentIndex(max(0, self.default_show.findData(settings.default_show)))
        default_row = QHBoxLayout()
        default_row.addWidget(QLabel("Default show"))
        default_row.addWidget(self.default_show, 1)
        quit_layout = QVBoxLayout(quit_box)
        quit_layout.addLayout(default_row)
        quit_layout.addWidget(hint("It opens when the PC starts (instead of the last show), and loads again when a global "
                                   "automation that loaded another show stops matching (with the countdown), unless "
                                   "someone has loaded a show by hand since. None (lights off) is no show at all: every "
                                   "light off, the global automations still on. Global automations still win: one that's "
                                   "under way loads its show. Reopening CentraLight after a quit or a crash carries on as it "
                                   "was."))
        quit_layout.addWidget(self.start_with_windows)
        quit_layout.addWidget(self.clshow_files)
        quit_layout.addWidget(hint("Closing the window keeps CentraLight running in the tray; Quit is in the tray icon's menu."))
        quit_layout.addWidget(self.quit_action)
        quit_layout.addWidget(hint("The Eurolite keeps showing the last look until something else is sent."))

        # --- files
        files_box = QGroupBox("Files")
        show_path = ElidedLabel(show_folder or "(none)")
        show_path.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        open_show = QPushButton("Open show folder")
        open_show.clicked.connect(lambda: self._open(Path(self._show_folder)))
        open_show.setEnabled(bool(show_folder))  # (no show loaded, M14: there's no folder to open)
        open_logs = QPushButton("Open log folder")
        open_logs.clicked.connect(lambda: self._open(app_data_dir()))
        preview = QPushButton("Look preview…")
        preview.setToolTip("Colours, fonts and controls on one page (for checking the design)")
        preview.clicked.connect(self._preview)
        diagnostics = QPushButton("Diagnostics…")
        diagnostics.setToolTip("How CentraLight is doing right now (also Ctrl+Shift+D, and in the tray icon's menu)")
        diagnostics.clicked.connect(self._diagnostics)
        files_buttons = QHBoxLayout()
        files_buttons.addWidget(open_show)
        files_buttons.addWidget(open_logs)
        files_buttons.addStretch(1)
        files_buttons.addWidget(diagnostics)
        files_buttons.addWidget(preview)
        files_layout = QVBoxLayout(files_box)
        files_layout.addWidget(QLabel("The loaded show's folder"))
        files_layout.addWidget(show_path)
        files_layout.addLayout(files_buttons)

        # --- AI
        ai_box = QGroupBox("AI: inputs written by an AI")
        ai_layout = QVBoxLayout(ai_box)
        ai_layout.addWidget(hint(
            "Any AI chat can write inputs: use Create with AI on the Dashboard. AI tools such as Claude Code or Cursor "
            "can work in the show folder: they read AI_GUIDE.md there, and inputs they write appear straight away. "
            "Global automations have a guide of their own, in the shows folder's “_Global automations”."
        ))
        ai_buttons = QHBoxLayout()
        ai_folder = QPushButton("Open show folder")
        ai_folder.setToolTip("Point Claude Code or Cursor at this folder")
        ai_folder.clicked.connect(lambda: self._open(Path(self._show_folder)))
        ai_folder.setEnabled(bool(show_folder))
        ai_buttons.addWidget(ai_folder)
        open_guide = QPushButton("Open the AI guide")
        open_guide.clicked.connect(lambda: self._open(Path(self._show_folder) / "AI_GUIDE.md"))
        export = QPushButton("Export Claude skill…")
        export.setToolTip("A folder Claude Code can use (copy it into .claude\\skills), or upload to Claude")
        export.clicked.connect(self._export_skill)
        open_guide.setEnabled(bool(show_folder))
        export.setEnabled(show is not None and not show.no_show)  # (a skill describes a show's lights: M14)
        ai_buttons.addWidget(open_guide)
        ai_buttons.addWidget(export)
        ai_buttons.addStretch(1)
        ai_layout.addLayout(ai_buttons)
        self.ai_status = hint("")
        self.ai_status.setVisible(False)
        ai_layout.addWidget(self.ai_status)

        content = QWidget()
        content.setObjectName("ScrollContent")
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 6, 0)
        content_layout.addWidget(output_box)
        content_layout.addWidget(ndi_box)
        content_layout.addWidget(remote_box)
        content_layout.addWidget(audio_box)
        content_layout.addWidget(black_box)
        content_layout.addWidget(quit_box)
        content_layout.addWidget(files_box)
        content_layout.addWidget(ai_box)
        content_layout.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(content)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        ok = buttons.button(QDialogButtonBox.StandardButton.Ok)
        ok.setProperty("accent", True)
        ok.setDefault(True)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(scroll, 1)
        layout.addWidget(buttons)
        self._sync_enabled()
        screen = self.screen().availableGeometry() if self.screen() else None
        self.resize(620, min(760, screen.height() - 80) if screen else 700)

    def selected_output(self) -> str:
        button = self.output_group.checkedButton()
        return str(button.property("value")) if button is not None else OUTPUT_SIMULATED

    def _sync_enabled(self) -> None:
        output = self.selected_output()
        for widget in (self.port, self.full_universe):
            widget.setEnabled(output == OUTPUT_USB)
        for widget in (self.artnet_ip, self.artnet_universe):
            widget.setEnabled(output == OUTPUT_ARTNET)

    def _open(self, folder: Path) -> None:
        if folder.exists():
            os.startfile(folder)  # noqa: S606 (opens Explorer, or the file in its own program)

    def _export_skill(self) -> None:
        from PySide6.QtWidgets import QFileDialog

        from ..ai_guide import export_skill

        target = QFileDialog.getExistingDirectory(self, "Where should the Claude skill folder go?", str(Path.home() / "Documents"))
        if target and self._show is not None:
            folder = export_skill(self._show, Path(target))
            self.ai_status.setText(f"Saved: {folder}")
            self.ai_status.setVisible(True)

    def _preview(self) -> None:
        self.preview_requested = True
        self.reject()

    def done(self, result: int) -> None:  # noqa: D401 (Qt name)
        self._meter_timer.stop()  # (no meter to update once it's closed)
        super().done(result)

    def _diagnostics(self) -> None:
        self.diagnostics_requested = True
        self.reject()

    def apply_to(self, settings: Settings) -> None:
        settings.output = self.selected_output()
        settings.usb_port = str(self.port.currentData() or "")
        settings.full_universe = self.full_universe.isChecked()
        settings.artnet_ip = self.artnet_ip.text().strip() or settings.artnet_ip
        settings.artnet_universe = self.artnet_universe.value()
        settings.quit_action = str(self.quit_action.currentData() or QUIT_ASK)
        settings.black_fade = round(self.black_fade.value(), 2)
        settings.countdown = self.countdown.value()
        settings.default_show = str(self.default_show.currentData() or "")
        settings.start_with_windows = self.start_with_windows.isChecked()
        settings.clshow_files = self.clshow_files.isChecked()
        settings.remote_port = self.remote_port.value()
        settings.remote_lan = self.remote_lan.isChecked()
        settings.remote_key = self.remote_key.text().strip() or new_key()
        settings.ndi_default = self.ndi_default.text().strip() or settings.ndi_default
        settings.audio_source = str(self.audio_source.currentData() or "")
        settings.audio_attack = self.audio_attack.value()
        settings.audio_release = self.audio_release.value()


class AudioMeter(QWidget):
    """Six small bars: loudness, bass, middle, treble, beat and pitch, as the engine has them now."""

    NAMES = (("level", "Loud"), ("bass", "Bass"), ("mid", "Mid"), ("treble", "Treble"), ("beat", "Beat"), ("pitch", "Pitch"))

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._values: dict = {}
        self.setMinimumHeight(self.fontMetrics().height() * 2 + 30)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setToolTip("What audio.level, audio.bass… are right now (0 when nothing is playing)")

    def set_values(self, values) -> None:
        values = dict(values or {})
        if values != self._values:
            self._values = values
            self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        from PySide6.QtCore import QRectF
        from PySide6.QtGui import QColor, QPainter

        from . import theme

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        fm = self.fontMetrics()
        count = len(self.NAMES)
        gap = 8
        width = (self.width() - gap * (count - 1)) / count
        bar_h = self.height() - fm.height() - 6
        for i, (key, label) in enumerate(self.NAMES):
            x = i * (width + gap)
            box = QRectF(x, 0, width, bar_h)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(theme.CARD))
            painter.drawRoundedRect(box, 3, 3)
            value = min(1.0, max(0.0, float(self._values.get(key, 0.0) or 0.0)))
            painter.setBrush(QColor(theme.ACCENT))
            painter.drawRoundedRect(QRectF(x, bar_h * (1 - value), width, bar_h * value), 3, 3)
            painter.setPen(QColor(theme.TEXT_2))
            painter.drawText(QRectF(x, bar_h + 4, width, fm.height()), Qt.AlignmentFlag.AlignHCenter,
                             fm.elidedText(label, Qt.TextElideMode.ElideRight, int(width)))
        painter.end()


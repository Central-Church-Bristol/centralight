"""Settings: where the DMX goes, the Stream Deck remote, what Quit does, and where the files are."""

from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

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
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setModal(True)
        self.setMinimumWidth(560)
        self._show_folder = show_folder
        self.preview_requested = False

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

        # --- quitting
        quit_box = QGroupBox("Starting and quitting")
        self.quit_action = QComboBox()
        for value, label in QUIT_LABELS:
            self.quit_action.addItem(label, value)
        self.quit_action.setCurrentIndex(max(0, self.quit_action.findData(settings.quit_action)))
        quit_layout = QVBoxLayout(quit_box)
        quit_layout.addWidget(self.start_with_windows)
        quit_layout.addWidget(hint("Closing the window keeps CentraLight running in the tray; Quit is in the tray icon's menu."))
        quit_layout.addWidget(self.quit_action)
        quit_layout.addWidget(hint("The Eurolite keeps showing the last look until something else is sent."))

        # --- files
        files_box = QGroupBox("Files")
        show_path = ElidedLabel(show_folder or "(none)")
        show_path.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        open_show = QPushButton("Open show folder")
        open_show.clicked.connect(lambda: self._open(Path(self._show_folder)))
        open_logs = QPushButton("Open log folder")
        open_logs.clicked.connect(lambda: self._open(app_data_dir()))
        preview = QPushButton("Look preview…")
        preview.setToolTip("Colours, fonts and controls on one page (for checking the design)")
        preview.clicked.connect(self._preview)
        files_buttons = QHBoxLayout()
        files_buttons.addWidget(open_show)
        files_buttons.addWidget(open_logs)
        files_buttons.addStretch(1)
        files_buttons.addWidget(preview)
        files_layout = QVBoxLayout(files_box)
        files_layout.addWidget(QLabel("Show folder"))
        files_layout.addWidget(show_path)
        files_layout.addLayout(files_buttons)

        content = QWidget()
        content.setObjectName("ScrollContent")
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 6, 0)
        content_layout.addWidget(output_box)
        content_layout.addWidget(remote_box)
        content_layout.addWidget(quit_box)
        content_layout.addWidget(files_box)
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
        if folder.is_dir():
            os.startfile(folder)  # noqa: S606 (opens Explorer)

    def _preview(self) -> None:
        self.preview_requested = True
        self.reject()

    def apply_to(self, settings: Settings) -> None:
        settings.output = self.selected_output()
        settings.usb_port = str(self.port.currentData() or "")
        settings.full_universe = self.full_universe.isChecked()
        settings.artnet_ip = self.artnet_ip.text().strip() or settings.artnet_ip
        settings.artnet_universe = self.artnet_universe.value()
        settings.quit_action = str(self.quit_action.currentData() or QUIT_ASK)
        settings.start_with_windows = self.start_with_windows.isChecked()
        settings.remote_port = self.remote_port.value()
        settings.remote_lan = self.remote_lan.isChecked()
        settings.remote_key = self.remote_key.text().strip() or new_key()

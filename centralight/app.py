"""Starting CentraLight: logging, the look, the show, the output, the engine and the window.

Order matters for "no flicker": the last live state is loaded before the engine's first
tick, and the output thread sends nothing until that first frame exists.
"""

from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

from .engine import Engine
from .live_state import LiveState
from .log import install_crash_logging, log_exception, log_info
from .model import Show
from .new_show import FOLLOW_SCREEN_ID, open_or_create_show, show_from_patch
from .output import DmxOutputThread, device_spec, universe_size
from .sources import SourceManager
from .paths import default_show_dir
from .priority import prefer_livestream_apps
from .settings import QUIT_OFF, Settings
from .show_io import ShowError, backup_show
from .timing import begin_precise_timer, end_precise_timer


def _prepare_windows_shell() -> None:
    if sys.platform != "win32":
        return
    import ctypes

    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("CentraLight.App.1")
    except (AttributeError, OSError):
        pass


def open_show(settings: Settings) -> tuple[Show, str]:
    """The show to use, plus a message for people if something was wrong with it."""
    folder = Path(settings.show_folder) if settings.show_folder else default_show_dir()
    try:
        show = open_or_create_show(folder)
    except Exception as exc:  # whatever is wrong with the files, CentraLight still starts (and says why)
        log_exception("opening the show", exc)
        reason = str(exc) if isinstance(exc, (ShowError, OSError)) else f"something in it couldn't be read ({type(exc).__name__}: {exc})"
        show = replace(show_from_patch(), folder=str(folder), read_only=True)
        return show, (
            f"CentraLight couldn't open the show in\n{folder}\n\n{reason}\n\n"
            "It's running with the standard church lights and won't save any changes until this is "
            "fixed. Earlier copies are in the show's backups folder."
        )
    for warning in show.warnings:
        log_info(f"Show warning: {warning}")
    for input_id, error in show.input_errors.items():
        log_info(f"Input {input_id} couldn't be read: {error}")
    if not show.read_only:
        try:
            backup_show(folder)
        except OSError as exc:
            log_exception("show backup", exc)
    return show, ""


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)
    install_crash_logging()
    _prepare_windows_shell()
    begin_precise_timer()
    prefer_livestream_apps()

    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication, QMessageBox

    from .ui.icons import app_icon
    from .ui.main_window import MainWindow
    from .ui.theme import apply_theme

    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(argv)
    app.setApplicationName("CentraLight")
    app.setStyle("Fusion")
    fonts = apply_theme(app)
    if fonts.missing:
        log_info(f"Brand fonts missing (using {fonts.ui}): {', '.join(fonts.missing)}")
    app.setWindowIcon(app_icon())

    settings = Settings.load()
    show, problem = open_show(settings)
    live = LiveState.load()
    if live is not None and live.show != show.folder:
        live = None  # it belongs to a different show

    try:
        import cv2

        cv2.setNumThreads(1)  # keep CPU use modest next to ProPresenter
    except ImportError:
        pass
    sources = SourceManager()
    output = DmxOutputThread(device_spec(settings, universe_size(show.fixtures)))
    output.start()
    from .audio import AudioCapture

    audio = AudioCapture(settings.audio_source, settings.audio_attack, settings.audio_release)
    audio.start()  # off unless Settings → Audio says what to listen to
    engine = Engine(show, output, live, sources=sources, audio=audio, initial_base=FOLLOW_SCREEN_ID,
                    started_by_windows="--minimized" in argv)
    engine.start()
    log_info(f"CentraLight started: show {show.folder}, output {settings.output}")

    from PySide6.QtWidgets import QSystemTrayIcon

    from . import startup
    from .commands import PowerEvent
    from .remote import RemoteServer, new_key
    from .ui.instance import InstanceServer
    from .ui.power import PowerEventFilter
    from .ui.tray import Tray

    if not settings.remote_key:
        settings.remote_key = new_key()  # only used if "Allow other computers" is switched on
    remote = RemoteServer(engine, settings.remote_port, settings.remote_lan, settings.remote_key)
    remote.start()  # if the port is taken, the header's Remote chip says so

    window = MainWindow(settings, show, engine, output, remote=remote)
    # Windows logging off or shutting down: stop cleanly without asking
    app.aboutToQuit.connect(lambda: window.shutdown(lights_off=settings.quit_action == QUIT_OFF))

    # Always running: the tray, a second launch showing this window, sleep / wake, start with Windows
    tray = Tray(window) if QSystemTrayIcon.isSystemTrayAvailable() else None
    if tray is not None:
        app.setQuitOnLastWindowClosed(False)  # closing the window hides it in the tray
        window.attach_tray(tray)
    window.instance_server = InstanceServer(window.bring_to_front)
    window.power_filter = PowerEventFilter(lambda kind: engine.submit(PowerEvent(kind)) if kind == "woke" else log_info("The PC is going to sleep"))
    app.installNativeEventFilter(window.power_filter)
    if startup.managed():
        trouble = startup.sync(settings.start_with_windows)
        if trouble:
            log_info(trouble)

    minimized = "--minimized" in argv and tray is not None
    if minimized:
        log_info("Started in the tray (by Windows at sign-in)")
    elif settings.maximized:
        window.showMaximized()
    else:
        window.show()
    if problem:
        QMessageBox.warning(window, "Show problem", problem)
    try:
        return app.exec()
    finally:
        end_precise_timer()

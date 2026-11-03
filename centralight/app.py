"""Starting CentraLight: logging, the look, the show, the output, the engine and the window.

Order matters for "no flicker": the last live state is loaded before the engine's first
tick, and the output thread sends nothing until that first frame exists.
"""

from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

from .engine import Engine
from .global_automations import GLOBAL_NAME, global_folder, load_global_automations
from .live_state import LiveState
from .log import install_crash_logging, log_exception, log_info
from .model import Show
from .new_show import FOLLOW_SCREEN_ID, open_or_create_show, show_from_patch
from .output import DmxOutputThread, device_spec, universe_size
from .sources import SourceManager
from .paths import default_show_dir, shows_root
from .priority import prefer_livestream_apps
from .settings import QUIT_OFF, Settings
from .show_io import SHOW_FILE, ShowError, backup_show
from .timing import begin_precise_timer, end_precise_timer


def _prepare_windows_shell() -> None:
    if sys.platform != "win32":
        return
    import ctypes

    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("CentraLight.App.1")
    except (AttributeError, OSError):
        pass


def show_to_open(settings: Settings, live: LiveState | None, root: Path | None = None) -> Path:
    """The show that was loaded last (live.json says which), else the one Settings names, else Central Church; if
    none of those is there any more (deleted, say), the first show there is; only with no show at all is Central
    Church made afresh."""
    from .shows import list_shows

    for candidate in (live.show if live is not None else "", settings.show_folder, str(default_show_dir())):
        if candidate and (Path(candidate) / SHOW_FILE).is_file():
            return Path(candidate)
    shows = list_shows(root if root is not None else shows_root())
    if shows:
        log_info(f"The last show ({(live.show if live is not None else '') or settings.show_folder}) isn't there any more, "
                 f"so “{shows[0].name}” opens")
        return Path(shows[0].folder)
    return default_show_dir()


def open_global_automations(root: Path | None = None) -> Show:
    """The global automations (app-wide, next to the shows). If they can't be read, CentraLight still starts."""
    folder = global_folder(root if root is not None else shows_root())
    try:
        folder.mkdir(parents=True, exist_ok=True)
        container = load_global_automations(folder)
    except Exception as exc:
        log_exception("opening the global automations", exc)
        return Show(name=GLOBAL_NAME, folder=str(folder), read_only=True)
    for warning in container.warnings:
        log_info(f"Global automations: {warning}")
    return container


def open_show(settings: Settings, folder: Path | None = None) -> tuple[Show, str]:
    """The show to use, plus a message for people if something was wrong with it."""
    if folder is None:
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
    live = LiveState.load()
    show, problem = open_show(settings, show_to_open(settings, live))
    if live is not None:
        live = live.for_show(show.folder)  # (saved with another show loaded: only what's app-wide carries over)
    if show.folder and not show.read_only:
        settings.show_folder = show.folder
    global_automations = open_global_automations()

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
                    started_by_windows="--minimized" in argv, global_automations=global_automations)
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
    remote = RemoteServer.from_settings(engine, settings)
    remote.start()  # if the port is taken, the header's Remote chip says so

    window = MainWindow(settings, show, engine, output, remote=remote, global_automations=global_automations,
                        shows_root=shows_root())
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

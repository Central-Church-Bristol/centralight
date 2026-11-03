"""Create (or edit) an input with any AI chat website: no API keys, just copy and paste.

1. Describe the look (or, for an edit, what should change) and click Copy prompt.
2. Paste that into any AI chat (ChatGPT, Claude, Gemini, Copilot...) and copy its whole reply.
3. Paste the reply here. It's checked straight away: if it's fine you see a live preview of the
   lights (and can try it on the real lights); if not, the problems are listed in plain words and
   "Copy error for the AI" gives you a ready-made message asking the AI to fix them.
4. Add (or, for an edit, Replace). It's one undo step, and its tile gets a small sparkle.
"""

from __future__ import annotations

import time
from typing import Any

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..ai_guide import (
    AIReplyError,
    check_input_data,
    create_prompt,
    describe_changes,
    edit_prompt,
    fix_message,
    outside_media,
    parse_reply,
    with_media,
)
from ..commands import SetSolo
from ..input_edit import copy_media
from ..log import log_exception
from ..model import Input
from ..preview import Previewer
from .document import ShowDocument
from .light_view import LightView
from .widgets import Panel

PREVIEW_MS = 125  # the preview moves at 8 pictures a second


def _hint(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("Hint")
    label.setWordWrap(True)
    return label


class AIDialog(QDialog):
    def __init__(self, document: ShowDocument, engine: Any, input_id: str = "", reply: str = "",
                 variables: Any = None, sources: Any = None, parent: QWidget | None = None) -> None:
        """input_id "" = make a new input; otherwise edit that one. reply: a reply to check straight away
        (e.g. pasted on the Dashboard with Ctrl+V)."""
        super().__init__(parent)
        self.document = document
        self.engine = engine
        self.input_id = input_id if input_id in document.show.inputs else ""
        self.candidate: Input | None = None
        self.problems: list[str] = []
        self._variables = variables or {}
        self._previewer = Previewer(sources)
        self._started = time.monotonic()
        self._soloing = False
        self._outside: dict = {}  # pictures the reply uses from outside the show folder
        self._copied: dict[str, str] = {}  # ... and where they were copied to ("media/...")
        editing = self.current()
        self.setWindowTitle(f"Edit “{editing.name}” with AI" if editing else "Create an input with AI")
        self.setModal(True)

        # --- step 1
        ask = Panel("1. What should change?" if editing else "1. Describe the look")
        self.description = QPlainTextEdit()
        self.description.setObjectName("AIDescription")
        self.description.setPlaceholderText(
            "e.g. make it slower, and leave the pillars alone" if editing else
            "e.g. the pillars breathing purple, slowly, while the other lights follow the screen")
        self.description.setFixedHeight(self.description.fontMetrics().height() * 4 + 16)
        ask.body.addWidget(self.description)
        self.name = QLineEdit()
        self.name.setPlaceholderText("Name (optional)")
        self.name.setVisible(editing is None)
        ask.body.addWidget(self.name)
        copy_row = QHBoxLayout()
        self.copy_button = QPushButton("Copy prompt")
        self.copy_button.setProperty("accent", True)
        self.copy_button.setToolTip("Puts a complete request for the AI on the clipboard")
        self.copy_button.clicked.connect(self.copy_prompt)
        copy_row.addWidget(self.copy_button)
        copy_row.addStretch(1)
        ask.body.addLayout(copy_row)
        self.copy_hint = _hint("Then paste it into any AI chat website (ChatGPT, Claude, Gemini, Copilot…) and copy its whole reply.")
        ask.body.addWidget(self.copy_hint)

        # --- step 2
        paste = Panel("2. Paste the AI's reply")
        self.reply = QPlainTextEdit()
        self.reply.setObjectName("AIReply")
        self.reply.setPlaceholderText("Paste the whole reply here (the part between the ``` marks is what matters).")
        self.reply.textChanged.connect(lambda: self._check_timer.start())
        paste.body.addWidget(self.reply, 1)
        paste_row = QHBoxLayout()
        paste_button = QPushButton("Paste from clipboard")
        paste_button.clicked.connect(lambda: self.reply.setPlainText(QGuiApplication.clipboard().text()))
        paste_row.addWidget(paste_button)
        paste_row.addStretch(1)
        paste.body.addLayout(paste_row)

        # --- the result
        result = Panel("Result")
        self.status = QLabel("Paste a reply to check it.")
        self.status.setObjectName("Hint")
        self.status.setWordWrap(True)
        result.body.addWidget(self.status)
        self.problems_label = QLabel("")
        self.problems_label.setObjectName("ExpressionProblem")
        self.problems_label.setWordWrap(True)
        self.problems_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        result.body.addWidget(self.problems_label)
        self.fix_button = QPushButton("Copy error for the AI")
        self.fix_button.setToolTip("A ready-made reply for the AI chat, asking it to fix these problems")
        self.fix_button.clicked.connect(self.copy_fix)
        fix_row = QHBoxLayout()
        fix_row.addWidget(self.fix_button)
        fix_row.addStretch(1)
        result.body.addLayout(fix_row)
        self.media_label = _hint("")
        result.body.addWidget(self.media_label)
        self.media_button = QPushButton("Copy into the show")
        self.media_button.setToolTip("Copies these files into the show's media folder, so the input can use them")
        self.media_button.clicked.connect(self.copy_media_in)
        media_row = QHBoxLayout()
        media_row.addWidget(self.media_button)
        media_row.addStretch(1)
        result.body.addLayout(media_row)
        self.notes_label = _hint("")
        result.body.addWidget(self.notes_label)
        self.light_view = LightView(document.show)
        self.light_view.setMinimumHeight(140)
        self.light_view.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        result.body.addWidget(self.light_view)
        solo_row = QHBoxLayout()
        self.solo_button = QPushButton("Try it on the lights")
        self.solo_button.setCheckable(True)
        self.solo_button.setToolTip("Show only this on the lights while this window is open")
        self.solo_button.toggled.connect(self._solo)
        solo_row.addWidget(self.solo_button)
        solo_row.addStretch(1)
        result.body.addLayout(solo_row)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(12)
        left_layout.addWidget(ask)
        left_layout.addWidget(paste, 1)
        right = QWidget()
        right.setObjectName("ScrollContent")
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 6, 0)
        right_layout.addWidget(result)
        right_layout.addStretch(1)
        right_scroll = QScrollArea()
        right_scroll.setWidgetResizable(True)
        right_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        right_scroll.setWidget(right)
        columns = QHBoxLayout()
        columns.setSpacing(12)
        columns.addWidget(left, 1)
        columns.addWidget(right_scroll, 1)

        self.buttons = QDialogButtonBox()
        self.use_button = self.buttons.addButton("Replace" if editing else "Add", QDialogButtonBox.ButtonRole.AcceptRole)
        self.use_button.setProperty("accent", True)
        self.buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        self.use_button.clicked.connect(self.use)
        self.buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.addLayout(columns, 1)
        layout.addWidget(self.buttons)

        self._check_timer = QTimer(self)
        self._check_timer.setSingleShot(True)
        self._check_timer.setInterval(400)
        self._check_timer.timeout.connect(self.check)
        self._preview_timer = QTimer(self)
        self._preview_timer.setInterval(PREVIEW_MS)
        self._preview_timer.timeout.connect(self._draw_preview)
        screen = self.screen().availableGeometry() if self.screen() else None
        self.resize(min(1100, screen.width() - 80) if screen else 1000, min(720, screen.height() - 80) if screen else 680)
        if reply:
            self.reply.setPlainText(reply)
            self.check()
        self._sync()

    def current(self) -> Input | None:
        return self.document.show.inputs.get(self.input_id) if self.input_id else None

    # ------------------------------------------------------------ prompts

    def copy_prompt(self) -> str:
        editing = self.current()
        text = self.description.toPlainText()
        prompt = edit_prompt(self.document.show, editing, text) if editing else create_prompt(self.document.show, text, self.name.text())
        QGuiApplication.clipboard().setText(prompt)
        self.copy_button.setText("Copied ✓")
        QTimer.singleShot(2500, lambda: self.copy_button.setText("Copy prompt"))
        return prompt

    def copy_fix(self) -> str:
        message = fix_message(self.problems)
        QGuiApplication.clipboard().setText(message)
        self.fix_button.setText("Copied ✓")
        QTimer.singleShot(2500, lambda: self.fix_button.setText("Copy error for the AI"))
        return message

    # ------------------------------------------------------------ checking the reply

    def check(self) -> None:
        self._check_timer.stop()
        text = self.reply.toPlainText()
        self.candidate, self.problems, notes = None, [], []
        self._outside = {}
        if text.strip():
            try:
                data = with_media(parse_reply(text), self._copied)
            except AIReplyError as exc:
                self.problems = [str(exc)]
            else:
                self._outside = outside_media(data, self.document.show)
                self.candidate, self.problems, notes = check_input_data(data, self.document.show, existing_id=self.input_id or None)
        if self.candidate is not None:
            editing = self.current()
            if editing is not None:
                notes = [f"Changes: {', '.join(describe_changes(editing, self.candidate))}"] + notes
            self.status.setText(f"Ready: “{self.candidate.name}”. The preview below shows what the lights will do.")
        elif self.problems:
            self.status.setText("CentraLight can't use that yet:")
        else:
            self.status.setText("Paste a reply to check it.")
        self.problems_label.setText("\n".join(f"• {p}" for p in self.problems))
        self.notes_label.setText("\n".join(notes))
        self._started = time.monotonic()
        self._sync()
        if self.solo_button.isChecked():
            self._solo(True)

    def _sync(self) -> None:
        ok = self.candidate is not None
        self.use_button.setEnabled(ok)
        self.solo_button.setEnabled(ok)
        if not ok and self.solo_button.isChecked():
            self.solo_button.setChecked(False)
        self.problems_label.setVisible(bool(self.problems))
        self.fix_button.setVisible(bool(self.problems))
        files = "\n".join(str(path) for path in self._outside.values())
        self.media_label.setText(f"It uses {'a file' if len(self._outside) == 1 else 'files'} from elsewhere on this PC:\n{files}"
                                 if self._outside else "")
        self.media_label.setVisible(bool(self._outside))
        self.media_button.setVisible(bool(self._outside))
        self.notes_label.setVisible(bool(self.notes_label.text()))
        self.light_view.setVisible(ok)
        if ok:
            self._preview_timer.start()
            self._draw_preview()
        else:
            self._preview_timer.stop()

    def _draw_preview(self) -> None:
        if self.candidate is None:
            return
        colours = self._previewer.colours(self.document.show, self.candidate, time.monotonic() - self._started, self._variables)
        self.light_view.set_levels(colours, {})

    def _solo(self, on: bool) -> None:
        if on and self.candidate is not None:
            self.engine.submit(SetSolo(self.candidate.id, input=self.candidate))
            self._soloing = True
        elif self._soloing:
            self.engine.submit(SetSolo(None))
            self._soloing = False

    def copy_media_in(self) -> list[str]:
        """Copy the pictures the reply uses from elsewhere into the show's media folder (clicking the button is
        the OK), then check it again with their new places."""
        copied = []
        for file, path in self._outside.items():
            try:
                self._copied[file] = copy_media(self.document.show.folder, path)
            except OSError as exc:
                log_exception(f"copying {path} into the show", exc)
                continue
            copied.append(self._copied[file])
        self.check()
        return copied

    # ------------------------------------------------------------ using it

    def use(self) -> bool:
        """Add (or replace) the input: one undo step."""
        inp = self.candidate
        if inp is None:
            return False
        show = self.document.show
        if self.input_id:
            done = self.document.apply(show.with_inputs({**show.inputs, inp.id: inp}), f"{inp.name}: changed with AI")
        else:
            done = self.document.apply(show.with_inputs({**show.inputs, inp.id: inp}, show.input_order + (inp.id,)), f"Add {inp.name} (made with AI)")
        if done:
            self.accept()
        return done

    def done(self, result: int) -> None:  # noqa: D401 (Qt name)
        self._preview_timer.stop()
        self._check_timer.stop()
        if self._soloing:
            self.engine.submit(SetSolo(None))
            self._soloing = False
        super().done(result)

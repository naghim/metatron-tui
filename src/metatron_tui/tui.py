"""metatron-tui - an interactive Textual client for Metatron.

A thin UI layer over the upstream Metatron PDF metadata engine
(https://github.com/naghim/metatron). Launched via the ``metatron-tui``
command; the same app can be served to a browser with ``textual serve``.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import ClassVar

from metatron.core import (
    SETTABLE_FIELDS,
    PDFMetadata,
    clean_metadata,
    read_metadata,
    update_metadata,
)
from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
from textual.theme import BUILTIN_THEMES
from textual.widgets import (
    Button,
    DataTable,
    Footer,
    Input,
    Label,
    Static,
    TabbedContent,
    TabPane,
)

from metatron_tui import __version__

# friendly name -> label shown in the form
EDITABLE_FIELDS: list[tuple[str, str]] = [
    ("title", "Title"),
    ("author", "Author"),
    ("subject", "Subject"),
    ("keywords", "Keywords"),
    ("creator", "Creator"),
    ("producer", "Producer"),
]


def _human_size(nbytes: int) -> str:
    """Format a byte count as a short human-readable string."""
    size = float(nbytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


class ConfirmScreen(Screen[bool]):
    """A small modal confirmation dialog."""

    BINDINGS: ClassVar[list[Binding]] = [Binding("escape", "dismiss(False)", "Cancel")]

    def __init__(self, message: str, confirm_label: str = "Confirm", danger: bool = False) -> None:
        super().__init__()
        self._message = message
        self._confirm_label = confirm_label
        self._danger = danger

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm-box"):
            yield Static(self._message, id="confirm-msg")
            with Horizontal(id="confirm-buttons"):
                yield Button("Cancel", id="cancel")
                yield Button(
                    self._confirm_label,
                    id="confirm",
                    variant="error" if self._danger else "primary",
                )

    @on(Button.Pressed)
    def _on_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        self.dismiss(event.button.id == "confirm")


class MetatronApp(App[None]):
    """Interactive PDF metadata viewer/editor."""

    TITLE = "Metatron"
    SUB_TITLE = "PDF metadata editor"

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("ctrl+s", "save", "Save"),
        Binding("ctrl+r", "reload", "Reload"),
        Binding("ctrl+l", "focus_path", "Path"),
        Binding("ctrl+t", "cycle_theme", "Theme"),
        Binding("ctrl+q", "quit", "Quit"),
    ]

    CSS = """
    Screen {
        layout: vertical;
        align-horizontal: center;
        background: $surface;
    }

    #titlebar {
        dock: top;
        height: 1;
        padding: 0 2;
        background: $primary;
        color: $text;
        text-style: bold;
    }

    #shell {
        width: 100%;
        max-width: 104;
        height: 1fr;
    }

    #toolbar {
        height: 1;
        margin: 1 2 0 2;
    }
    #file-path {
        width: 1fr;
        padding: 0 1;
        background: $panel;
    }
    #toolbar Button {
        height: 1;
        min-width: 6;
        border: none;
        margin-left: 1;
        padding: 0 2;
    }

    #overview {
        height: 1;
        margin: 1 2 0 2;
        color: $text-muted;
    }

    TabbedContent {
        height: 1fr;
        margin-top: 1;
    }

    #fields {
        padding: 1 2;
    }
    .field-row {
        height: 1;
        margin-bottom: 1;
    }
    .field-label {
        width: 12;
        content-align: right middle;
        padding: 0 1 0 0;
        color: $text-muted;
    }
    .field-row Input {
        width: 1fr;
        padding: 0 1;
    }
    .field-row Input:focus {
        background: $boost;
    }

    DataTable { height: 1fr; }

    ConfirmScreen { align: center middle; background: $background 60%; }
    #confirm-box {
        width: 56;
        height: auto;
        padding: 1 2;
        background: $surface;
        border: round $error;
    }
    #confirm-msg { width: 1fr; margin-bottom: 1; }
    #confirm-buttons { height: auto; align-horizontal: right; }
    #confirm-buttons Button { margin-left: 2; height: 1; border: none; padding: 0 2; }
    """

    def __init__(self, path: Path | None = None, theme: str = "catppuccin-mocha") -> None:
        super().__init__()
        self.theme = theme
        self.path = path
        self.meta: PDFMetadata | None = None
        self._field_inputs: dict[str, Input] = {}
        self.title = "Metatron"

    def compose(self) -> ComposeResult:
        yield Static("Metatron", id="titlebar")
        with Vertical(id="shell"):
            with Horizontal(id="toolbar"):
                yield Input(compact=True, placeholder="Path to a PDF file…", id="file-path")
                yield Button("Load", compact=True, id="load", variant="primary")
                yield Button("Save", compact=True, id="save", variant="success")
                yield Button("Strip", compact=True, id="strip", variant="error")
            yield Static("No file loaded.", id="overview")
            with TabbedContent(id="tabs"):
                with TabPane("Fields", id="tab-fields"):  # noqa: SIM117 - Textual compose idiom
                    with VerticalScroll(id="fields"):
                        for name, label in EDITABLE_FIELDS:
                            with Horizontal(classes="field-row"):
                                yield Label(label, classes="field-label")
                                field_input = Input(compact=True, id=f"field-{name}")
                                self._field_inputs[name] = field_input
                                yield field_input
                with TabPane("XMP", id="tab-xmp"):
                    yield DataTable(id="xmp-table")
                with TabPane("Custom / Other", id="tab-custom"):
                    yield DataTable(id="custom-table")
        yield Footer()

    def on_mount(self) -> None:
        for table_id in ("#xmp-table", "#custom-table"):
            table = self.query_one(table_id, DataTable)
            table.add_columns("Key", "Value")
            table.cursor_type = "row"
            table.zebra_stripes = True
        if self.path:
            self.query_one("#file-path", Input).value = str(self.path)
            self.load_file(self.path)

    # -- loading -----------------------------------------------------------

    def load_file(self, path: Path) -> None:
        path = path.expanduser()
        try:
            meta = read_metadata(path)
        except Exception as exc:  # noqa: BLE001 - surface any read error to the user
            self.notify(f"Failed to read {path.name}: {exc}", severity="error", timeout=8)
            return

        self.path = path
        self.meta = meta
        self.query_one("#file-path", Input).value = str(path)
        self._render_overview(meta)
        self._populate_fields(meta)
        self._populate_table("#xmp-table", sorted(meta.xmp_info.items()))
        self._populate_table("#custom-table", meta.custom_info.items())
        self.notify(f"Loaded {path.name}", severity="information", timeout=3)

    def _render_overview(self, meta: PDFMetadata) -> None:
        self.query_one("#titlebar", Static).update(f"Metatron  ·  {meta.filepath.name}")
        encrypted = "encrypted" if meta.is_encrypted else "not encrypted"
        text = (
            f"{_human_size(meta.file_size)}   ·   "
            f"{meta.page_count} pages   ·   "
            f"PDF {meta.pdf_version}   ·   {encrypted}"
        )
        self.query_one("#overview", Static).update(text)

    def _populate_fields(self, meta: PDFMetadata) -> None:
        for name, _ in EDITABLE_FIELDS:
            pdf_key = SETTABLE_FIELDS[name]
            self._field_inputs[name].value = meta.doc_info.get(pdf_key, "")

    def _populate_table(self, table_id: str, rows) -> None:
        table = self.query_one(table_id, DataTable)
        table.clear()
        for key, value in rows:
            table.add_row(str(key), str(value) if value else "—")

    # -- actions -----------------------------------------------------------

    def action_save(self) -> None:
        if self.path is None or not self.path.is_file():
            self.notify("No file loaded.", severity="warning")
            return
        assert self.meta is not None

        updates: dict[str, str] = {}
        for name, _ in EDITABLE_FIELDS:
            new_value = self._field_inputs[name].value
            old_value = self.meta.doc_info.get(SETTABLE_FIELDS[name], "")
            if new_value != old_value:
                updates[name] = new_value

        if not updates:
            self.notify("No changes to save.", severity="information")
            return

        try:
            update_metadata(self.path, **updates)
        except Exception as exc:  # noqa: BLE001
            self.notify(f"Save failed: {exc}", severity="error", timeout=8)
            return

        self.notify(f"Saved {len(updates)} field(s).", severity="information")
        self.load_file(self.path)

    def action_reload(self) -> None:
        if self.path:
            self.load_file(self.path)

    def action_focus_path(self) -> None:
        self.query_one("#file-path", Input).focus()

    def action_cycle_theme(self) -> None:
        themes = sorted(BUILTIN_THEMES)
        current = self.theme if self.theme in themes else themes[0]
        self.theme = themes[(themes.index(current) + 1) % len(themes)]
        self.notify(f"Theme: {self.theme}", timeout=2)

    # -- button handlers ---------------------------------------------------

    @on(Button.Pressed, "#load")
    def _handle_load(self) -> None:
        raw = self.query_one("#file-path", Input).value.strip()
        if not raw:
            self.notify("Enter a file path first.", severity="warning")
            return
        self.load_file(Path(raw))

    @on(Input.Submitted, "#file-path")
    def _handle_path_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        self._handle_load()

    @on(Button.Pressed, "#save")
    def _handle_save(self) -> None:
        self.action_save()

    @on(Button.Pressed, "#strip")
    def _handle_strip(self) -> None:
        if self.path is None or not self.path.is_file():
            self.notify("No file loaded.", severity="warning")
            return
        self.push_screen(
            ConfirmScreen(
                "Strip ALL metadata (Document Info + XMP) from "
                f"{self.path.name}?\nThis cannot be undone.",
                confirm_label="Strip",
                danger=True,
            ),
            self._after_strip,
        )

    def _after_strip(self, confirmed: bool | None) -> None:
        if not confirmed or self.path is None:
            return
        try:
            clean_metadata(self.path)
        except Exception as exc:  # noqa: BLE001
            self.notify(f"Strip failed: {exc}", severity="error", timeout=8)
            return
        self.notify("All metadata stripped.", severity="information")
        self.load_file(self.path)


def main(argv: list[str] | None = None) -> None:
    """Entry point for the ``metatron-tui`` command."""
    parser = argparse.ArgumentParser(
        prog="metatron-tui",
        description="Interactive TUI client for Metatron PDF metadata.",
    )
    parser.add_argument("file", nargs="?", help="PDF to open (optional; you can load one in-app).")
    parser.add_argument(
        "--theme",
        default=os.environ.get("METATRON_THEME", "catppuccin-mocha"),
        help="Color theme, e.g. catppuccin-mocha, gruvbox, tokyo-night (Ctrl+T cycles).",
    )
    parser.add_argument("--version", action="version", version=f"metatron-tui {__version__}")
    args = parser.parse_args(argv)

    MetatronApp(Path(args.file) if args.file else None, theme=args.theme).run()


if __name__ == "__main__":
    main()

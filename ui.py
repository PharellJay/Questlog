"""Textual user interface for Questlog: your to-dos as an RPG quest journal.

One dashboard of JRPG-style windows: the questline sidebar and the command menu on the left,
today's (or the chosen view's) quests and the week ahead on the right.
The UI speaks RPG: projects are *questlines*, tasks are *quests*, and completing one earns XP.
The data model (models.py) keeps the plain names.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import date, timedelta

from rich.text import Text
from textual import events, on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Grid, Horizontal, Vertical
from textual.message import Message
from textual.screen import ModalScreen, Screen
from textual.theme import Theme
from textual.widget import Widget
from textual.widgets import (
    Button,
    Input,
    Label,
    OptionList,
    Select,
    Static,
    Switch,
    TextArea,
    Tree,
)
from textual.widgets.option_list import Option
from textual.widgets.tree import TreeNode

from models import (
    PRIORITIES,
    ROOT_ID,
    XP_REWARD,
    Filter,
    Store,
    StoreError,
    Task,
    describe_due,
    level_info,
    parse_due,
    sort_tasks,
    streak,
    title_for_level,
)

# ---------------------------------------------------------------- look

QUEST_THEME = Theme(
    name="questlog",
    primary="#c9a85c",  # window frame gold
    secondary="#3a4a8c",  # cursor blue
    accent="#ffd25e",  # bright gold: focus, highlights
    foreground="#eef0ff",
    background="#0a0d1c",  # night sky behind the windows
    surface="#121a3c",  # window body
    panel="#16204a",
    success="#7ee08a",
    warning="#ffb84d",
    error="#ff6b5e",
    dark=True,
)

GOLD, BRIGHT, CRIMSON, EMERALD, MUTED = "#c9a85c", "#ffd25e", "#ff6b5e", "#7ee08a", "#8b93b8"
PRIORITY_STYLE = {0: MUTED, 1: "#5aa2ff", 2: "#b57bff", 3: "bold #ff9a3c"}

TODAY_VIEW = "@today"  # main panel views besides questline ids: today, ROOT_ID (all), "@day:<iso>"
DAY_PREFIX = "@day:"
VIEW_NAMES = {TODAY_VIEW: "☀ Today's Quests", ROOT_ID: "✧ All Quests"}
TREE_NAMES = {TODAY_VIEW: "☀ Today", ROOT_ID: "✧ All quests"}


def _one_line(text: str, width: int = 60) -> str:
    text = " ".join(text.split())
    return text if len(text) <= width else text[: width - 1] + "…"


def _bar(done: int, total: int, width: int, full: str, empty: str, color: str) -> Text:
    filled = round(width * done / total) if total else 0
    return Text.assemble((full * filled, color), (empty * (width - filled), MUTED))


def _fit(text: Text, width: int) -> Text:
    """Crop or pad a line to exactly `width` cells."""
    text = text.copy()
    text.truncate(width, overflow="ellipsis", pad=True)
    return text


def _due_label(task: Task, today: date) -> tuple[str, str]:
    """(text, style) for a quest's due date in a list."""
    if not task.due or task.done:
        return "", ""
    days = (date.fromisoformat(task.due) - today).days
    if days < 0:
        return f"⚠ {-days}d late", f"bold {CRIMSON}"
    if days == 0:
        return "★ today", f"bold {BRIGHT}"
    return describe_due(task.due, today), MUTED


def _gem(task: Task) -> tuple[str, str]:
    return ("✔ ", EMERALD) if task.done else ("◆ ", PRIORITY_STYLE[task.priority])


def _day_view(day: date) -> str:
    return DAY_PREFIX + day.isoformat()


# ---------------------------------------------------------------- modals


def _rejected(screen: Screen, check: Callable[..., object] | None, *values: str) -> bool:
    """Run a dialog's check; if it fails, show why and keep the dialog (and its input) open."""
    if check is None:
        return False
    try:
        check(*values)
    except StoreError as exc:
        screen.notify(str(exc), severity="error")
        return True
    return False


def _dialog(title: str, *classes: str) -> Vertical:
    """The game window every dialog lives in, with its title set in the frame."""
    box = Vertical(classes=" ".join(("dialog",) + classes))
    box.border_title = title
    return box


class TextPrompt(ModalScreen[str | None]):
    """Ask for a single line of text (questline names)."""

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(
        self, title: str, value: str = "", check: Callable[[str], object] | None = None
    ) -> None:
        super().__init__()
        self.prompt_title = title
        self.value = value
        self.check = check

    def compose(self) -> ComposeResult:
        with _dialog(self.prompt_title):
            yield Input(value=self.value, id="text")
            with Horizontal(classes="buttons"):
                yield Button("OK", variant="primary", id="ok")
                yield Button("Cancel", id="cancel")

    @on(Input.Submitted)
    @on(Button.Pressed, "#ok")
    def submit(self) -> None:
        value = self.query_one("#text", Input).value
        if not _rejected(self, self.check, value):
            self.dismiss(value)

    @on(Button.Pressed, "#cancel")
    def action_cancel(self) -> None:
        self.dismiss(None)


class CreateProjectDialog(ModalScreen[tuple[str, str] | None]):
    """Ask for a questline name and where to put it. Returns (parent_id, name)."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("ctrl+l", "change_location", "Change location", priority=True),
    ]

    def __init__(self, store: Store, parent_id: str) -> None:
        super().__init__()
        self.store = store
        self.parent_id = parent_id

    def compose(self) -> ComposeResult:
        with _dialog("✦ New questline"):
            yield Label("Name")
            yield Input(id="name", placeholder="e.g. Work, Moving house, Learn Rust")
            with Horizontal(classes="location-row"):
                yield Label(id="location", classes="location")
                yield Button("Change (ctrl+l)", id="change")
            with Horizontal(classes="buttons"):
                yield Button("Create", variant="primary", id="create")
                yield Button("Cancel", id="cancel")

    def on_mount(self) -> None:
        self.update_location()
        self.query_one("#name", Input).focus()

    def update_location(self) -> None:
        where = "(top level)" if self.parent_id == ROOT_ID else self.store.path_of(self.parent_id)
        self.query_one("#location", Label).update(f"Inside: [b]{where}[/b]")

    @on(Button.Pressed, "#change")
    def action_change_location(self) -> None:
        def picked(dest: str | None) -> None:
            if dest is not None:
                self.parent_id = dest
                self.update_location()
            self.query_one("#name", Input).focus()

        self.app.push_screen(ProjectPicker(self.store, "Create the questline in…"), picked)

    @on(Input.Submitted)
    @on(Button.Pressed, "#create")
    def submit(self) -> None:
        name = self.query_one("#name", Input).value
        if not _rejected(self, self.store.check_name, self.parent_id, name):
            self.dismiss((self.parent_id, name))

    @on(Button.Pressed, "#cancel")
    def action_cancel(self) -> None:
        self.dismiss(None)


class TaskForm(ModalScreen[dict | None]):
    """Post or edit a quest. Returns {project_id, title, notes, due, priority}."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("ctrl+s", "save", "Save", priority=True),
        Binding("ctrl+l", "change_location", "Change questline", priority=True),
    ]

    def __init__(
        self, store: Store, title: str, project_id: str | None, task: Task | None = None,
        due: str = "",
    ) -> None:
        super().__init__()
        self.store = store
        self.form_title = title
        self.project_id = project_id
        self.task_ = task
        self.due_text = (task.due or "") if task else due

    def compose(self) -> ComposeResult:
        task = self.task_
        with _dialog(self.form_title, "wide"):
            yield Label("Quest")
            yield Input(task.title if task else "", id="title", placeholder="What needs doing?")
            with Horizontal(id="due-row"):
                with Vertical(id="due-col"):
                    yield Label("Due [dim](today, fri, +3, 2w, 10-24…)[/]")
                    yield Input(self.due_text, id="due")
                    yield Label(id="due-hint")
                with Vertical(id="prio-col"):
                    yield Label("Priority")
                    yield Select(
                        [(name, i) for i, name in enumerate(PRIORITIES)],
                        value=task.priority if task else 0,
                        allow_blank=False,
                        id="priority",
                    )
                    yield Label(id="reward")
            yield Label("Notes")
            yield TextArea(task.notes if task else "", id="notes", classes="notes")
            with Horizontal(classes="location-row"):
                yield Label(id="location", classes="location")
                yield Button("Change (ctrl+l)", id="change")
            yield Label("[dim]ctrl+s to save · tab to switch field · esc to cancel[/]")
            with Horizontal(classes="buttons"):
                yield Button("Save", variant="primary", id="save")
                yield Button("Cancel", id="cancel")

    def on_mount(self) -> None:
        self.query_one("#notes", TextArea).tab_behavior = "focus"
        self.update_location()
        self.update_due_hint(self.due_text)
        self.update_reward(self.query_one("#priority", Select).value)
        self.query_one("#title", Input).focus()

    def update_location(self) -> None:
        if self.project_id is None or self.project_id not in self.store.projects:
            where = f"[{CRIMSON}](none yet — choose one)[/]"
        else:
            where = f"[b]{self.store.path_of(self.project_id)}[/b]"
        self.query_one("#location", Label).update(f"Questline: {where}")

    @on(Select.Changed, "#priority")
    def priority_changed(self, event: Select.Changed) -> None:
        self.update_reward(event.value)

    def update_reward(self, priority: object) -> None:
        if isinstance(priority, int):
            self.query_one("#reward", Label).update(f"[{BRIGHT}]reward +{XP_REWARD[priority]} XP[/]")

    @on(Input.Changed, "#due")
    def due_changed(self, event: Input.Changed) -> None:
        self.update_due_hint(event.value)

    def update_due_hint(self, text: str) -> None:
        hint = self.query_one("#due-hint", Label)
        try:
            iso = parse_due(text)
        except StoreError:
            hint.update(f"[{CRIMSON}]can't read this date[/]")
            return
        if iso is None:
            hint.update("[dim]→ no deadline[/]")
        else:
            when = date.fromisoformat(iso)
            hint.update(f"[{EMERALD}]→ {when.strftime('%a %Y-%m-%d')}[/] [dim]({describe_due(iso)})[/]")

    @on(Button.Pressed, "#change")
    def action_change_location(self) -> None:
        def picked(dest: str | None) -> None:
            if dest is not None:
                self.project_id = dest
                self.update_location()

        self.app.push_screen(
            ProjectPicker(self.store, "Put the quest in…", allow_root=False), picked
        )

    @on(Input.Submitted)
    @on(Button.Pressed, "#save")
    def action_save(self) -> None:
        title = self.query_one("#title", Input).value
        if _rejected(self, Store.check_title, title):
            return
        try:
            due = parse_due(self.query_one("#due", Input).value)
        except StoreError as exc:
            self.notify(str(exc), severity="error")
            self.query_one("#due", Input).focus()
            return
        if self.project_id not in self.store.projects or self.project_id == ROOT_ID:
            self.notify("Choose a questline for the quest (ctrl+l).", severity="error")
            self.action_change_location()
            return
        self.dismiss({
            "project_id": self.project_id,
            "title": title,
            "notes": self.query_one("#notes", TextArea).text,
            "due": due,
            "priority": self.query_one("#priority", Select).value,
        })

    @on(Button.Pressed, "#cancel")
    def action_cancel(self) -> None:
        self.dismiss(None)


class FilterDialog(ModalScreen[Filter | None]):
    """Choose which quests the quest window shows."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("ctrl+s", "apply", "Apply", priority=True),
        Binding("ctrl+r", "reset", "Reset", priority=True),
    ]

    DUE_CHOICES = [
        ("Any time", "any"),
        ("Overdue", "overdue"),
        ("Due today (or earlier)", "today"),
        ("Due within 7 days", "week"),
        ("No due date", "none"),
    ]

    def __init__(self, current: Filter, focus_search: bool = False) -> None:
        super().__init__()
        self.current = current
        self.focus_search = focus_search

    def compose(self) -> ComposeResult:
        f = self.current
        with _dialog("⧩ Filter quests"):
            yield Label("Search (title and notes)")
            yield Input(f.text, id="text")
            with Grid(id="filter-grid"):
                with Vertical():
                    yield Label("Priority")
                    yield Select(
                        [("Any", 0)] + [(f"{name} or higher", i) for i, name in enumerate(PRIORITIES) if i],
                        value=f.min_priority, allow_blank=False, id="min-priority",
                    )
                with Vertical():
                    yield Label("Due")
                    yield Select(self.DUE_CHOICES, value=f.due, allow_blank=False, id="due")
            with Horizontal(classes="switch-row"):
                yield Switch(f.show_done, id="show-done")
                yield Label("Show completed quests")
            with Horizontal(classes="switch-row"):
                yield Switch(f.recursive, id="recursive")
                yield Label("Include sub-questlines")
            yield Label("[dim]enter / ctrl+s to apply · ctrl+r to reset · esc to cancel[/]")
            with Horizontal(classes="buttons"):
                yield Button("Apply", variant="primary", id="apply")
                yield Button("Reset", id="reset")
                yield Button("Cancel", id="cancel")

    def on_mount(self) -> None:
        if not self.focus_search:
            self.query_one("#min-priority", Select).focus()

    @on(Input.Submitted)
    @on(Button.Pressed, "#apply")
    def action_apply(self) -> None:
        self.dismiss(Filter(
            show_done=self.query_one("#show-done", Switch).value,
            min_priority=self.query_one("#min-priority", Select).value,
            due=self.query_one("#due", Select).value,
            text=self.query_one("#text", Input).value.strip(),
            recursive=self.query_one("#recursive", Switch).value,
        ))

    @on(Button.Pressed, "#reset")
    def action_reset(self) -> None:
        self.dismiss(Filter())

    @on(Button.Pressed, "#cancel")
    def action_cancel(self) -> None:
        self.dismiss(None)


class Confirm(ModalScreen[bool]):
    BINDINGS = [
        Binding("escape,n", "no", "No"),
        Binding("y", "yes", "Yes"),
        Binding("left,up", "app.focus_previous", "Previous", show=False),
        Binding("right,down", "app.focus_next", "Next", show=False),
    ]

    def __init__(self, message: str) -> None:
        super().__init__()
        self.message = message

    def compose(self) -> ComposeResult:
        with _dialog("⚠ Are you sure?"):
            yield Label(self.message, classes="dialog-text")
            with Horizontal(classes="buttons"):
                yield Button("Yes (y)", variant="error", id="yes")
                yield Button("No (n)", id="no")

    @on(Button.Pressed, "#yes")
    def action_yes(self) -> None:
        self.dismiss(True)

    @on(Button.Pressed, "#no")
    def action_no(self) -> None:
        self.dismiss(False)


class ProjectPicker(ModalScreen[str | None]):
    """Pick a destination questline. Questlines in `exclude_subtree` are hidden."""

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(
        self, store: Store, title: str, exclude_subtree: str | None = None, allow_root: bool = True
    ) -> None:
        super().__init__()
        self.store = store
        self.picker_title = title
        self.exclude = exclude_subtree
        self.allow_root = allow_root

    def compose(self) -> ComposeResult:
        with _dialog(self.picker_title, "wide"):
            label = "(top level)" if self.allow_root else "[dim]All questlines[/]"
            tree: Tree[str] = Tree(label, data=ROOT_ID, id="picker")
            tree.show_root = True
            tree.guide_depth = 3
            self._fill(tree.root, ROOT_ID)
            tree.root.expand_all()
            yield tree
            yield Label("[dim]enter to choose · esc to cancel[/]")

    def _fill(self, node: TreeNode[str], project_id: str) -> None:
        for cid in self.store.projects[project_id].children:
            if cid == self.exclude:
                continue
            child = node.add(self.store.projects[cid].name, data=cid, allow_expand=True)
            self._fill(child, cid)
            if not child.children:
                child.allow_expand = False

    def on_mount(self) -> None:
        tree = self.query_one(Tree)
        tree.auto_expand = False
        tree.focus()
        if not self.allow_root and tree.root.children:
            tree.move_cursor(tree.root.children[0])

    @on(Tree.NodeSelected)
    def chosen(self, event: Tree.NodeSelected[str]) -> None:
        if event.node.data == ROOT_ID and not self.allow_root:
            self.notify("Choose a questline, not the top level.", severity="warning")
            return
        self.dismiss(event.node.data)

    def action_cancel(self) -> None:
        self.dismiss(None)


class ActionMenu(ModalScreen[str | None]):
    """A small menu of actions. Items are (action, label, key, enabled); returns the action."""

    BINDINGS = [Binding("escape", "cancel", "Cancel")]
    WINDOW_CLASSES: tuple[str, ...] = ("menu",)

    def __init__(
        self, title: str, items: list[tuple[str, str, str, bool]], initial: str | None = None
    ) -> None:
        super().__init__()
        self.menu_title = title
        self.items = items
        self.initial = initial  # action to highlight first

    def compose(self) -> ComposeResult:
        with _dialog(self.menu_title, *self.WINDOW_CLASSES):
            yield from self.compose_details()
            yield OptionList(
                *(
                    Option(Text.assemble(label, (f"  {key}", "dim")), id=action, disabled=not enabled)
                    for action, label, key, enabled in self.items
                ),
                id="menu",
            )

    def compose_details(self) -> ComposeResult:
        """Anything shown above the menu items (nothing for a plain menu)."""
        yield from ()

    def on_mount(self) -> None:
        actions = [action for action, *_ in self.items]
        if self.initial in actions:
            self.query_one("#menu", OptionList).highlighted = actions.index(self.initial)

    def on_key(self, event: events.Key) -> None:
        # the key shown next to an item picks it (case-sensitive: m and M are different items)
        for action, _, key, enabled in self.items:
            if key and enabled and event.character == key:
                event.stop()
                self.dismiss(action)
                return

    def on_click(self, event: events.Click) -> None:
        if event.widget is self:  # clicked outside the menu
            self.dismiss(None)

    @on(OptionList.OptionSelected)
    def chosen(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(event.option.id)

    def action_cancel(self) -> None:
        self.dismiss(None)


class QuestScroll(ActionMenu):
    """A quest's full details on a parchment scroll, with what you can do with it."""

    WINDOW_CLASSES = ("scroll",)

    def __init__(self, title: str, details: Text, items: list[tuple[str, str, str, bool]]) -> None:
        super().__init__(title, items)
        self.details = details

    def compose_details(self) -> ComposeResult:
        yield Static(self.details, id="scroll-text")


class QuestCompleteBanner(ModalScreen[None]):
    """The big 'QUEST COMPLETE' card; closes by itself, or on any key or click."""

    def __init__(self, title: str, gained: int, level_up: tuple[int, str] | None) -> None:
        super().__init__()
        self.quest_title = title
        self.gained = gained
        self.level_up = level_up

    def compose(self) -> ComposeResult:
        with Vertical(id="banner-card"):
            yield Static("✦  QUEST COMPLETE  ✦", id="banner-head")
            yield Static(Text(_one_line(self.quest_title, 44), style="bold"), classes="banner-line")
            yield Static(Text(f"+{self.gained} XP", style=f"bold {BRIGHT}"), classes="banner-line")
            if self.level_up:
                level, title = self.level_up
                yield Static(
                    Text(f"★ LEVEL UP ★  You are now a {title} (Lv {level})", style=f"bold {EMERALD}"),
                    classes="banner-line",
                )

    def on_mount(self) -> None:
        self.set_timer(2.5 if self.level_up else 1.4, self._close)

    def _close(self) -> None:
        if self.is_current:
            self.dismiss(None)

    def on_key(self, event: events.Key) -> None:
        event.stop()
        self._close()

    def on_click(self) -> None:
        self._close()


class HelpScreen(ModalScreen[None]):
    """Every key, since the dashboard has no footer."""

    KEYS = [
        ("Quests", [
            ("a", "post a new quest"), ("space", "complete / reopen"), ("enter", "open the quest scroll"),
            ("e", "edit"), ("p", "cycle priority"), ("] / [", "due a day later / earlier"),
            ("M", "move to another questline"), ("g", "go to its questline"), ("d", "abandon (delete)"),
        ]),
        ("Views", [
            ("t", "today's quests"), ("← / →", "pick a day (in This Week)"), ("/", "filter and search"),
            ("h", "show / hide completed"), ("tab", "next window"),
        ]),
        ("Questlines", [
            ("n", "new questline"), ("r / m", "rename / move"), ("d", "delete (sidebar focused)"),
            ("z", "collapse / expand all"), ("i / x", "import / share"),
        ]),
        ("Everywhere", [("o", "more actions"), ("ctrl+z", "undo"), ("?", "this help"), ("q", "quit")]),
    ]

    def compose(self) -> ComposeResult:
        text = Text()
        for i, (section, keys) in enumerate(self.KEYS):
            if i:
                text.append("\n")
            text.append(f"{section}\n", style=f"bold {BRIGHT}")
            for key, what in keys:
                text.append(f"  {key:<8}", style=f"bold {GOLD}")
                text.append(f"{what}\n")
        with _dialog("? Keys"):
            yield Static(text)
            yield Label("[dim]any key to close[/]")

    def on_key(self, event: events.Key) -> None:
        event.stop()
        self.dismiss(None)

    def on_click(self) -> None:
        self.dismiss(None)


class ShareCodeDialog(ModalScreen[None]):
    """Show a questline's share code (already copied to the clipboard)."""

    BINDINGS = [Binding("escape", "close", "Close")]

    def __init__(self, project_name: str, code: str) -> None:
        super().__init__()
        self.project_name = project_name
        self.code = code

    def compose(self) -> ComposeResult:
        with _dialog(f"⇪ Share {self.project_name}", "wide"):
            yield Static(
                "Copied to the clipboard. Send this code to a fellow adventurer; they add it with "
                "[b]Import[/b] (i). If your terminal didn't copy it, select it below."
            )
            yield TextArea(self.code, read_only=True, soft_wrap=True, id="code", classes="notes")
            with Horizontal(classes="buttons"):
                yield Button("Close", variant="primary", id="close")

    def on_mount(self) -> None:
        self.query_one("#close", Button).focus()

    @on(Button.Pressed, "#close")
    def action_close(self) -> None:
        self.dismiss(None)


class ImportDialog(ModalScreen[str | None]):
    """Paste a share code; returns it once it decodes."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("ctrl+s", "import", "Import", priority=True),
    ]

    def __init__(self, where: str) -> None:
        super().__init__()
        self.where = where

    def compose(self) -> ComposeResult:
        with _dialog(f"⇩ Import into {self.where}", "wide"):
            yield Static("Paste a share code (ctrl+shift+v or right-click → paste in most terminals)")
            yield TextArea(soft_wrap=True, id="code", classes="notes")
            yield Label("[dim]ctrl+s to import · esc to cancel[/]")
            with Horizontal(classes="buttons"):
                yield Button("Import", variant="primary", id="import")
                yield Button("Cancel", id="cancel")

    @on(Button.Pressed, "#import")
    def action_import(self) -> None:
        code = self.query_one("#code", TextArea).text
        if not _rejected(self, Store.decode_share_code, code):
            self.dismiss(code)

    @on(Button.Pressed, "#cancel")
    def action_cancel(self) -> None:
        self.dismiss(None)


# ---------------------------------------------------------------- dashboard windows


class QuitButton(Static):
    """✕ at the right end of the banner."""

    DEFAULT_CSS = """
    QuitButton { width: auto; padding: 0 1; }
    QuitButton:hover { background: $error; }
    """

    def __init__(self) -> None:
        super().__init__("✕")
        self.tooltip = "Quit (q)"

    def on_click(self) -> None:
        self.app.exit()


class Banner(Horizontal):
    """⚔ QUESTLOG and the date; your title, level, XP bar and streak."""

    DEFAULT_CSS = """
    Banner {
        dock: top; height: 3; border: double $primary; background: $panel; padding: 0 1;
    }
    Banner #brand { width: auto; color: $accent; text-style: bold; }
    Banner #today { width: auto; color: $text-muted; margin-left: 2; }
    Banner #stats { width: 1fr; content-align: right middle; }
    Screen.-narrow Banner #today { display: none; }
    """

    def compose(self) -> ComposeResult:
        yield Static("⚔ QUESTLOG", id="brand")
        yield Static(date.today().strftime("%a %-d %b"), id="today")
        yield Static(id="stats")
        yield QuitButton()

    _stats = (0, 0)

    def on_resize(self) -> None:
        self.show_stats(*self._stats)  # wide and narrow banners differ

    def show_stats(self, xp: int, days: int) -> None:
        self._stats = (xp, days)
        level, into, need = level_info(xp)
        self.query_one("#brand", Static).update("⚔ QUESTLOG" if self.size.width < 100 else "⚔ Q U E S T L O G")
        if self.size.width < 100:  # compact: title, level, bar, streak
            stats = Text.assemble(
                (title_for_level(level), f"bold {GOLD}"), (" Lv", MUTED), (str(level), f"bold {BRIGHT}"), " ",
                _bar(into, need, 6, "▰", "▱", BRIGHT), (f" {into}/{need}", MUTED),
            )
            if days:
                stats.append(f" ✹{days}d", style=f"bold {BRIGHT}")
            self.query_one("#stats", Static).update(stats)
            return
        stats = Text.assemble(
            (title_for_level(level), f"bold {GOLD}"), (" · Lv ", MUTED), (str(level), f"bold {BRIGHT}"), "  ",
            _bar(into, need, 10, "▰", "▱", BRIGHT), (f" {into}/{need} XP", MUTED),
        )
        if days:
            stats.append(f"  ✹ {days}-day streak", style=f"bold {BRIGHT}")
        self.query_one("#stats", Static).update(stats)


def _set_window_title(widget: Widget, title: str, focused: bool | None = None) -> None:
    """Window titles get a ▶ while the window has focus, like a game cursor."""
    widget.window_title = title  # type: ignore[attr-defined]
    if focused is None:
        focused = widget.has_focus
    widget.border_title = f"▶ {title}" if focused else title


class Window:
    """Mixin for focusable dashboard windows: keeps the ▶ in the title in sync with focus."""

    def on_focus(self) -> None:
        _set_window_title(self, getattr(self, "window_title", ""), True)  # type: ignore[arg-type]

    def on_blur(self) -> None:
        _set_window_title(self, getattr(self, "window_title", ""), False)  # type: ignore[arg-type]


class ProjectTree(Window, Tree[str]):
    """Questline sidebar; right-clicking a questline opens its options menu."""

    def _on_click(self, event: events.Click) -> None:
        if event.button == 3:
            event.prevent_default()
            if "line" in event.style.meta:
                self.cursor_line = event.style.meta["line"]
            self.focus()
            self.call_after_refresh(self.app.action_options)


class TaskList(Window, OptionList):
    """The main quest window; right-clicking a quest opens its options menu."""

    def _on_click(self, event: events.Click) -> None:
        if event.button == 3:
            event.prevent_default()
            index = event.style.meta.get("option")
            if index is not None and not self.get_option_at_index(index).disabled:
                self.highlighted = index
            self.focus()
            self.call_after_refresh(self.app.action_options)


class CommandMenu(Window, OptionList):
    """The JRPG command window. Items are (action, label, key); the app decides which are enabled."""

    def show_commands(self, items: list[tuple[str, str, str, bool]]) -> None:
        keep = self.highlighted
        self._labels: list[tuple[str, str]] = []  # cleared first: clear_options fires watch_highlighted
        self.clear_options()
        self.add_options(Option(self._prompt(label, key, False), id=action, disabled=not enabled)
                         for action, label, key, enabled in items)
        self._labels = [(label, key) for _, label, key, _ in items]
        self.highlighted = keep if keep is not None and keep < len(items) else 0
        self._mark_cursor()

    @staticmethod
    def _prompt(label: str, key: str, cursor: bool) -> Text:
        return Text.assemble(("▶ " if cursor else "  ", f"bold {BRIGHT}"), f"{label:<14}", (key, MUTED))

    def _mark_cursor(self) -> None:
        """Draw the ▶ next to the highlighted command while the window has focus."""
        labels = getattr(self, "_labels", [])
        if len(labels) != self.option_count:
            return
        for i, (label, key) in enumerate(labels):
            self.replace_option_prompt_at_index(i, self._prompt(label, key, self.has_focus and i == self.highlighted))

    def watch_highlighted(self, highlighted: int | None) -> None:
        super().watch_highlighted(highlighted)
        self._mark_cursor()

    # Textual runs the on_focus / on_blur of every class in the MRO, so Window's still updates the title
    def on_focus(self) -> None:
        self._mark_cursor()

    def on_blur(self) -> None:
        self._mark_cursor()


class WeekStrip(Window, Widget, can_focus=True):
    """Seven day cards, today first. ←/→ pick a day, enter (or a click) shows its quests."""

    BINDINGS = [
        Binding("left", "move(-1)", "Previous day", show=False),
        Binding("right", "move(1)", "Next day", show=False),
        Binding("enter", "choose", "Show day", show=False),
    ]


    class DayChosen(Message):
        def __init__(self, day: date) -> None:
            super().__init__()
            self.day = day

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.days: list[tuple[date, list[Task]]] = []
        self.late = 0  # overdue quests, shown on today's card
        self.selected = 0
        self.active = False  # whether the main window shows the selected day

    def show_week(self, days: list[tuple[date, list[Task]]], late: int, active_day: date | None) -> None:
        self.days, self.late = days, late
        self.active = active_day is not None
        if active_day is not None:
            self.selected = next((i for i, (d, _) in enumerate(days) if d == active_day), self.selected)
        self.refresh()

    def _col_width(self) -> int:
        return max(3, (self.content_size.width - 6) // 7)

    def action_move(self, step: int) -> None:
        if self.days:
            self.selected = (self.selected + step) % len(self.days)
            self.refresh()

    def action_choose(self) -> None:
        if self.days:
            self.post_message(self.DayChosen(self.days[self.selected][0]))

    def on_click(self, event: events.Click) -> None:
        offset = event.get_content_offset(self)
        if offset is None or not self.days:
            return
        self.selected = min(offset.x // (self._col_width() + 1), len(self.days) - 1)
        self.refresh()
        self.action_choose()

    def on_focus(self) -> None:
        self.refresh()

    def on_blur(self) -> None:
        self.refresh()

    def _cell(self, index: int, day: date, tasks: list[Task], width: int, height: int) -> list[Text]:
        """One day card: a header, then its quests (or gems when narrow), then a footer line."""
        is_today = index == 0
        compact = width < 11
        head_style = f"bold {BRIGHT}" if is_today else "bold"
        if index == self.selected and (self.has_focus or self.active):
            head_style = f"bold reverse {BRIGHT}" if self.has_focus else f"bold underline {BRIGHT}"
        if is_today:
            head = "TODAY"
        elif compact:
            head = day.strftime("%a")[:2] + f"{day.day:>3}"
        else:
            head = day.strftime("%a %-d")
        lines = [Text(head, style=head_style)]
        tasks = sort_tasks(tasks)
        room = height - 2  # rows between header and footer
        footer = Text()
        if compact:
            gems = Text()
            for t in tasks:
                mark, style = _gem(t)
                gems.append(mark[0], style=style)
            for i in range(0, len(gems), width):
                lines.append(gems[i:i + width])
            if not tasks:
                lines.append(Text(" ·", style=MUTED))
            lines = lines[: 1 + room]
        else:
            shown = tasks
            if len(tasks) > room:
                shown = tasks[:room]
                footer.append(f"+{len(tasks) - room} more ", style=MUTED)
            for t in shown:
                mark, style = _gem(t)
                lines.append(Text.assemble((mark, style), (t.title, "dim strike" if t.done else "")))
            if not tasks:
                lines.append(Text(" ·", style=MUTED))
        if is_today and self.late:
            footer.append(f"⚠{self.late}" + ("" if compact else " late"), style=f"bold {CRIMSON}")
        while len(lines) < height - 1:
            lines.append(Text())
        lines.append(footer)
        return [_fit(line, width) for line in lines[:height]]

    def render(self) -> Text:
        if not self.days:
            return Text()
        width, height = self._col_width(), max(4, self.content_size.height)
        rows = [Text() for _ in range(height)]
        for i, (day, tasks) in enumerate(self.days):
            for row, cell in zip(rows, self._cell(i, day, tasks, width, height)):
                if i:
                    row.append("│", style=GOLD)
                row.append_text(cell)
        return Text("\n").join(rows)


# ---------------------------------------------------------------- main


class QuestlogApp(App[None]):
    TITLE = "Questlog"
    HORIZONTAL_BREAKPOINTS = [(0, "-narrow"), (120, "-wide")]
    CSS = """
    Screen { background: $background; }
    #main { height: 1fr; }

    /* every dashboard window: navy body, double gold frame, brighter when focused */
    .window {
        border: double $primary; background: $surface;
        border-title-color: $primary; border-title-style: bold;
        border-subtitle-color: $text-muted;
    }
    .window:focus {
        border: double $accent; border-title-color: $background; border-title-background: $accent;
    }

    #left { width: 26; }
    Screen.-narrow #left { width: 22; }
    #projects { height: 1fr; padding: 0 1; }
    #commands { height: auto; max-height: 12; padding: 0; }
    #commands > .option-list--option-highlighted { background: $surface; text-style: bold; }
    #commands:focus > .option-list--option-highlighted { background: $panel; color: $accent; }

    #right { width: 1fr; }
    #tasks {
        height: 3fr; min-height: 5; padding: 0 1;
        text-wrap: nowrap; text-overflow: ellipsis;
    }
    #tasks > .option-list--option-highlighted { background: $secondary 50%; }
    #tasks:focus > .option-list--option-highlighted { background: $secondary; }
    #week { height: 2fr; min-height: 7; max-height: 16; padding: 0 1; }

    #hints { height: 1; padding: 0 1; color: $text-muted; }

    /* ---- dialogs: the same game windows, raised over a dimmed dashboard */
    ModalScreen { align: center middle; background: $background 70%; }
    .dialog {
        width: 60; max-width: 100%; height: auto; max-height: 95%;
        padding: 1 2; border: double $accent; background: $panel; overflow-y: auto;
        border-title-color: $accent; border-title-style: bold;
    }
    .dialog.wide { width: 80; }
    .dialog.menu { width: 46; padding: 0 1; }
    .dialog.scroll {
        width: 64; padding: 1 2; background: #e9dcb8; color: #2a1f10;
        border: double #8c6a2b; border-title-color: #5a3d14;
    }
    .dialog.scroll #menu { background: #e9dcb8; color: #2a1f10; margin-top: 1; }
    .dialog.scroll #menu > .option-list--option-highlighted { background: #c9a85c; color: #1a1208; }
    #menu { height: auto; max-height: 20; border: none; padding: 0; background: $panel; }
    .dialog-text { width: 100%; margin-bottom: 1; }
    .dialog #picker { height: auto; max-height: 20; background: $panel; }
    .notes { height: 5; }
    .buttons { height: auto; margin-top: 1; align-horizontal: right; }
    .buttons Button { margin-left: 1; }
    .location-row { height: auto; margin-top: 1; }
    .location { width: 1fr; height: 3; content-align: left middle; }
    #due-row { height: auto; margin-top: 1; }
    #due-col { width: 1fr; height: auto; }
    #prio-col { width: 20; height: auto; margin-left: 2; }
    #filter-grid { grid-size: 2; grid-gutter: 0 2; height: auto; margin-top: 1; }
    #filter-grid Vertical { height: auto; }
    .switch-row { height: auto; margin-top: 1; }
    .switch-row Label { height: 3; content-align: left middle; }

    QuestCompleteBanner { background: $background 40%; }
    #banner-card {
        width: 54; height: auto; padding: 1 2; border: double $accent; background: $panel;
    }
    #banner-head {
        width: 100%; content-align: center middle; text-style: bold; color: $background;
        background: $accent; margin-bottom: 1;
    }
    .banner-line { width: 100%; content-align: center middle; }
    """

    # no footer: the command window, the hint line and `?` show the keys
    BINDINGS = [
        Binding("a", "add_task", "New quest"),
        Binding("n", "create_project", "New questline"),
        Binding("space", "toggle_done", "Complete"),
        Binding("p", "cycle_priority", "Priority"),
        Binding("t", "goto_today", "Today"),
        Binding("slash,F", "filter", "Filter"),
        Binding("h", "toggle_show_done", "Show/hide done"),
        Binding("o", "options", "More"),
        Binding("z", "toggle_projects", "Collapse/expand"),
        Binding("i", "import_project", "Import"),
        Binding("x", "export_project", "Share"),
        Binding("right_square_bracket", "postpone(1)", "Later"),
        Binding("left_square_bracket", "postpone(-1)", "Earlier"),
        Binding("r", "rename_project", "Rename"),
        Binding("m", "move_project", "Move questline"),
        Binding("e", "edit_task", "Edit quest"),
        Binding("M", "move_task", "Move quest"),
        Binding("g", "goto_project", "Go to questline"),
        Binding("d", "delete", "Delete"),
        Binding("ctrl+z", "undo", "Undo"),
        Binding("question_mark", "help", "Keys"),
        Binding("q", "quit", "Quit"),
    ]

    # main-screen actions (everything bound above except quit)
    MAIN_ACTIONS = frozenset(b.action.split("(")[0] for b in BINDINGS if b.action != "quit")

    # actions that need a highlighted quest
    QUEST_ACTIONS = frozenset({
        "toggle_done", "cycle_priority", "set_priority", "postpone", "edit_task", "move_task",
        "goto_project", "clear_due", "open_scroll",
    })

    def __init__(self, store: Store) -> None:
        super().__init__()
        self.store = store
        self._saved_expanded = set(store.expanded)
        self.filter = Filter()
        self.view = TODAY_VIEW  # what the main quest window shows
        self.last_project: str | None = None  # where quests posted from Today / a day go by default
        self.last_window = "quests"  # "map" or "quests": what `o` and `d` act on
        self._undo: Callable[[], object] | None = None  # takes back the last delete / completion
        self._undo_label = ""
        self._all_node: TreeNode[str] | None = None

    # ----- layout

    def compose(self) -> ComposeResult:
        yield Banner()
        with Horizontal(id="main"):
            with Vertical(id="left"):
                tree = ProjectTree("", id="projects", classes="window")
                tree.show_root = False
                tree.auto_expand = False
                tree.guide_depth = 2
                _set_window_title(tree, "Questlines")
                yield tree
                commands = CommandMenu(id="commands", classes="window")
                _set_window_title(commands, "Command")
                yield commands
            with Vertical(id="right"):
                tasks = TaskList(id="tasks", classes="window")
                _set_window_title(tasks, VIEW_NAMES[TODAY_VIEW])
                yield tasks
                week = WeekStrip(id="week", classes="window")
                _set_window_title(week, "◷ This Week")
                yield week
        yield Static(id="hints")

    def on_mount(self) -> None:
        self.register_theme(QUEST_THEME)
        self.theme = "questlog"
        self.refresh_tree(TODAY_VIEW, expanded=self.store.expanded)
        self.task_list.focus()

    def on_unmount(self) -> None:
        # store.expanded follows the tree in memory; it's written once here, not on every toggle
        if self.store.expanded != self._saved_expanded:
            self.store.save()

    # ----- helpers

    @property
    def tree(self) -> Tree[str]:
        return self.query_one("#projects", Tree)

    @property
    def task_list(self) -> OptionList:
        return self.query_one("#tasks", OptionList)

    @property
    def week(self) -> WeekStrip:
        return self.query_one("#week", WeekStrip)

    def _valid_view(self, view: str) -> bool:
        return view in VIEW_NAMES or view in self.store.projects or view.startswith(DAY_PREFIX)

    def selected_view(self) -> str:
        return self.view

    def selected_project(self) -> str:
        """The questline the main window shows, or ROOT_ID for Today / All / a day."""
        return self.view if self.view in self.store.projects else ROOT_ID

    def selected_task(self) -> Task | None:
        ol = self.task_list
        if ol.highlighted is None or ol.option_count == 0:
            return None
        return self.store.tasks.get(ol.get_option_at_index(ol.highlighted).id)

    @staticmethod
    def _anchor(view: str) -> str:
        """The sidebar entry that stands for a view (days are reached from Today)."""
        return TODAY_VIEW if view.startswith(DAY_PREFIX) else view

    @staticmethod
    def _count_label(name: str, open_: int, overdue: int, bold: bool = False) -> Text:
        label = Text(name, style="bold" if bold else "")
        if open_:
            label.append(f" {open_}", style=MUTED)
        if overdue:
            label.append(f" ⚠{overdue}", style=f"bold {CRIMSON}")
        return label

    def _label(self, view: str) -> Text:
        if view == TODAY_VIEW:
            tasks = [t for t in self.store.today_tasks() if not t.done]
            return self._count_label(TREE_NAMES[view], len(tasks), sum(t.overdue() for t in tasks), True)
        open_, overdue = self.store.counts(view)
        name = TREE_NAMES[ROOT_ID] if view == ROOT_ID else self.store.projects[view].name
        return self._count_label(name, open_, overdue, view == ROOT_ID)

    def _project_nodes(self) -> list[TreeNode[str]]:
        return list(self._all_node.children) if self._all_node is not None else []

    def _expanded_ids(self) -> set[str]:
        """Ids of the open questlines below "All quests"."""
        expanded = set()
        stack = self._project_nodes()
        while stack:
            node = stack.pop()
            if node.is_expanded and node.children:
                expanded.add(node.data)
            stack.extend(node.children)
        return expanded

    def refresh_tree(
        self,
        select_id: str | None = None,
        select_task: str | None = None,
        expanded: set[str] | None = None,
    ) -> None:
        """Rebuild the sidebar (keeping open questlines open), switch to `select_id` and redraw."""
        tree = self.tree
        view = select_id or self.view
        self.view = view if self._valid_view(view) else ROOT_ID
        anchor = self._anchor(self.view)
        expanded = set(self._expanded_ids() if expanded is None else expanded)
        # make sure the selected questline is visible
        cur = self.store.projects.get(anchor)
        while cur is not None and cur.parent_id is not None:
            expanded.add(cur.parent_id)
            cur = self.store.projects.get(cur.parent_id)

        tree.clear()
        today = tree.root.add_leaf(self._label(TODAY_VIEW), data=TODAY_VIEW)
        self._all_node = all_node = tree.root.add(self._label(ROOT_ID), data=ROOT_ID, allow_expand=False)
        target = today if anchor == TODAY_VIEW else all_node

        def build(node: TreeNode[str], project_id: str) -> None:
            nonlocal target
            for cid in self.store.projects[project_id].children:
                p = self.store.projects[cid]
                child = node.add(self._label(cid), data=cid, allow_expand=bool(p.children))
                if cid == anchor:
                    target = child
                build(child, cid)
                if cid in expanded:
                    child.expand()

        build(all_node, ROOT_ID)
        all_node.expand()
        tree.root.expand()
        self.call_after_refresh(tree.move_cursor, target)
        self.refresh_main(select_task)

    def _base_tasks(self, view: str) -> list[Task]:
        """The quests a view covers before the filter."""
        if view == TODAY_VIEW:
            return self.store.today_tasks()
        if view.startswith(DAY_PREFIX):
            return self.store.tasks_due_on(date.fromisoformat(view.removeprefix(DAY_PREFIX)))
        return self.store.tasks_in(view, recursive=self.filter.recursive or view == ROOT_ID)

    def _visible_tasks(self, view: str) -> list[Task]:
        f = self.filter
        if view == TODAY_VIEW or view.startswith(DAY_PREFIX):  # days keep their completed quests
            f = replace(f, show_done=True)
        return f.apply(self._base_tasks(view))

    def _show_paths(self, view: str) -> bool:
        return view in VIEW_NAMES or view.startswith(DAY_PREFIX) or self.filter.recursive

    def _view_title(self, view: str) -> str:
        if view.startswith(DAY_PREFIX):
            return date.fromisoformat(view.removeprefix(DAY_PREFIX)).strftime("◷ %A, %-d %B")
        return VIEW_NAMES.get(view) or "⚑ " + self.store.path_of(view)

    def refresh_main(self, select_task: str | None = None) -> None:
        """Redraw everything right of the sidebar, plus the banner, commands and hints."""
        view = self.view
        if not self._valid_view(view):
            view = self.view = ROOT_ID
        today = date.today()
        base = self._base_tasks(view)
        tasks = self._visible_tasks(view)

        # quest window: rows, title, progress in the bottom frame
        ol = self.task_list
        previous = ol.highlighted
        ol.clear_options()
        paths = self._show_paths(view)
        if tasks:
            ol.add_options(self._task_option(t, today, paths) for t in tasks)
            ids = [t.id for t in tasks]
            if select_task in ids:
                ol.highlighted = ids.index(select_task)
            else:
                ol.highlighted = min(previous or 0, len(ids) - 1)
        else:
            if base and len(base) != len(tasks):
                hint = "No quests match — / to change the filter, h to show completed"
            elif view == TODAY_VIEW:
                hint = "No quests due today. Rest at the inn."
            elif view.startswith(DAY_PREFIX):
                hint = "Nothing due that day. The road is clear."
            elif view == ROOT_ID and not self.store.projects[ROOT_ID].children:
                hint = "Your quest log is empty — press n to start a questline"
            else:
                hint = "No quests here yet — press a to post one"
            ol.add_option(Option(Text(hint, style=f"italic {MUTED}"), disabled=True))
        _set_window_title(ol, self._view_title(view))
        n_done = sum(t.done for t in base)
        progress = Text.assemble(
            _bar(n_done, len(base), 10, "━", "─", EMERALD), (f" {n_done}/{len(base)} done", MUTED)
        )
        extras = []
        if summary := self.filter.summary():
            extras.append(f"[{BRIGHT}]⧩ {summary}[/]")
        hidden = sum(t.done for t in base) - sum(t.done for t in tasks)
        if hidden:
            extras.append(f"{hidden} done hidden")
        ol.border_subtitle = " · ".join(extras + [progress.markup])

        # the week ahead
        days = [today + timedelta(days=i) for i in range(7)]
        late = sum(t.overdue(today) for t in self.store.tasks.values())
        active = date.fromisoformat(view.removeprefix(DAY_PREFIX)) if view.startswith(DAY_PREFIX) else None
        self.week.show_week([(d, self.store.tasks_due_on(d)) for d in days], late, active)

        self.query_one(Banner).show_stats(self.store.xp, streak(self.store.active_days))
        self.refresh_commands()

    def _task_option(self, t: Task, today: date, show_path: bool) -> Option:
        mark, style = _gem(t)
        text = Text.assemble(
            (mark, style),
            (_one_line(t.title, 50), "dim strike" if t.done else ("bold" if t.priority == 3 else "")),
        )
        due, due_style = _due_label(t, today)
        if due:
            text.append(f"  {due}", style=due_style)
        if t.notes:
            text.append("  ✎", style=MUTED)
        if show_path:
            text.append(f"  {self.store.path_of(t.project_id)}", style=f"italic {MUTED}")
        return Option(text, id=t.id)

    # ----- command window and hints

    def command_enabled(self, action: str) -> bool:
        """Whether an action makes sense right now (drives the command window and the keys)."""
        if action in self.QUEST_ACTIONS:
            return self.selected_task() is not None
        if action == "add_task":
            return bool(self.store.projects[ROOT_ID].children)
        if action == "undo":
            return self._undo is not None
        return True

    def refresh_commands(self) -> None:
        task = self.selected_task()
        done_label = "Reopen" if task is not None and task.done else "Complete"
        items = [
            ("add_task", "New quest", "a"),
            ("toggle_done", done_label, "spc"),
            ("set_priority", "Priority…", "p"),
            ("postpone", "Postpone", "]"),
            ("filter", "Filter…", "/"),
            ("toggle_show_done", "Hide done" if self.filter.show_done else "Show done", "h"),
            ("create_project", "New questline", "n"),
            ("options", "More…", "o"),
        ]
        self.query_one("#commands", CommandMenu).show_commands(
            [(action, label, key, self.command_enabled(action)) for action, label, key in items]
        )
        parts = ["tab next window", "enter open", "←→ day", "? keys"]
        if self._undo is not None:
            parts.append("ctrl+z undo")
        parts.append("q quit")
        self.query_one("#hints", Static).update("  ·  ".join(parts))

    @on(OptionList.OptionSelected, "#commands")
    def command_chosen(self, event: OptionList.OptionSelected) -> None:
        self._run(event.option.id)

    def _run(self, action: str | None) -> None:
        """Run a menu choice by its action name."""
        if action == "postpone":
            self.action_postpone(1)
        elif action is not None:
            getattr(self, f"action_{action}")()

    # ----- events

    @on(Tree.NodeHighlighted, "#projects")
    def project_highlighted(self, event: Tree.NodeHighlighted[str]) -> None:
        # programmatic moves land on the view's own entry and change nothing
        if event.node.data is None or event.node.data == self._anchor(self.view):
            return
        self.view = event.node.data
        self.refresh_main()

    @on(Tree.NodeSelected, "#projects")
    def project_selected(self, event: Tree.NodeSelected[str]) -> None:
        if event.node.data is not None:
            self.view = event.node.data
            self.refresh_main()
            self.task_list.focus()

    @on(Tree.NodeExpanded, "#projects")
    @on(Tree.NodeCollapsed, "#projects")
    def project_toggled(self) -> None:
        self.store.expanded = self._expanded_ids()

    @on(OptionList.OptionHighlighted, "#tasks")
    def task_highlighted(self) -> None:
        self.refresh_commands()

    @on(OptionList.OptionSelected, "#tasks")
    def task_selected(self) -> None:
        self.action_open_scroll()

    @on(WeekStrip.DayChosen)
    def day_chosen(self, event: WeekStrip.DayChosen) -> None:
        self.view = TODAY_VIEW if event.day == date.today() else _day_view(event.day)
        self.refresh_tree()
        self.task_list.focus()

    def on_descendant_focus(self, event: events.DescendantFocus) -> None:
        if event.widget is self.tree:
            self.last_window = "map"
        elif event.widget in (self.task_list, self.week):
            self.last_window = "quests"

    # ----- view actions

    def action_toggle_projects(self) -> None:
        nodes = self._project_nodes()
        if not any(node.children for node in nodes):
            return
        if self._expanded_ids():
            for child in nodes:
                child.collapse_all()
            self.refresh_tree()
        else:
            self._all_node.expand_all()

    def action_goto_today(self) -> None:
        self.refresh_tree(TODAY_VIEW)
        self.task_list.focus()

    def action_goto_project(self) -> None:
        task = self.selected_task()
        if task is not None:
            self.refresh_tree(task.project_id, select_task=task.id)

    def action_filter(self) -> None:
        def done(result: Filter | None) -> None:
            if result is not None:
                self.filter = result
                self.refresh_main()

        self.push_screen(FilterDialog(self.filter, focus_search=True), done)

    def action_toggle_show_done(self) -> None:
        task = self.selected_task()
        self.filter = replace(self.filter, show_done=not self.filter.show_done)
        self.refresh_main(select_task=task.id if task else None)

    def action_help(self) -> None:
        self.push_screen(HelpScreen())

    # ----- questline actions

    def action_create_project(self) -> None:
        def done(result: tuple[str, str] | None) -> None:
            if result is not None and (project := self._try(self.store.create_project, *result)):
                self.refresh_tree(project.id)

        self.push_screen(CreateProjectDialog(self.store, self.selected_project()), done)

    def action_rename_project(self) -> None:
        pid = self.selected_project()
        if pid == ROOT_ID:
            self.notify("Select a questline to rename.", severity="warning")
            return

        def done(name: str | None) -> None:
            if name is not None and self._try(self.store.rename_project, pid, name):
                self.refresh_tree(pid)

        project = self.store.projects[pid]
        check = lambda name: self.store.check_name(project.parent_id, name, exclude=pid)  # noqa: E731
        self.push_screen(TextPrompt("✎ Rename questline", project.name, check), done)

    def action_move_project(self) -> None:
        pid = self.selected_project()
        if pid == ROOT_ID:
            self.notify("Select a questline to move.", severity="warning")
            return

        def done(dest: str | None) -> None:
            if dest is not None and self._try(self.store.move_project, pid, dest):
                self.refresh_tree(pid)
                self.notify(f"Moved to {self.store.path_of(pid)}")

        name = self.store.projects[pid].name
        self.push_screen(
            ProjectPicker(self.store, f"Move '{name}' into…", exclude_subtree=pid), done
        )

    # ----- quest actions

    def _try(self, fn, *args):
        """Run a store operation; show its error and return None if it fails.

        Operations that return nothing give True, so success is always truthy.
        """
        try:
            result = fn(*args)
            return True if result is None else result
        except StoreError as exc:
            self.notify(str(exc), severity="error")
            return None

    def action_add_task(self) -> None:
        if not self.store.projects[ROOT_ID].children:
            self.notify("Start a questline first (n).", severity="warning")
            return
        view = self.view
        pid: str | None = view if view in self.store.projects and view != ROOT_ID else None
        if pid is None and self.last_project in self.store.projects:
            pid = self.last_project
        due = "today" if view == TODAY_VIEW else view.removeprefix(DAY_PREFIX) if view.startswith(DAY_PREFIX) else ""
        where = self._view_title(view)

        def done(result: dict | None) -> None:
            if result is None:
                return
            task = self._try(
                self.store.add_task, result["project_id"], result["title"], result["notes"],
                result["due"], result["priority"],
            )
            if task:
                self.last_project = task.project_id
                self.refresh_tree(select_task=task.id)
                self.notify(f"Quest posted to {self.store.path_of(task.project_id)}", timeout=2)

        self.push_screen(TaskForm(self.store, f"✦ New quest · {where}", pid, due=due), done)

    def action_edit_task(self) -> None:
        task = self.selected_task()
        if task is None:
            self.notify("Highlight a quest first.", severity="warning")
            return

        def done(result: dict | None) -> None:
            if result is None:
                return
            ok = self._try(
                self.store.edit_task, task.id, result["title"], result["notes"], result["due"],
                result["priority"],
            )
            if ok and result["project_id"] != task.project_id:
                ok = self._try(self.store.move_task, task.id, result["project_id"])
            if ok:
                self.refresh_tree(select_task=task.id)

        self.push_screen(TaskForm(self.store, "✎ Edit quest", task.project_id, task), done)

    def action_open_scroll(self) -> None:
        """The quest's full details on a scroll, with its commands."""
        task = self.selected_task()
        if task is None:
            return
        today = date.today()
        mark, style = _gem(task)
        reward = f"earned {task.xp} XP" if task.done else f"reward +{XP_REWARD[task.priority]} XP"
        due = Text("no deadline", style="italic")
        if task.due:
            label, _ = _due_label(task, today)
            due = Text.assemble(
                date.fromisoformat(task.due).strftime("%A, %-d %B %Y"),
                (f"  {label}", "bold #a3271b" if "late" in label else "bold") if label else "",
            )
        status = f"completed on {task.done_on}" if task.done else "in progress"
        ink = "#6b5a3c"
        details = Text.assemble(
            (mark, style), (task.title, "bold"), "\n\n",
            ("Priority   ", ink), (PRIORITIES[task.priority], "bold"), ("  ·  ", ink), (reward, "bold #8c5a00"), "\n",
            ("Due        ", ink), due, "\n",
            ("Questline  ", ink), self.store.path_of(task.project_id), "\n",
            ("Status     ", ink), status, (f"  ·  posted {task.created}", ink),
        )
        if task.notes:
            details.append("\n" + "─" * 40 + "\n", style=ink)
            details.append(task.notes)
        items = [
            ("toggle_done", "↺ Reopen" if task.done else "✔ Complete", "c", True),
            ("edit_task", "✎ Edit", "e", True),
            ("set_priority", "◆ Priority…", "p", True),
            ("postpone", "→ Postpone a day", "]", True),
            ("move_task", "⇢ Move to…", "M", True),
            ("goto_project", "↗ Go to its questline", "g", self.view != task.project_id),
            ("delete_task", "✕ Abandon", "d", True),
            ("close", "Close", "", True),
        ]

        def done(action: str | None) -> None:
            if action != "close":
                self._run(action)

        self.push_screen(QuestScroll("❖ Quest Scroll", details, items), done)

    def action_toggle_done(self) -> None:
        task = self.selected_task()
        if task is None:
            return
        # snapshot everything completing touches, so ctrl+z restores it exactly
        before = (task.done, task.done_on, task.xp, self.store.xp, set(self.store.active_days))
        level_before = level_info(self.store.xp)[0]
        gained = self.store.set_done(task.id, not task.done)

        def undo() -> None:
            task.done, task.done_on, task.xp, self.store.xp, self.store.active_days = before
            self.store.save()
            self.refresh_tree(select_task=task.id)

        title = _one_line(task.title, 30)
        self._remember_undo(f"{'completed' if task.done else 'reopened'} '{title}'", undo)
        self.refresh_tree(select_task=task.id)
        if task.done:
            level = level_info(self.store.xp)[0]
            level_up = (level, title_for_level(level)) if level > level_before else None
            self.push_screen(QuestCompleteBanner(task.title, gained, level_up))
        else:
            self.notify(f"Quest reopened: {title}  ({gained} XP) — ctrl+z to undo")

    def action_cycle_priority(self) -> None:
        task = self.selected_task()
        if task is None:
            return
        self.store.set_priority(task.id, (task.priority + 1) % len(PRIORITIES))
        self.refresh_tree(select_task=task.id)

    def action_set_priority(self) -> None:
        task = self.selected_task()
        if task is None:
            return

        def done(choice: str | None) -> None:
            if choice is not None:
                self.store.set_priority(task.id, int(choice.removeprefix("prio-")))
                self.refresh_tree(select_task=task.id)

        items = [
            (f"prio-{i}", f"◆ {name}  (+{XP_REWARD[i]} XP)", str(i), True)
            for i, name in enumerate(PRIORITIES)
        ]
        self.push_screen(ActionMenu("◆ Priority", items, initial=f"prio-{task.priority}"), done)

    def action_postpone(self, days: int) -> None:
        task = self.selected_task()
        if task is None:
            return
        self.store.shift_due(task.id, days)
        self.refresh_tree(select_task=task.id)
        self.notify(f"Due {describe_due(task.due)} ({task.due})", timeout=2)

    def action_clear_due(self) -> None:
        task = self.selected_task()
        if task is not None:
            self.store.edit_task(task.id, task.title, task.notes, None, task.priority)
            self.refresh_tree(select_task=task.id)

    def action_move_task(self) -> None:
        task = self.selected_task()
        if task is None:
            self.notify("Highlight a quest first.", severity="warning")
            return

        def done(dest: str | None) -> None:
            if dest is not None and self._try(self.store.move_task, task.id, dest):
                self.refresh_tree(select_task=task.id)
                self.notify(f"Quest moved to {self.store.path_of(dest)}")

        self.push_screen(ProjectPicker(self.store, "Move the quest into…", allow_root=False), done)

    def action_delete(self) -> None:
        """`d` abandons the highlighted quest, or deletes the questline when the sidebar was used last."""
        if self.last_window == "map":
            self.action_delete_project()
        else:
            self.action_delete_task()

    def action_delete_task(self) -> None:
        task = self.selected_task()
        if task is None:
            self.notify("Highlight a quest to abandon.", severity="warning")
            return

        def done(ok: bool | None) -> None:
            if ok:
                removed = self.store.delete_task(task.id)

                def undo() -> None:
                    self.store.restore_task(*removed)
                    self.refresh_tree(select_task=task.id)

                label = f"quest '{_one_line(task.title, 30)}'"
                self._remember_undo(f"abandoned {label}", undo)
                self.notify(f"Abandoned {label} — ctrl+z to undo")
                self.refresh_tree()

        self.push_screen(Confirm(f"Abandon the quest '{_one_line(task.title, 40)}'?"), done)

    def action_delete_project(self) -> None:
        pid = self.selected_project()
        if pid == ROOT_ID:
            self.notify("Select a questline to delete.", severity="warning")
            return
        p = self.store.projects[pid]
        n_sub = len(self.store.subtree(pid)) - 1
        n_tasks = len(self.store.tasks_in(pid))
        parent = p.parent_id

        def done(ok: bool | None) -> None:
            if ok:
                removed = self.store.delete_project(pid)

                def undo() -> None:
                    self.store.restore_project(*removed)
                    self.refresh_tree(pid)

                self._remember_undo(f"deleted questline '{p.name}'", undo)
                self.notify(f"Deleted questline '{p.name}' — ctrl+z to undo")
                self.refresh_tree(parent)

        self.push_screen(
            Confirm(
                f"Delete the questline '{p.name}' with {n_sub} sub-questline(s) and {n_tasks} quest(s)?"
            ),
            done,
        )

    def _remember_undo(self, label: str, undo: Callable[[], object]) -> None:
        """Keep the last delete / completion so ctrl+z can take it back."""
        self._undo, self._undo_label = undo, label
        self.refresh_commands()

    def action_undo(self) -> None:
        if self._undo is None:
            return
        if self._try(self._undo):
            self.notify(f"Undid: {self._undo_label}")
            self._undo = None
            self.refresh_commands()

    # ----- sharing

    def action_export_project(self) -> None:
        pid = self.selected_project()
        code = self._try(self.store.export_project, pid)
        if not code:
            return
        self.copy_to_clipboard(code)
        what = "all questlines" if pid == ROOT_ID else f"'{self.store.projects[pid].name}'"
        self.push_screen(ShareCodeDialog(what, code))

    def action_import_project(self) -> None:
        pid = self.selected_project()
        where = "the top level" if pid == ROOT_ID else self.store.path_of(pid)

        def done(code: str | None) -> None:
            if code is not None and (result := self._try(self.store.import_project, pid, code)):
                projects, n = result
                self.refresh_tree(projects[0].id)
                names = ", ".join(f"'{p.name}'" for p in projects)
                self.notify(f"Imported {names} ({n} quest(s))")

        self.push_screen(ImportDialog(where), done)

    # ----- more… menu

    def action_options(self) -> None:
        """More actions: for the questline when the sidebar was used last, else for the quest."""
        task = self.selected_task()
        pid = self.selected_project()
        if task is not None and (self.last_window == "quests" or pid == ROOT_ID):
            title = f"Quest: {_one_line(task.title, 34)}"
            items = [
                ("open_scroll", "❖ Open scroll", "", True),
                ("edit_task", "✎ Edit", "e", True),
                ("set_priority", f"◆ Priority ({PRIORITIES[task.priority]})…", "p", True),
                ("postpone", "→ Due a day later", "]", True),
                ("clear_due", "∅ Clear due date", "", task.due is not None),
                ("move_task", "⇢ Move to…", "M", True),
                ("goto_project", "↗ Go to its questline", "g", self.view != task.project_id),
                ("delete_task", "✕ Abandon", "d", True),
            ]
        elif pid != ROOT_ID:
            title = f"Questline: {self.store.path_of(pid)}"
            items = [
                ("add_task", "+ New quest", "a", True),
                ("create_project", "+ New sub-questline", "n", True),
                ("rename_project", "✎ Rename", "r", True),
                ("move_project", "⇢ Move to…", "m", True),
                ("export_project", "⇪ Share", "x", True),
                ("import_project", "⇩ Import here", "i", True),
                ("delete_project", "✕ Delete questline", "d", True),
            ]
        else:
            title = "Quest log"
            items = [
                ("create_project", "+ New questline", "n", True),
                ("export_project", "⇪ Share everything", "x", bool(self.store.projects[ROOT_ID].children)),
                ("import_project", "⇩ Import", "i", True),
                ("help", "? Keys", "?", True),
            ]

        self.push_screen(ActionMenu(title, items), self._run)

    def check_action(self, action: str, parameters: tuple) -> bool | None:
        # main-screen actions shouldn't fire while a dialog is open
        if action in self.MAIN_ACTIONS and len(self.screen_stack) > 1:
            return False
        return True if self.command_enabled(action) else None

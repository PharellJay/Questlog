"""Data model: nested projects (questlines) of tasks (quests), XP and JSON persistence."""

from __future__ import annotations

import base64
import binascii
import json
import os
import re
import shutil
import tempfile
import uuid
import zlib
from dataclasses import asdict, dataclass, field, fields
from datetime import date, timedelta
from pathlib import Path

ROOT_ID = "root"

PRIORITIES = ("None", "Low", "Medium", "High")  # index = Task.priority

XP_REWARD = (10, 25, 50, 100)  # XP for completing a task, by priority

DUE_RANGES = ("any", "overdue", "today", "week", "none")

WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")

SHARE_PREFIX = "questlog:1:"  # share codes: prefix + base64(zlib(json of the project tree))


class StoreError(Exception):
    """Raised for invalid operations (bad names, cyclic moves, unreadable dates, ...)."""


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


def _today() -> date:
    return date.today()


# ---------------------------------------------------------------- dates


def parse_due(text: str, today: date | None = None) -> str | None:
    """Turn what the user typed into an ISO date (None for no date); raises StoreError if unreadable.

    Accepts YYYY-MM-DD, MM-DD, today/tod, tomorrow/tom, +N / Nd / Nw, and weekday names (mon, friday).
    """
    today = today or _today()
    text = text.strip().lower()
    if not text:
        return None
    if text in ("today", "tod", "now"):
        return today.isoformat()
    if text in ("tomorrow", "tom", "tmr"):
        return (today + timedelta(days=1)).isoformat()
    if m := re.fullmatch(r"\+?(\d+)\s*([dw]?)", text):
        if text.isdigit() and len(text) > 3:  # a bare "2026" is not a day count
            raise StoreError(f"Can't read the date '{text}'. Try 2026-10-24, fri, +3 or 2w.")
        n = int(m.group(1)) * (7 if m.group(2) == "w" else 1)
        return (today + timedelta(days=n)).isoformat()
    if len(text) >= 3:
        for i, name in enumerate(WEEKDAYS):
            if name.startswith(text):
                ahead = (i - today.weekday()) % 7 or 7  # the next one, never today
                return (today + timedelta(days=ahead)).isoformat()
    try:
        if re.fullmatch(r"\d{4}-\d{1,2}-\d{1,2}", text):
            y, mo, d = map(int, text.split("-"))
            return date(y, mo, d).isoformat()
        if re.fullmatch(r"\d{1,2}-\d{1,2}", text):
            mo, d = map(int, text.split("-"))
            when = date(today.year, mo, d)
            if when < today:
                when = date(today.year + 1, mo, d)
            return when.isoformat()
    except ValueError:
        raise StoreError(f"'{text}' is not a real date.") from None
    raise StoreError(f"Can't read the date '{text}'. Try 2026-10-24, fri, +3 or 2w.")


def describe_due(iso: str | None, today: date | None = None) -> str:
    """Short, relative text for a due date: 'overdue 3d', 'today', 'tomorrow', 'Fri', 'Oct 24'."""
    if not iso:
        return ""
    today = today or _today()
    when = date.fromisoformat(iso)
    days = (when - today).days
    if days < 0:
        return f"overdue {-days}d"
    if days == 0:
        return "today"
    if days == 1:
        return "tomorrow"
    if days < 7:
        return when.strftime("%a")
    if when.year == today.year:
        return when.strftime("%b %-d")
    return when.strftime("%b %-d %Y")


# ---------------------------------------------------------------- XP


def level_info(xp: int) -> tuple[int, int, int]:
    """(level, XP into this level, XP this level needs). Level n needs 100 * n XP to pass."""
    level, need = 1, 100
    while xp >= need:
        xp -= need
        level += 1
        need = 100 * level
    return level, xp, need


LEVEL_TITLES = (
    "Novice", "Apprentice", "Squire", "Adventurer", "Veteran", "Knight", "Champion", "Hero", "Legend",
)


def title_for_level(level: int) -> str:
    """The adventurer title shown for a level; Legend from level 9 on."""
    return LEVEL_TITLES[min(max(level, 1), len(LEVEL_TITLES)) - 1]


def streak(active_days: set[str], today: date | None = None) -> int:
    """Days in a row with a completed task, ending today (or yesterday, if today has none yet)."""
    day = today or _today()
    if day.isoformat() not in active_days:
        day -= timedelta(days=1)
    n = 0
    while day.isoformat() in active_days:
        n += 1
        day -= timedelta(days=1)
    return n


# ---------------------------------------------------------------- data


@dataclass
class Project:
    id: str
    name: str
    parent_id: str | None
    children: list[str] = field(default_factory=list)
    tasks: list[str] = field(default_factory=list)


@dataclass
class Task:
    id: str
    project_id: str
    title: str
    notes: str = ""
    due: str | None = None  # ISO date
    priority: int = 0  # index into PRIORITIES
    done: bool = False
    done_on: str | None = None  # ISO date it was completed
    created: str = field(default_factory=lambda: _today().isoformat())
    xp: int = 0  # XP it granted when completed (taken back if reopened)

    def overdue(self, today: date | None = None) -> bool:
        return not self.done and self.due is not None and self.due < (today or _today()).isoformat()


_TASK_FIELDS = {f.name for f in fields(Task)}


@dataclass
class Filter:
    """What the task list shows; smart views (Today, Next 7 days) apply their own date range on top."""

    show_done: bool = False
    min_priority: int = 0
    due: str = "any"  # one of DUE_RANGES
    text: str = ""
    recursive: bool = False  # include tasks of subprojects

    def summary(self) -> str:
        parts = []
        if self.min_priority:
            parts.append(f"{PRIORITIES[self.min_priority].lower()}+")
        if self.due != "any":
            parts.append({"overdue": "overdue", "today": "due today", "week": "due this week",
                          "none": "no date"}[self.due])
        if self.text:
            parts.append(f'"{self.text}"')
        return " · ".join(parts)

    def matches(self, task: Task, today: date | None = None) -> bool:
        today = today or _today()
        t = today.isoformat()
        if task.done and not self.show_done:
            return False
        if task.priority < self.min_priority:
            return False
        if self.due == "overdue" and not task.overdue(today):
            return False
        if self.due == "today" and not (task.due and task.due <= t):
            return False
        if self.due == "week" and not (task.due and task.due <= (today + timedelta(days=7)).isoformat()):
            return False
        if self.due == "none" and task.due is not None:
            return False
        if self.text:
            needle = self.text.lower()
            if needle not in task.title.lower() and needle not in task.notes.lower():
                return False
        return True

    def apply(self, tasks: list[Task], today: date | None = None) -> list[Task]:
        return sort_tasks([t for t in tasks if self.matches(t, today)])


def sort_tasks(tasks: list[Task]) -> list[Task]:
    """Open first; then by due date (undated last), highest priority, title."""
    return sorted(
        tasks,
        key=lambda t: (t.done, t.due is None, t.due or "", -t.priority, t.title.lower()),
    )


class Store:
    def __init__(self, path: str | os.PathLike | None = None):
        self.path = Path(path) if path else None
        self.projects: dict[str, Project] = {ROOT_ID: Project(ROOT_ID, "/", None)}
        self.tasks: dict[str, Task] = {}
        self.expanded: set[str] = set()  # projects open in the tree, restored on the next start
        self.xp = 0  # total XP earned by completing tasks
        self.active_days: set[str] = set()  # ISO dates with at least one completed task (for streaks)

    # ---------- persistence ----------

    @classmethod
    def load(cls, path: str | os.PathLike) -> "Store":
        store = cls(path)
        p = Path(path)
        if p.exists():
            data = json.loads(p.read_text(encoding="utf-8"))
            store.projects = {f["id"]: Project(**f) for f in data.get("projects", [])}
            store.tasks = {
                t["id"]: Task(**{k: v for k, v in t.items() if k in _TASK_FIELDS})
                for t in data.get("tasks", [])
            }
            if ROOT_ID not in store.projects:
                store.projects[ROOT_ID] = Project(ROOT_ID, "/", None)
            store.expanded = {pid for pid in data.get("expanded", []) if pid in store.projects}
            store.xp = int(data.get("xp", 0))
            store.active_days = set(data.get("active_days", []))
        return store

    def save(self) -> None:
        if self.path is None:
            return
        data = {
            "version": 1,
            "projects": [asdict(p) for p in self.projects.values()],
            "tasks": [asdict(t) for t in self.tasks.values()],
            "expanded": sorted(pid for pid in self.expanded if pid in self.projects),
            "xp": self.xp,
            "active_days": sorted(self.active_days),
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".data-", suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2, ensure_ascii=False)
            if self.path.exists():  # keep the previous version as a last-good copy
                shutil.copy2(self.path, self.path.with_name(self.path.name + ".bak"))
            os.replace(tmp, self.path)
        except BaseException:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise

    # ---------- projects ----------

    def check_name(self, parent_id: str, name: str, exclude: str | None = None) -> str:
        """Return the cleaned-up name, or raise StoreError if it can't be used in parent_id."""
        name = name.strip()
        if not name:
            raise StoreError("Name cannot be empty.")
        if "/" in name:
            raise StoreError("Name cannot contain '/'.")
        for cid in self.projects[parent_id].children:
            if cid != exclude and self.projects[cid].name.lower() == name.lower():
                raise StoreError(f"A project named '{name}' already exists here.")
        return name

    def _sorted_children(self, parent_id: str) -> None:
        self.projects[parent_id].children.sort(key=lambda i: self.projects[i].name.lower())

    def create_project(self, parent_id: str, name: str) -> Project:
        name = self.check_name(parent_id, name)
        project = Project(_new_id(), name, parent_id)
        self.projects[project.id] = project
        self.projects[parent_id].children.append(project.id)
        self._sorted_children(parent_id)
        self.save()
        return project

    def rename_project(self, project_id: str, name: str) -> None:
        if project_id == ROOT_ID:
            raise StoreError("Cannot rename the root.")
        project = self.projects[project_id]
        project.name = self.check_name(project.parent_id, name, exclude=project_id)
        self._sorted_children(project.parent_id)
        self.save()

    def is_descendant(self, project_id: str, ancestor_id: str) -> bool:
        """True if project_id is ancestor_id or lies beneath it."""
        cur: str | None = project_id
        while cur is not None:
            if cur == ancestor_id:
                return True
            cur = self.projects[cur].parent_id
        return False

    def move_project(self, project_id: str, new_parent_id: str) -> None:
        if project_id == ROOT_ID:
            raise StoreError("Cannot move the root.")
        if self.is_descendant(new_parent_id, project_id):
            raise StoreError("Cannot move a project into itself or its own subproject.")
        project = self.projects[project_id]
        if project.parent_id == new_parent_id:
            return
        self.check_name(new_parent_id, project.name)
        self.projects[project.parent_id].children.remove(project_id)
        self.projects[new_parent_id].children.append(project_id)
        project.parent_id = new_parent_id
        self._sorted_children(new_parent_id)
        self.save()

    def delete_project(self, project_id: str) -> tuple[dict[str, Project], dict[str, Task]]:
        """Delete a project with everything in it; returns what was removed, for restore_project."""
        if project_id == ROOT_ID:
            raise StoreError("Cannot delete the root.")
        project = self.projects[project_id]
        projects, tasks = {}, {}
        for sub in self.subtree(project_id):
            projects[sub] = self.projects.pop(sub)
            for tid in projects[sub].tasks:
                tasks[tid] = self.tasks.pop(tid)
        self.projects[project.parent_id].children.remove(project_id)
        self.save()
        return projects, tasks

    def restore_project(self, projects: dict[str, Project], tasks: dict[str, Task]) -> Project:
        """Put back a project removed by delete_project (the first entry is the project itself)."""
        project = next(iter(projects.values()))
        if project.parent_id not in self.projects:
            raise StoreError("Can't restore: its parent project no longer exists.")
        self.check_name(project.parent_id, project.name)
        self.projects.update(projects)
        self.tasks.update(tasks)
        self.projects[project.parent_id].children.append(project.id)
        self._sorted_children(project.parent_id)
        self.save()
        return project

    def subtree(self, project_id: str) -> list[str]:
        out, stack = [], [project_id]
        while stack:
            pid = stack.pop()
            out.append(pid)
            stack.extend(self.projects[pid].children)
        return out

    def path_of(self, project_id: str) -> str:
        parts = []
        cur: str | None = project_id
        while cur is not None and cur != ROOT_ID:
            parts.append(self.projects[cur].name)
            cur = self.projects[cur].parent_id
        return "/" + "/".join(reversed(parts))

    # ---------- sharing ----------

    def export_project(self, project_id: str) -> str:
        """A one-line share code with the project, its subprojects and tasks (all shared as open).

        For the root it holds every top-level project instead, i.e. everything.
        """

        def task_json(tid: str) -> dict:
            t = self.tasks[tid]
            out: dict = {"title": t.title}
            if t.notes:
                out["notes"] = t.notes
            if t.due:
                out["due"] = t.due
            if t.priority:
                out["priority"] = t.priority
            return out

        def tree(pid: str) -> dict:
            p = self.projects[pid]
            return {
                "name": p.name,
                "tasks": [task_json(t) for t in p.tasks],
                "projects": [tree(cid) for cid in p.children],
            }

        if project_id == ROOT_ID:
            if not self.projects[ROOT_ID].children:
                raise StoreError("There are no projects to export yet.")
            data: dict = {"projects": [tree(cid) for cid in self.projects[ROOT_ID].children]}
        else:
            data = tree(project_id)
        raw = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        return SHARE_PREFIX + base64.urlsafe_b64encode(zlib.compress(raw.encode("utf-8"), 9)).decode()

    @staticmethod
    def decode_share_code(code: str) -> list[dict]:
        """Parse and validate a share code into its top-level projects; raises StoreError if it isn't one."""
        bad = StoreError("That doesn't look like a Questlog share code.")
        code = "".join(code.split())  # chat apps may wrap long lines
        if not code.startswith(SHARE_PREFIX):
            raise bad
        try:
            data = json.loads(zlib.decompress(base64.urlsafe_b64decode(code[len(SHARE_PREFIX):])))
        except (binascii.Error, zlib.error, ValueError):
            raise bad from None

        def valid_task(t: object) -> bool:
            if not (isinstance(t, dict) and isinstance(t.get("title"), str) and t["title"].strip()):
                return False
            if not isinstance(t.get("notes", ""), str):
                return False
            if t.get("priority", 0) not in range(len(PRIORITIES)):
                return False
            due = t.get("due")
            if due is not None:
                try:
                    date.fromisoformat(due)
                except (TypeError, ValueError):
                    return False
            return True

        def valid(node: object) -> bool:
            return (
                isinstance(node, dict)
                and isinstance(node.get("name"), str) and node["name"].strip() != ""
                and "/" not in node["name"]
                and isinstance(node.get("tasks"), list)
                and all(valid_task(t) for t in node["tasks"])
                and isinstance(node.get("projects"), list)
                and all(valid(sub) for sub in node["projects"])
            )

        # a single project, or {"projects": [...]} for everything exported from the root
        if isinstance(data, dict) and set(data) == {"projects"} and isinstance(data["projects"], list):
            nodes = data["projects"]
        else:
            nodes = [data]
        if not nodes or not all(valid(node) for node in nodes):
            raise bad
        return nodes

    def import_project(self, parent_id: str, code: str) -> tuple[list[Project], int]:
        """Add the project(s) from a share code under parent_id; returns them and the task count.

        A name already used in parent_id gets " (2)", " (3)", ... appended.
        """
        nodes = self.decode_share_code(code)
        taken = {self.projects[c].name.lower() for c in self.projects[parent_id].children}
        for node in nodes:
            base = node["name"].strip()
            name, n = base, 2
            while name.lower() in taken:
                name, n = f"{base} ({n})", n + 1
            node["name"] = name
            taken.add(name.lower())
        count = 0

        def build(node: dict, pid: str) -> Project:
            nonlocal count
            project = Project(_new_id(), node["name"].strip(), pid)
            self.projects[project.id] = project
            self.projects[pid].children.append(project.id)
            self._sorted_children(pid)
            for t in node["tasks"]:
                task = Task(
                    _new_id(), project.id, t["title"].strip(), t.get("notes", "").strip(),
                    t.get("due"), t.get("priority", 0),
                )
                self.tasks[task.id] = task
                project.tasks.append(task.id)
                count += 1
            for sub in node["projects"]:
                build(sub, project.id)
            return project

        projects = [build(node, parent_id) for node in nodes]
        self.save()
        return projects, count

    # ---------- tasks ----------

    @staticmethod
    def check_title(title: str) -> None:
        if not title.strip():
            raise StoreError("Title cannot be empty.")

    def add_task(
        self, project_id: str, title: str, notes: str = "", due: str | None = None, priority: int = 0
    ) -> Task:
        if project_id == ROOT_ID:
            raise StoreError("Select a project first (tasks must live in a project).")
        self.check_title(title)
        task = Task(_new_id(), project_id, title.strip(), notes.strip(), due, priority)
        self.tasks[task.id] = task
        self.projects[project_id].tasks.append(task.id)
        self.save()
        return task

    def edit_task(self, task_id: str, title: str, notes: str, due: str | None, priority: int) -> None:
        self.check_title(title)
        task = self.tasks[task_id]
        task.title, task.notes, task.due, task.priority = title.strip(), notes.strip(), due, priority
        self.save()

    def delete_task(self, task_id: str) -> tuple[Task, int]:
        """Delete a task; returns it and its position, for restore_task."""
        task = self.tasks.pop(task_id)
        siblings = self.projects[task.project_id].tasks
        index = siblings.index(task_id)
        del siblings[index]
        self.save()
        return task, index

    def restore_task(self, task: Task, index: int) -> None:
        """Put back a task removed by delete_task, at its old position."""
        if task.project_id not in self.projects:
            raise StoreError("Can't restore: its project no longer exists.")
        self.tasks[task.id] = task
        self.projects[task.project_id].tasks.insert(index, task.id)
        self.save()

    def move_task(self, task_id: str, project_id: str) -> None:
        if project_id == ROOT_ID:
            raise StoreError("Tasks must live in a project.")
        task = self.tasks[task_id]
        if task.project_id == project_id:
            return
        self.projects[task.project_id].tasks.remove(task_id)
        self.projects[project_id].tasks.append(task_id)
        task.project_id = project_id
        self.save()

    def set_done(self, task_id: str, done: bool) -> int:
        """Complete or reopen a task; returns the XP gained (negative when reopening)."""
        task = self.tasks[task_id]
        if task.done == done:
            return 0
        task.done = done
        if done:
            task.done_on = _today().isoformat()
            task.xp = XP_REWARD[task.priority]
            self.active_days.add(task.done_on)
            gained = task.xp
        else:
            task.done_on = None
            gained, task.xp = -task.xp, 0
        self.xp = max(0, self.xp + gained)
        self.save()
        return gained

    def set_priority(self, task_id: str, priority: int) -> None:
        if priority not in range(len(PRIORITIES)):
            raise StoreError("Unknown priority.")
        self.tasks[task_id].priority = priority
        self.save()

    def shift_due(self, task_id: str, days: int) -> None:
        """Move the due date by `days`; a task without a date starts from today."""
        task = self.tasks[task_id]
        start = date.fromisoformat(task.due) if task.due else _today()
        task.due = (start + timedelta(days=days if task.due else max(days, 0))).isoformat()
        self.save()

    # ---------- queries ----------

    def tasks_in(self, project_id: str, recursive: bool = True) -> list[Task]:
        ids = self.subtree(project_id) if recursive else [project_id]
        return [self.tasks[t] for pid in ids for t in self.projects[pid].tasks]

    def today_tasks(self, today: date | None = None) -> list[Task]:
        """Open tasks that are overdue or due today, plus the ones completed today."""
        t = (today or _today()).isoformat()
        return [
            task for task in self.tasks.values()
            if (not task.done and task.due is not None and task.due <= t) or task.done_on == t
        ]

    def upcoming_tasks(self, days: int = 7, today: date | None = None) -> list[Task]:
        """Tasks due from today up to `days` ahead (overdue ones are in Today)."""
        today = today or _today()
        start, end = today.isoformat(), (today + timedelta(days=days)).isoformat()
        return [task for task in self.tasks.values() if task.due and start <= task.due <= end]

    def tasks_due_on(self, day: date) -> list[Task]:
        """Tasks (open or done) due on that day."""
        iso = day.isoformat()
        return [task for task in self.tasks.values() if task.due == iso]

    def counts(self, project_id: str, today: date | None = None) -> tuple[int, int]:
        """(open, overdue) tasks in the project and its subprojects."""
        open_ = [t for t in self.tasks_in(project_id) if not t.done]
        return len(open_), sum(t.overdue(today) for t in open_)

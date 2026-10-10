import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from models import ROOT_ID, Filter, Store, StoreError, describe_due, parse_due  # noqa: E402

TODAY = date(2026, 10, 10)  # a Saturday


@pytest.mark.parametrize(
    "text, expected",
    [
        ("", None),
        ("today", "2026-10-10"),
        ("tom", "2026-10-11"),
        ("+3", "2026-10-13"),
        ("3", "2026-10-13"),
        ("2w", "2026-10-24"),
        ("mon", "2026-10-12"),
        ("sat", "2026-10-17"),  # the next one, never today
        ("10-24", "2026-10-24"),
        ("1-5", "2027-01-05"),  # already passed this year
        ("2026-12-01", "2026-12-01"),
    ],
)
def test_parse_due(text, expected):
    assert parse_due(text, TODAY) == expected


@pytest.mark.parametrize("text", ["soon", "2026", "2026-02-30", "13-01"])
def test_parse_due_rejects(text):
    with pytest.raises(StoreError):
        parse_due(text, TODAY)


def test_describe_due():
    assert describe_due("2026-10-07", TODAY) == "overdue 3d"
    assert describe_due("2026-10-10", TODAY) == "today"
    assert describe_due("2026-10-11", TODAY) == "tomorrow"
    assert describe_due("2026-10-14", TODAY) == "Wed"
    assert describe_due("2026-11-02", TODAY) == "Nov 2"


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "data.json")
    work = s.create_project(ROOT_ID, "Work")
    q4 = s.create_project(work.id, "Q4")
    s.add_task(work.id, "Old report", due="2000-01-01", priority=3)
    s.add_task(q4.id, "Plan", due=date.today().isoformat())
    s.add_task(q4.id, "Someday", notes="api ideas")
    return s


def test_counts_include_subprojects(store):
    work = store.projects[ROOT_ID].children[0]
    assert store.counts(work) == (3, 1)


def test_today_view_and_done(store):
    titles = {t.title for t in store.today_tasks()}
    assert titles == {"Old report", "Plan"}
    plan = next(t for t in store.tasks.values() if t.title == "Plan")
    store.set_done(plan.id, True)
    assert plan in store.today_tasks()  # ticked off today stays visible
    assert Filter().apply(store.today_tasks()) == [t for t in store.today_tasks() if not t.done]


def test_filter_and_sort(store):
    tasks = list(store.tasks.values())
    assert [t.title for t in Filter().apply(tasks)] == ["Old report", "Plan", "Someday"]
    assert [t.title for t in Filter(text="API").apply(tasks)] == ["Someday"]
    assert [t.title for t in Filter(min_priority=3).apply(tasks)] == ["Old report"]
    assert [t.title for t in Filter(due="none").apply(tasks)] == ["Someday"]
    assert [t.title for t in Filter(due="overdue").apply(tasks)] == ["Old report"]


def test_move_project_into_itself_fails(store):
    work = store.projects[ROOT_ID].children[0]
    q4 = store.projects[work].children[0]
    with pytest.raises(StoreError):
        store.move_project(work, q4)


def test_delete_restore_and_reload(store):
    work = store.projects[ROOT_ID].children[0]
    removed = store.delete_project(work)
    assert not store.tasks
    store.restore_project(*removed)
    again = Store.load(store.path)
    assert len(again.tasks) == 3 and again.path_of(again.projects[work].children[0]) == "/Work/Q4"


def test_share_code_round_trip(store):
    store.set_done(next(iter(store.tasks)), True)
    code = store.export_project(ROOT_ID)
    other = Store()
    projects, n = other.import_project(ROOT_ID, code)
    assert n == 3 and projects[0].name == "Work"
    assert not any(t.done for t in other.tasks.values())
    assert {t.priority for t in other.tasks.values()} == {0, 3}
    # importing again renames instead of clashing
    projects, _ = other.import_project(ROOT_ID, code)
    assert projects[0].name == "Work (2)"
    with pytest.raises(StoreError):
        other.import_project(ROOT_ID, "flashcards:1:abc")


def test_xp_levels_and_streak(store):
    from models import level_info, streak

    report = next(t for t in store.tasks.values() if t.title == "Old report")  # High priority
    assert store.set_done(report.id, True) == 100
    assert store.set_done(report.id, True) == 0  # already done: no double reward
    assert store.xp == 100 and level_info(store.xp) == (2, 0, 200)
    assert store.set_done(report.id, False) == -100 and store.xp == 0
    assert level_info(299) == (2, 199, 200)
    days = {"2026-10-08", "2026-10-09", "2026-10-10"}
    assert streak(days, TODAY) == 3
    assert streak(days - {"2026-10-10"}, TODAY) == 2  # today not done yet: streak still alive
    assert streak({"2026-10-07"}, TODAY) == 0


def test_tasks_due_on_and_titles(store):
    from models import title_for_level

    assert [t.title for t in store.tasks_due_on(date.today())] == ["Plan"]
    assert store.tasks_due_on(date(1999, 1, 1)) == []
    assert title_for_level(1) == "Novice"
    assert title_for_level(5) == "Veteran"
    assert title_for_level(40) == "Legend"

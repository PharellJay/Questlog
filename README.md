# Questlog

**Your to-do list as an RPG quest journal, in the terminal.** Organise your work into nested *questlines*, post *quests* with due dates and priorities, see today and the week ahead at a glance, and earn XP and levels for every quest you complete. You can also share whole questlines with friends through a short code you can paste into any chat.

Built with Python and [Textual](https://textual.textualize.io/).

```
╔═══════════════════════════════════════════════════════════════════════════════════════════════╗
║ ⚔ Q U E S T L O G  Sat 10 Oct            Adventurer · Lv 4 ▰▰▰▰▰▰▱▱▱▱ 240/400 XP ✹ 3-day streak ║
╚═══════════════════════════════════════════════════════════════════════════════════════════════╝
╔═ Questlines ════════╗╔═▶ ☀ Today's Quests ═══════════════════════════════════════════════════╗
║ ☀ Today 3 ⚠1        ║║ ◆ Write launch report  ⚠ 3d late  ✎  /Work/Q4 launch                   ║
║ ✧ All quests 10 ⚠1  ║║ ◆ Pay rent  ★ today  /Home                                             ║
║  ├ Home 2           ║║ ◆ Review API changes  ★ today  /Work/Q4 launch                         ║
║  ├ Learn Rust 1     ║║ ✔ Send invites  /Work/Q4 launch                                        ║
║  └ ▼ Work 7 ⚠1      ║║                                                                        ║
║     └ Q4 launch 6 ⚠1║╚════════════════════════════════════════════════════ ━━━─────── 1/4 done ╝
║                     ║╔═ ◷ This Week ═══════════════════════════════════════════════════════════╗
╚═════════════════════╝║ TODAY      │ Sun 11     │ Mon 12     │ Tue 13 │ Wed 14       │ Thu 15 │ … ║
╔═ Command ═══════════╗║ ◆ Pay rent │ ◆ Finish … │ ◆ 1:1 with │  ·     │ ◆ Order cat… │  ·     │   ║
║ ▶ New quest       a ║║ ◆ Review … │            │            │        │ ◆ Book venue │        │   ║
║   Complete      spc ║║ ✔ Send in… │            │            │        │ ◆ Print bad… │        │   ║
║   Priority…       p ║║ ⚠1 late    │            │            │        │              │        │   ║
║   Postpone        ] ║╚═════════════════════════════════════════════════════════════════════════╝
║   Filter…         / ║
║   Show done       h ║
║   New questline   n ║
║   More…           o ║
╚═════════════════════╝
 tab next window  ·  enter open  ·  ←→ day  ·  ? keys  ·  q quit
```

The UI uses quest words; underneath it is a normal task tracker:

| In Questlog | Means |
| --- | --- |
| **Questline** | A project (they can be nested: sub-questlines) |
| **Quest** | A task |
| **Complete** | Mark a task done |
| **Abandon** | Delete a task |

## The dashboard

Everything lives on one screen of JRPG-style windows: navy panels with double gold frames. The window that has focus gets a bright gold title tab with a `▶`. `tab` moves between the windows.

- **Banner**: your adventurer title (Novice → Apprentice → Squire → Adventurer → Veteran → Knight → Champion → Hero → Legend), level, XP bar and daily streak.
- **Questlines** (top left): `☀ Today`, `✧ All quests` and your nested questlines, with open counts and `⚠N` for overdue quests. Moving through it changes what the main window shows.
- **Command** (bottom left): a JRPG command menu. Pick an entry with the arrow keys and `enter`, or click it. It acts on the highlighted quest, and entries that don't apply right now are greyed out. Every entry also has a hotkey, so you never have to open the menu.
- **Quests** (the big window): Today's quests by default, or all quests, one questline, or one day of the week. It shows each quest's priority gem (◆ grey / blue / purple / orange = None / Low / Medium / High), due date (`⚠ 3d late`, `★ today`), a `✎` if it has notes, and its questline. The bottom of the frame shows a progress bar of how many quests are done.
- **This Week**: seven day cards, today first, each listing the quests due that day (or gems and counts on narrow terminals). Today's card also shows how many quests are late. Use `←`/`→` to pick a day and `enter` (or a click) to list that day's quests in the main window.
- **Quest Scroll**: press `enter` on a quest to unroll its full details on a parchment scroll (priority and XP reward, due date, questline, status, notes), together with everything you can do with it.
- **Quest complete!** Completing a quest shows a *QUEST COMPLETE* card with the XP you earned, plus a *LEVEL UP* card when you reach a new level. It goes away by itself, or press any key.

## Features

- **Nested questlines**: as many levels as you need. You can create, rename, move and delete them.
- **Quests with due dates and priorities**: each quest has a title, optional notes, a due date and a priority. Dates can be typed the quick way: `today`, `tom`, `fri`, `+3`, `2w`, `10-24` or `2026-10-24`. The form shows what each one means as you type.
- **XP, levels and streaks**: completing a quest earns XP by priority: None +10, Low +25, Medium +50, High +100. Level *n* takes 100 × *n* XP to pass. Your streak counts the days in a row on which you completed at least one quest. Reopening a quest gives its XP back.
- **Sensible order**: open quests come first, then they are sorted by due date (undated quests last), then by priority.
- **Filters**: search titles and notes, set a minimum priority, show only overdue, due today, due this week or undated quests, and choose whether to include sub-questlines. Completed quests are hidden in questline views until you press `h`. Today and the day views always show them, ticked off.
- **Quick edits**: `space` completes a quest, `p` cycles its priority, and `]` / `[` move its due date a day later / earlier.
- **Undo**: `ctrl+z` takes back the last completion (XP included), or the last abandoned quest or deleted questline.
- **Share questlines**: export a questline (with its sub-questlines), or everything, as one line of text. It is copied to your clipboard. Anyone can paste it into **Import** to get the same questlines and quests.
- **Mouse and keyboard**: click windows, quests, days and commands. Right-click a quest or questline for its options.
- **Safe saving**: every change is saved right away. The previous version is kept as `data.json.bak`, and a damaged data file gives a clear error message instead of a crash.

## Installation

You need **Python 3.10 or newer**.

```bash
git clone https://github.com/PharellJay/Questlog.git
cd Questlog
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

On Debian/Ubuntu, if `python3 -m venv` complains about `ensurepip`, install the venv package first (e.g. `sudo apt install python3.14-venv`, matching your Python version).

## Usage

```bash
./run.sh                 # uses data.json next to the program
./run.sh my-quests.json  # or any other data file
```

Without the script: `.venv/bin/python questlog.py [data-file]`. The file is created automatically if it doesn't exist yet.

### Getting started

1. Press **n** to start a questline, such as `Work`. Press **n** again while `Work` is shown to make a sub-questline.
2. Press **a** to post a quest. Type a title, a due date (for example `fri`) and pick a priority, then press **ctrl+s** or **enter**.
3. Press **t** for today's quests, and **space** to complete them and earn XP.
4. Press **tab** until **This Week** is focused, then use **←/→** and **enter** to look at a day.
5. Press **?** at any time to see every key.

## Keyboard shortcuts

| Key | Action |
| --- | --- |
| `a` | Post a quest (to the questline shown, or the last one you used) |
| `space` | Complete the highlighted quest, or reopen it |
| `enter` | Open the quest's scroll (in the quest window); choose a day (in This Week); run a command (in Command) |
| `e` | Edit the highlighted quest |
| `p` | Cycle the quest's priority |
| `]` / `[` | Move the due date one day later / earlier |
| `M` / `g` | Move the quest to another questline / go to its questline |
| `d` | Abandon the quest, or delete the questline if you last used the sidebar |
| `t` | Today's quests |
| `←` / `→` | Pick a day (in This Week) |
| `/` or `F` | Filter and search |
| `h` | Show / hide completed quests |
| `n` | New questline |
| `r` / `m` | Rename / move the questline shown |
| `z` | Collapse or expand all questlines |
| `i` / `x` | Import a share code / share the questline shown (or everything) |
| `o` | More actions |
| `ctrl+z` | Undo the last completion or deletion |
| `tab` | Next window |
| `?` | All keys |
| `q` | Quit |

In the quest form, **ctrl+s** saves, **ctrl+l** changes the questline and **esc** cancels. In Yes/No questions, **y** and **n** answer directly.

## Sharing questlines

1. Show a questline, or **All quests** to share everything, and press `x` (or **More… → Share**).
   The share code (`questlog:1:…`) is copied to your clipboard and also shown in a window, in case your terminal doesn't support copying.
2. Send the code to someone, for example in a chat.
3. They show where the questlines should go, press `i`, paste the code and press **ctrl+s**.

Share codes contain questline names and each quest's title, notes, due date and priority. Everything arrives as open, and XP is not shared. If a questline with the same name already exists, the imported one is renamed to `Name (2)`.

## Your data

Everything is stored in one readable JSON file (`data.json` by default). It holds the questlines (`projects`), the quests (`tasks`), your XP, the days you were active, and which questlines are open in the sidebar. To back up or move your quest log, copy that file.

## Project structure

| File | Purpose |
| --- | --- |
| `questlog.py` | Entry point: loads the data file and starts the app |
| `models.py` | Data model: projects, tasks, date parsing, filters, XP/levels, saving/loading and share codes (no UI code) |
| `ui.py` | The Textual user interface: theme, dashboard windows, command menu, week strip, dialogs and keys |
| `tests/` | Tests for the data model (`.venv/bin/pip install pytest && .venv/bin/pytest`) |
| `run.sh` | Starts the app with the project's virtual environment |
| `requirements.txt` | Python dependencies (Textual 8) |

## License

[MIT](LICENSE) © 2026 Pharell Jay Jeyakumar

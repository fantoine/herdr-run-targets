# Herdr Run Targets

A [Herdr](https://herdr.dev) plugin that turns a repository's dev services into a
dashboard: launch several at once, then stop, restart or close them individually.

One tab holds a persistent dashboard pane on the left and a column of service
panes on the right.

Targets run as commands in your shell, and `.herdr-run.toml` is committed with the
repository: give it the same trust you give a `Makefile` in a fresh clone.

## Requirements

- Herdr 0.8.2 or newer
- Python 3.11 or newer
- Git
- macOS or Linux

## Install

```bash
git clone <this-repository>
herdr plugin link ./herdr-run-targets
```

Bind a key in `~/.config/herdr/config.toml`, then run `herdr server reload-config`:

```toml
[[keys.command]]
key = "prefix+shift+t"
type = "plugin_action"
command = "fantoine.run-targets.toggle"
description = "Run targets"
```

## Configuration

Two optional files at the repository root:

| File | Purpose | Commit it? |
| --- | --- | --- |
| `.herdr-run.toml` | The team's services | Yes |
| `.herdr-run.local.toml` | Your overrides and scratch targets | No — gitignore it |

```toml
[[target]]
name = "api"
command = "yarn nx serve api"

[[target]]
name = "web"
command = "yarn nx serve web"
cwd = "apps/web"           # optional, relative to the repository root
env = { PORT = "3000" }    # optional
```

A local target with the same `name` replaces the team one entirely; a new name is
appended. The dashboard marks local targets with a trailing `*` so a command that
differs from the repository's is never a mystery. Either file alone is enough —
you can try a target without committing anything.

An invalid file does not cost you the other one: the targets of the valid file
stay available and the parse error is reported.

## Using the dashboard

The dashboard starts read only. No key destroys anything until you enter edit
mode. The header reads `RUN TARGETS <repository>`, so a dashboard opened on the
wrong directory says which one it is looking at instead of quietly reporting an
empty repository.

**View mode**

| Key | Effect |
| --- | --- |
| `↑` `↓` / `j` `k` | move the cursor |
| `e` | enter edit mode |
| `q` | close the dashboard pane |

There is no key to jump into a service's pane — Herdr 0.8.2 has no way to focus
an arbitrary pane by id, only a directional `herdr pane focus` between
neighbours. The Herdr prefix should get you there, but whether a curses TUI lets
the prefix through has not been verified. `q` closes the dashboard either way,
and it also works while the pane is too narrow to draw the table.

**Edit mode**

| Key | Effect |
| --- | --- |
| `space` | check / uncheck |
| `enter` | start the selection |
| `s` | stop |
| `r` | restart |
| `x` | close the pane |
| `esc` | cancel: uncheck everything, back to view mode |

The edit-mode legend wraps onto as many lines as the pane's width needs, so no
key is ever hidden by truncation. In a narrow pane that costs two extra rows
while you are in edit mode; below about six rows of height the legend crowds the
service list out entirely, which is the point at which the pane wants resizing
anyway.

Actions apply to every checked row. With nothing checked, they apply to the row
under the cursor. After an action the dashboard returns to view mode and clears
the selection, so a stray second keypress cannot replay a batch.

## Naming

The tab the plugin creates is named `run`. A dashboard reopened into a tab that
already existed keeps that tab's own label — the plugin only renames a tab it
created itself.

Each service pane is named after its target, so a column of them reads at a
glance instead of showing four identical shell titles. The rename happens
before the command starts, so the command's own terminal title does not win.

Both renames are best-effort: if Herdr refuses one, the service still starts and
the dashboard still opens.

## States

| State | Meaning |
| --- | --- |
| `running` | a process holds the pane's foreground |
| `stopped` | you stopped it from the dashboard |
| `exited` | it stopped on its own — a crash or a clean finish |
| `idle` | never started in this tab |
| `gone` | its pane was closed |

`stopped` and `exited` look identical to the system: Herdr reports which process
is in the foreground, never why it left. The difference comes from the plugin
remembering what it asked for.

## Behaviour worth knowing

**Stopping keeps the pane and its logs.** A server that just crashed keeps its
output on screen, and restarting reuses the same pane. Closing the pane is a
separate key.

**An action with nothing to do is skipped and reported** — `db: already stopped,
stop skipped` — rather than silently ignored.

**Restarting a stopped service starts it.** The point of a restart is to end up
running.

**Restarting waits for the service to stop.** After the interrupt the plugin
polls the pane's foreground for up to about three seconds. If the process is
still there, the command is not sent — typing it into a dying process would only
feed its stdin — and you get `api: still running after stop, restart skipped`.

**The plugin only ever touches panes it created.** Other plugins put panes in
your tabs — `herdr-sidebar` docks one in every new tab — and they are never
split, stopped or closed by this one.

**Toggling the dashboard off closes the tab when nothing is running there.**
With at least one service pane alive, the toggle removes only the dashboard pane
and reopens it in place next time. With none, it closes the whole tab and forgets
it, so repeated toggles never pile up orphan tabs. It only ever closes a tab it
recorded as its own.

**The column is narrow, and stays narrow.** Service panes are stacked by
successive splits, so heights halve rather than divide evenly: past two or three
services you will want to drag the dividers yourself. Rebalancing split ratios is
out of scope. The dashboard column has the same constraint — below 30 columns or
4 rows it shows `Too small - q to close`, and it needs a fifth row before the
first service line has anywhere to go.

## Troubleshooting

```bash
herdr plugin log list --plugin fantoine.run-targets --limit 20
```

- `No targets in <repository>. Add .herdr-run.toml or .herdr-run.local.toml` —
  the merged configuration has no targets: neither file exists at the root of the
  named repository, or every entry present failed to parse. Check the name: if it
  is not the repository you expected, the pane inherited the wrong working
  directory.
- `<file>: invalid TOML (...)` — that file was skipped; the other one still
  applies.
- `<file>: target 'x' has an unsafe cwd; skipped` — `cwd` must stay inside the
  repository: no absolute path, no `..`.
- `<name>: no pane of ours to split from` — the dashboard pane vanished. Toggle
  the dashboard to bring it back.
- `<name>: still running after stop, restart skipped` — the service ignored the
  interrupt for three seconds. Stop it yourself in its pane, then start it again.
- `... is not inside a git repository.` — the tab's working directory is outside
  a repository, so no configuration can be found.
- `herdr <command> failed: <message>` — a Herdr CLI call itself failed; the
  message after the colon is Herdr's own, quoted verbatim.

## Development

```bash
python3 -m unittest discover -s tests -v
```

Standard library only; nothing to install.

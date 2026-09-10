<div align="center">
  <img src="icon.svg" width="96" height="96" />

  # Run Targets

  **Every dev service your repo declares, one keypress away.**

  ![version](https://img.shields.io/badge/version-0.2.1-2B8ABF)
  ![license](https://img.shields.io/badge/license-MIT-blue)
  ![herdr](https://img.shields.io/badge/herdr-%E2%89%A5%200.8.2-4AABDF)
</div>

---

Run Targets turns the services a repository declares into a dashboard you call
up with a keypress: a floating pane listing every target and its state, and one
named tab per running service. Launch several at once, then stop, restart or
remove them one by one — without retyping a command or hunting for the right
tab.

<details>
<summary><strong>Example: bringing a project up in the morning</strong></summary>

Your repository declares what it runs:

```toml
# .herdr-run.toml
[[target]]
name = "api"
command = "yarn nx serve api"

[[target]]
name = "web"
command = "yarn nx serve web"
```

Press your key. The dashboard floats over the pane you were on:

```
RUN TARGETS  my-project

> api         idle
  web         idle

SIMPLE   enter start   s stop   r restart   x close   space multi   esc close
```

Press `space` on each, then `enter`. Two tabs appear, each named after its
target:

```
[ 1 ] [ api ] [ web ]
```

An hour later the API needs a restart after a config change. Call the dashboard
back up, cursor on `api`, then `r`. It stops, restarts in the same tab, and the
previous output is still above it.

</details>

## 💡 Why?

A project's services live in someone's shell history. You remember two of the
four commands, the third is in a teammate's notes, and the fourth you rediscover
by grepping `package.json`. Then you do it again tomorrow.

Declaring them once turns that into a list anyone can read — and a dashboard
that shows which are running right now.

Useful for:
- **Monorepos** -- half a dozen `serve` targets, none of them memorable
- **Onboarding** -- a new teammate reads the file instead of asking
- **Worktrees** -- each one brings its own services up the same way
- **Scratch targets** -- try a command without committing it to the team's file

## 📦 Installation

```bash
herdr plugin install fantoine/herdr-run-targets
```

Or, to work on it locally:

```bash
git clone https://github.com/fantoine/herdr-run-targets
herdr plugin link ./herdr-run-targets
```

Requires Herdr 0.8.2 or newer, Python 3.11 or newer, git, and macOS or Linux.

### Bind a key

In `~/.config/herdr/config.toml`, then `herdr server reload-config`:

```toml
[[keys.command]]
key = "prefix+shift+s"
type = "plugin_action"
command = "fantoine.run-targets.toggle"
description = "Run targets"
```

The key opens the dashboard, and closes it when it is already there.

## 🚀 Getting started

### Declare your targets

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

A local target with the same `name` replaces the team one; a new name is added
to the list. Local targets show a trailing `*`, and the dashboard's footer spells
it out as `* local` while one is on screen. Either file alone is enough, so you
can try a target without committing anything, and a broken file never costs you
the targets of the other one.

### Drive the dashboard

The action keys are the same in both modes; what changes is what they apply to.

| Simple mode | |
| --- | --- |
| `↑` `↓` / `j` `k` | move the cursor |
| `enter` | start the target under the cursor |
| `s` | stop it |
| `r` | restart it |
| `x` | close its tab |
| `space` | check it — this is what enters multi-select |
| `esc` / `q` | close the dashboard |

| Multi-select mode | |
| --- | --- |
| `space` | check / uncheck; unchecking the last row leaves the mode |
| `enter` `s` `r` `x` | apply to every checked row |
| `esc` | uncheck everything, back to simple mode — press it again to close |

So one service is handled straight from the list, and a batch is one `space`
away. An action applies to the checked rows when there are any, and to the row
under the cursor otherwise; either way the dashboard returns to simple mode once
it has run.

## ⚙️ Settings

Optional, in the plugin's own config directory — `herdr plugin config-dir
fantoine.run-targets` prints it:

```toml
# config.toml
[tabs]
label_prefix = ""       # e.g. "run:" to spot the plugin's tabs at a glance
label_suffix = ""

[dashboard]
focus_mode = "stay"     # stay | first | last
placement = "overlay"   # overlay | popup
popup_width = "45%"     # popup only: cells (24) or a percentage
popup_height = "40%"
```

| Setting | Effect |
| --- | --- |
| `label_prefix` / `label_suffix` | Wrap the target's name in the tab label. Applied when the tab is created, so changing them leaves existing tabs alone |
| `focus_mode` | Where the focus goes after a batch launch: `stay` on the dashboard, or the `first` / `last` tab of the batch |
| `placement` | `overlay` covers the pane you are on; `popup` is a centred, sized box |
| `popup_width` / `popup_height` | Popup size, in terminal cells or a percentage of the window. Omit either for Herdr's half-size default. Ignored by `overlay`, which Herdr refuses to size |

Edits take effect on the next launch — no dashboard restart. A value the plugin
does not understand is reported in the dashboard's footer rather than applied
silently.

**Overlay or popup?** An overlay is an ordinary pane: your key closes it as well
as opens it. A popup is centred and sized, but session-modal and belongs to no
pane — so it is `q` that closes it, not the key that opened it.

## 📊 States

| State | Meaning |
| --- | --- |
| `running` | the service is up |
| `stopped` | you stopped it from the dashboard |
| `exited` | it stopped on its own — go read its tab |
| `idle` | not started yet |
| `gone` | its tab was closed |

## ⚠️ Worth knowing

**Targets run as commands in your shell**, and `.herdr-run.toml` is committed
with the repository: give it the same trust you give a `Makefile` in a fresh
clone.

**The dashboard floats over the pane you are on.** Herdr opens an overlay — and
a popup — on the active pane, so press the key from the workspace whose services
you want to manage.

**Stopping keeps the tab and its output.** A server that just crashed keeps its
logs on screen, and restarting reuses the same tab. `x` is what removes a tab.

**Closing the dashboard leaves every service running.** It owns no tab of its
own, so the key brings it straight back.

**An action with nothing to do says so** rather than failing silently — pressing
`s` on a stopped service prints `api: already stopped, stop skipped`.

**The action keys act immediately**, with no confirmation: `x` on the row under
the cursor closes that service's tab. Its output goes with the tab, so reach for
`s` when you still want to read it.

**The name column follows your longest target name**, up to 40 characters, and
gives room back to the state column in a narrow pane. Names can be as long as
they read well.

**Restarting waits for the service to actually stop**, up to three seconds. A
service that ignores the interrupt is left alone rather than being sent a
command it cannot read.

**Closing a service's tab by hand is fine.** The dashboard shows it as `gone`,
and `x` then just forgets it.

## 🩺 Troubleshooting

```bash
herdr plugin log list --plugin fantoine.run-targets --limit 20
```

| Message | What to do |
| --- | --- |
| `No targets in <repository>. Add .herdr-run.toml or .herdr-run.local.toml` | No configuration was found. If the repository name is not the one you expected, the dashboard opened on the wrong directory. |
| `<file>: invalid TOML (...)` | Fix the syntax; the other file still applies meanwhile. |
| `<file>: target 'x' has an unsafe cwd; skipped` | `cwd` must stay inside the repository — no absolute path, no `..`. |
| `config.toml: unknown focus_mode '...'` | Use `stay`, `first` or `last`. |
| `config.toml: unknown placement '...'` | Use `overlay` or `popup`. |
| `config.toml: unsupported popup_width '...'` | A number of cells (`24`) or a percentage (`"45%"`). |
| `<name>: still running after stop, restart skipped` | The service ignored the interrupt. Stop it yourself in its tab, then start it again. |
| `<name>: herdr tab create failed: ...` | Herdr refused the tab. The message is its own; the service was not started and nothing was recorded. |
| `... is not inside a git repository.` | Open the dashboard from a directory inside your project. |

## 🧪 Development

```bash
python3 -m unittest discover -s tests -v
uvx ruff check .
```

Standard library only; nothing to install. The lint is clean with ruff's
defaults — no configuration file, so any recent version applies the same rules.

## License

MIT

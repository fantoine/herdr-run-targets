<div align="center">
  <img src="icon.svg" width="96" height="96" />

  # Run Targets

  **Every dev service your repo declares, one keypress away.**

  ![version](https://img.shields.io/badge/version-0.1.0-2B8ABF)
  ![license](https://img.shields.io/badge/license-MIT-blue)
  ![herdr](https://img.shields.io/badge/herdr-%E2%89%A5%200.8.2-4AABDF)
</div>

---

Run Targets turns the services a repository declares into a dashboard you keep
open: a pane on the left listing every target and its state, a column of service
panes on the right. Launch several at once, then stop, restart or close them one
by one — without retyping a command or hunting for the right pane.

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

Press your key. A tab named `run` opens with the dashboard:

```
RUN TARGETS  my-project

> api         idle
  web         idle

VIEW  e edit  q close
```

Press `e`, `space` on each, then `enter`. Two panes appear beside the dashboard,
each named after its target:

```
RUN TARGETS  my-project

  api         running
> web         running
```

An hour later the API needs a restart after a config change. Cursor on `api`,
then `e` `r`. It stops, restarts in the same pane, and the previous output is
still above it.

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
to the list. Local targets show a trailing `*` in the dashboard. Either file
alone is enough, so you can try a target without committing anything, and a
broken file never costs you the targets of the other one.

### Drive the dashboard

The dashboard starts read only — press `e` before anything can act on a service.

| View mode | |
| --- | --- |
| `↑` `↓` / `j` `k` | move the cursor |
| `e` | enter edit mode |
| `q` | close the dashboard |

| Edit mode | |
| --- | --- |
| `space` | check / uncheck |
| `enter` | start |
| `s` | stop |
| `r` | restart |
| `x` | close the service's pane |
| `esc` | uncheck everything, back to view mode |

An action applies to every checked row, or to the row under the cursor when
nothing is checked. Check several and start, stop or restart them in one press.

## 📊 States

| State | Meaning |
| --- | --- |
| `running` | the service is up |
| `stopped` | you stopped it from the dashboard |
| `exited` | it stopped on its own — go read its pane |
| `idle` | not started yet |
| `gone` | its pane was closed |

## ⚠️ Worth knowing

**Targets run as commands in your shell**, and `.herdr-run.toml` is committed
with the repository: give it the same trust you give a `Makefile` in a fresh
clone.

**Stopping keeps the pane and its output.** A server that just crashed keeps its
logs on screen, and restarting reuses the same pane. `x` is what removes a pane.

**An action with nothing to do says so** rather than failing silently — pressing
`s` on a stopped service prints `api: already stopped, stop skipped`.

**Restarting waits for the service to actually stop**, up to three seconds. A
service that ignores the interrupt is left alone rather than being sent a
command it cannot read.

**Closing the dashboard with nothing running closes its tab.** With services
still up, only the dashboard pane goes and the key brings it back in place.
Careful: Herdr closes a workspace along with its last tab.

**The service column gets cramped past two or three services** — panes are added
by splitting, so you will want to drag the dividers yourself.

## 🩺 Troubleshooting

```bash
herdr plugin log list --plugin fantoine.run-targets --limit 20
```

| Message | What to do |
| --- | --- |
| `No targets in <repository>. Add .herdr-run.toml or .herdr-run.local.toml` | No configuration was found. If the repository name is not the one you expected, the dashboard opened on the wrong directory. |
| `<file>: invalid TOML (...)` | Fix the syntax; the other file still applies meanwhile. |
| `<file>: target 'x' has an unsafe cwd; skipped` | `cwd` must stay inside the repository — no absolute path, no `..`. |
| `<name>: still running after stop, restart skipped` | The service ignored the interrupt. Stop it yourself in its pane, then start it again. |
| `<name>: no pane of ours to split from` | The dashboard pane is gone. Press the key twice to bring it back. |
| `... is not inside a git repository.` | Open the dashboard from a directory inside your project. |

## 🧪 Development

```bash
python3 -m unittest discover -s tests -v
```

Standard library only; nothing to install.

## License

MIT

# Handoff — 2026-08-12

Written for whoever picks this up next, including a future me with no memory of the
session. This replaces the 2026-07-30 handoff, which was written before the UI, voice,
and hand tracking existed.

**Immediate context: ARC is being moved to a new Mac.** From here on, development happens
on the new machine and ARC is *used* on the old one, so the repo has to survive being
pulled in both directions. Start at the setup section below, then read
[Working across two Macs](#working-across-two-macs) — it is the part that keeps them from
drifting apart.

---

## Setting up on a new Mac

Eight steps. Step 3 does the heavy lifting; step 5 is the one people forget.

### 1. Prerequisites

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
brew install python@3.12
```

ARC requires Python 3.12 (`pyproject.toml`: `requires-python = ">=3.12"`). The old Mac
ran 3.12.13 from Homebrew.

### 2. Clone

```bash
mkdir -p ~/projects && cd ~/projects
git clone https://github.com/dhruvA207/ARC.git
cd ARC
```

### 3. Build the environment

```bash
bin/arc-setup
```

That is the whole of it. The script creates `.venv` on Python 3.12, installs
`pip install -e '.[all]'`, and applies one patch that has to happen every time the venv
is built. It is idempotent — run it again any time, including after a `git pull`.

Two things it handles that a hand-typed `python3.12 -m venv .venv` does not:

- **`--prompt ARC`.** Without it the prompt name defaults to the directory name and you
  get a stray dot: `(.venv)`.
- **The activate patch.** Homebrew's Python ships a broken template:
  `PS1="("__VENV_PROMPT__") ${PS1:-}"` while `venv/__init__.py` sets
  `context.prompt = '(%s) '`, so the substituted value already carries parentheses and
  the prompt comes out doubled — `((ARC) )`. This affects Homebrew python@3.12, 3.13 and
  3.14; Apple's `/usr/bin/python3` is fine. The patch lives inside `.venv/`, which is
  gitignored, so **it is lost on every recreate** — which is exactly why it is in the
  script rather than in this document.

Check it worked:

```bash
source .venv/bin/activate    # prompt should read exactly: (ARC)
```

Use `bin/arc-setup --recreate` to tear `.venv` down and rebuild it from scratch.

Do **not** reinstall `lxml-html-clean` if you see it in an old `pip freeze`. Nothing
imports lxml; it is a leftover from an abandoned extraction experiment.

### 4. Put `ARC` on PATH

```bash
mkdir -p ~/.local/bin
ln -s ~/projects/ARC/bin/ARC ~/.local/bin/ARC
```

If `~/.local/bin` is not already on PATH, add it to `~/.zshrc`:

```bash
export PATH="$HOME/.local/bin:$PATH"
```

`bin/ARC` resolves the repo from its own real path, so the symlink works from anywhere
and does not need the venv activated. macOS filesystems are case-insensitive, so `arc`
reaches the same script — that is why `ARC doctor` refuses with a pointer to the CLI
instead of silently opening a window.

> **Do not activate the venv to launch ARC.** Activating puts `.venv/bin` first on PATH,
> and because the filesystem is case-insensitive, `ARC` then resolves to `.venv/bin/arc`
> — the CLI console script — rather than the launcher. You get argparse usage and no
> window, with nothing written to `ui.log` because the launcher never ran:
>
> ```
> arc: error: the following arguments are required: command
> ```
>
> `deactivate` and the launcher works again. Activation is for development only; the
> launcher calls `.venv/bin/python` by absolute path and never needs it. From an
> activated shell, launch with the full path `~/.local/bin/ARC`.

### 5. Recreate `config/secrets.yaml`

It is gitignored, so the clone will not have it. Without it ARC cannot speak — speech
*recognition* still works, only output breaks.

```yaml
# config/secrets.yaml
gemini_api_key: <key>
```

The key can also come from a `GEMINI_API_KEY` environment variable or
`~/.arc/secrets.yaml`; that is the lookup order in `arc/voice/gemini.py`. On the old Mac
it was copied from `~/projects/Jarvis/config/api_keys.json`.

### 6. Bring across `~/.arc/`

**`memory.db` is the only part of ARC that cannot be rebuilt from this repository.**
Everything else — weights, logs, screenshots, `hardware.json` — regenerates.

Follow `docs/BACKUP.md`; its "Restoring" section is exactly this procedure. In short:
back up `memory.db`, `config.yaml`, `audit/` and `tasks/` from the old Mac, copy them
into `~/.arc/` on the new one, and skip `models/`, `logs/`, `screenshots/`.

**Do not copy `hardware.json`** — it describes the old machine, and model sizing is
derived from it.

Note that `BACKUP.md` opens by saying it is "not a migration guide" because ARC was never
going to move machines. That framing is now out of date; the procedure itself is correct
and was verified end to end.

### 7. Probe the machine and fetch weights

```bash
.venv/bin/arc probe                        # writes a fresh ~/.arc/hardware.json
.venv/bin/arc doctor                       # read the "recommended model" line
.venv/bin/arc model pull qwen3-4b-instruct # 2.12 GB
.venv/bin/arc model use qwen3-4b-instruct
```

The registry (`config/models.yaml`) holds two entries: `qwen3-4b-instruct` (2.12 GB) and
`qwen3-8b` (~4.5 GB). Which is right depends on the new machine — the old one was a
16 GB M3 Air with ~11.5 GB realistically usable, where both fit but the 8B throttled
after 10–20 minutes because the Air is fanless (ADR-010). **If the new Mac has more
memory or a fan, that trade-off changes** and `qwen3-8b` may simply be the better
default. Let `arc doctor` tell you rather than assuming.

The embedding model (`BAAI/bge-small-en-v1.5`, ~130 MB) downloads itself on first use.

### 8. Grant macOS permissions

```bash
.venv/bin/arc voice grant   # triggers the microphone and speech prompts
.venv/bin/arc doctor        # confirms what is still missing
```

Then grant these in **System Settings → Privacy & Security**:

| Permission | Needed for | Granted to |
|---|---|---|
| Accessibility | reading the screen structurally, synthetic clicks | your terminal app |
| Screen Recording | screen capture, OCR | your terminal app |
| Microphone | voice input | your terminal app |
| Speech Recognition | on-device transcription | your terminal app |
| Camera | hand tracking | your terminal app |

**These attach to the terminal application, not to Python.** macOS assigns the grant to
the *responsible* process, which is whatever launched ARC. Two consequences: restart the
terminal after granting Screen Recording, and if you later launch ARC from a different
terminal app you will have to grant them again there.

Without Accessibility, synthetic clicks silently do nothing — they do not error.

### Verify the whole thing

```bash
.venv/bin/python -m pytest      # expect 788 passed
.venv/bin/ruff check .          # expect all checks passed
.venv/bin/arc doctor            # expect 0 failed
ARC                             # window opens, prompt returns immediately
curl -s http://127.0.0.1:8787/health
```

`mypy arc` reports 5 errors in `arc/vision/hands/{tracker,cameras}.py`. Those are
pre-existing and unrelated to setup — do not treat them as a broken install.

---

## Working across two Macs

Code is written on the new Mac; ARC is run on the old one. That split works as long as
one rule holds:

> **Everything a machine needs to run ARC is either in git, or is created by
> `bin/arc-setup`. Nothing is installed by hand.**

### Catching up after a pull

```bash
git pull && bin/arc-setup
```

`arc-setup` is idempotent and takes a couple of seconds when nothing has changed, so it
costs nothing to make it a habit. If the pull added a dependency, this is what installs
it; if it did not, the script says so and exits.

### Adding a dependency

Add it to the right extra in `pyproject.toml` and give it a row in
`docs/DEPENDENCIES.md` — licence verified from wheel metadata, per hard rule 2. Do **not**
`pip install` it directly. A package installed by hand works on the machine you typed it
on and is invisible to the other one, which is exactly how this repo ended up with four
undeclared runtime dependencies that made a fresh clone fail.

`pyproject.toml`'s `all` extra composes every other extra, so a package added to `memory`,
`screen`, `voice`, `app`, or `camera` arrives on the other Mac through the same
`bin/arc-setup`. Nothing else needs updating.

### What git does not carry

Per-machine, and correctly so — do not try to force these through the repo:

| | Why not, and what to do |
|---|---|
| `.venv/` | Gitignored, architecture-specific. `bin/arc-setup` rebuilds it. |
| `config/secrets.yaml` | Gitignored — it holds the Gemini key. Copy it across by hand, once. |
| `~/.arc/hardware.json` | Describes *that* machine. Regenerate with `arc probe`; never copy. |
| `~/.arc/models/` | Multi-gigabyte weights. `arc model pull` refetches them. |
| `~/.arc/config.yaml` | Machine-local overrides, chiefly which model is active. The two Macs may reasonably want different models. |
| `~/.arc/memory.db` | **The interesting one — see below.** |

### Memory does not sync, and should not

`memory.db` is not in git and cannot be. If you use ARC on the old Mac while developing
on the new one, the two memory databases diverge immediately and permanently — there is
no merge for this, and nothing in ARC attempts one.

That is fine as long as you decide which machine is the real one. **The Mac you actually
talk to is the one whose `memory.db` matters**; the other is a development copy whose
memories are throwaway test data. If that ever stops being true, copy it deliberately
with the `sqlite3 .backup` procedure in `docs/BACKUP.md` — a straight `cp` of a
WAL-mode database while ARC is running can capture it mid-transaction.

---

## What ARC is

A local-first personal assistant, spec'd in `docs/BRIEF.md`. It runs entirely on Dhruv's
Mac: a local language model, memory that survives restarts, unrestricted access to the
machine, screen reading and control, voice, hand tracking, and web research.

## Read these first

| File | Why |
|---|---|
| `docs/BRIEF.md` | The spec. Dhruv wrote it; it is the source of truth. §6 is shelved. |
| `docs/DECISIONS.md` | 24 ADRs. **Read before changing anything architectural** — most surprising choices are deliberate and explained. |
| `docs/ARCHITECTURE.md` | What exists, plus measured performance costs. |
| `docs/DEPENDENCIES.md` | Every dependency, its licence, and why it earns its place. |
| `docs/BACKUP.md` | How to back up `~/.arc/`. Verified end to end. |

---

## Hard rules, learned the hard way

**1. Commits are Dhruv's alone.** No `Co-Authored-By: Claude` trailer, no "Generated with
Claude Code" footer. Thirteen commits were rewritten to strip it, and a `git pull` merge
later reattached every one of them to `main` permanently. Author is
`dhruvA207 <dhruvagrawal.v@gmail.com>`. **When `main` has diverged, rebase — never
merge.** `git rebase origin/main` replays commits without authoring a merge commit that
could carry the trailer back in.

**2. Every dependency is Apache-2.0 or MIT.** Verify from wheel metadata or the live
model card — not from memory. A licence audit killed the intended web stack when
`trafilatura → courlan → tld` turned out to include GPL-2.0, and `trafilatura` itself had
been GPLv3+ until v1.8.0. Two MPL-2.0 packages (`certifi`, `pathspec`) are in the tree via
the MLX stack and dev tooling; recorded, not hidden.

**3. ARC is a macOS application.** ADR-021. Phase 8's "Windows readiness" is cancelled.
`platform/windows.py` and `linux.py` stay as stubs — the abstraction earns its keep by
letting the core import without any Apple framework, which is verified in the test suite,
not assumed. Moving to a new Mac does not change this.

**4. Track B is shelved.** ADR-023. No training, no fine-tuning, no GPU rental. Do not
re-propose it. `BRIEF.md` §6, `config/training.yaml`, and `ML_CURRICULUM.md` are kept as
dead records of how the scope moved.

**5. §0.3 access is deliberate.** Unrestricted filesystem, arbitrary shell, no permission
prompts, no deny-list. Dhruv specified this explicitly and said not to negotiate it. The
safeguards he asked for instead — and which must stay — are the audit log, `arc-kill`,
and `--dry-run`.

---

## Current state

All 8 phases of §5 are complete, plus the UI, voice, and hand tracking built since.
**788 tests pass**, `ruff check` clean, `main` is pushed and level with `origin/main`.

Built since the last handoff: `interface/webui/` and the app window, the live voice
session (`voice/live.py`), hand tracking (`vision/hands/`), and the `apps`, `control`
and `messaging` tool modules. 42 tools total.

## Commands

```bash
ARC                         # launch, and only that: the app window plus the server
arc doctor                  # environment + macOS permission grants
arc chat                    # REPL; /memory /recall /why /model /tokens
arc do "<task>"             # multi-step work; --dry-run, --no-server
arc research "<q>"          # shallow; --deep for corroborated Research Mode
arc voice status|grant|say|listen
arc memory stats|search|add|forget|export|consolidate
arc model list|pull|use|remove
arc control status|release|demo     # demo shows the blue glow for 5s
arc control test                    # drives the pointer in a square, verifies each move
arc task list|show|resume
arc tools
arc serve                   # keeps the model warm; 11.5s → 8.7s per task
arc-kill                    # standalone; works when arc itself does not
```

`ARC` takes no arguments and does nothing but launch. The CLI is a separate thing, run as
`.venv/bin/arc <subcommand>` from the repo, or with the venv activated. Only `ARC` is
symlinked onto PATH; `arc` and `arc-kill` live in `.venv/bin/`.

**`ARC` launches detached** — the window opens, the prompt comes straight back, and
output goes to `~/.arc/logs/ui.log` rather than over your terminal. Closing the terminal
does not take ARC down. A second `ARC` refuses rather than starting a competing copy.

---

## The one thing that will mislead you

**The model is the weak link, not the code.** Qwen3-4B at 4-bit:

- Chained `list_directory → read_file → read_file → write_file` correctly, then summed
  2 + 1 and wrote **4**.
- Answered 17 × 23 as **491**.

When output looks wrong, check whether the tools returned the right thing before
suspecting a bug:

```bash
arc task show <id>     # exactly what each tool returned, step by step
```

---

## Measured performance

Profiled rather than guessed (§5), on the 16 GB M3 Air. **Re-measure on the new machine**
— these numbers are a baseline to compare against, not a spec.

| Operation | Cost |
|---|---|
| CLI startup | 0.05–0.11 s |
| memory recall | 0.007 s |
| embed batch of 32 | 0.024 s |
| accessibility tree | 0.110 s |
| screen capture | 0.184 s |
| **OCR** | **0.72 s** |
| **model load** | **1.98 s** |

Model load is why `arc serve` exists. Deep research is 1–3 minutes because claim
extraction is one model call per page — inherent at ~14 tok/s, not a bug.

---

## Bugs found the hard way, and what they teach

Twenty-plus, mostly found by *using* the thing rather than by tests passing. The ones
worth knowing because the same mistake is easy to repeat:

**Silent-failure class** — these all returned plausible results while being wrong:

- `bool` is a subclass of `int`, so every boolean tool parameter was typed `"integer"`
  and the model would send `1`/`0` for flags.
- `_coerce_arguments` read `inspect.Parameter.annotation`, which returns *strings* under
  `from __future__ import annotations` — used by every tool module. Coercion was
  completely dead in production while passing any test written without that import.
- The docstring parser broke at the blank line before `Args:`, the standard format, so
  every tool shipped with undocumented parameters.
- mdBook puts `class="sidebar-visible"` on `<html>`; substring-matching "sidebar" flagged
  whole documents as boilerplate. `doc.rust-lang.org` extracted **zero words** while blog
  spam sailed through.
- pyobjc's `AXValueGetValue` *returns* `(ok, value)` rather than filling a struct. Every
  accessibility element's frame came back `None` — 376 elements, zero usable.
- **Twelve config keys were declared and never read**, values hardcoded in the modules.
  Editing them did nothing. Worse than a missing key, which at least fails loudly.

**Interaction class** — only visible when subsystems met:

- Salience multiplied fused retrieval scores directly, and layer defaults (0.6–1.5)
  swamped RRF's ~3× range. A query containing "router bug" returned a preference about
  tidying Downloads.
- Consolidation ran dedupe *before* promotion, destroying the repeated phrasings that
  promotion counts. Promotion silently never fired.
- Memories rendered as `- fact [episodic, 2026-07-30]` got copied by the model: asked for
  "pong" it replied `pong [episodic, 2026-07-30]`, which was then stored and recalled,
  producing two markers next turn. **Instructing it not to did not work — the format had
  to change.**

**The lesson:** write adversarial probes, not just tests. Most of these passed a green
suite.

---

## Things left undone, honestly

- **`arc chat` does not use the warm server.** Only `arc do` does. Straightforward to add.
- **Deep research is slow** (1–3 min). Inherent to local inference.
- **Bing search is unreliable** and DuckDuckGo is primary (ADR-020 addendum). Dhruv wanted
  Bing/Google; they return ads and JavaScript shells to non-browser clients. A keyed
  search API is the only real fix, and he has not asked for one.
- **`robots.txt` compliance is disabled** in `config/default.yaml` at his explicit
  request. It logs a warning at every startup.
- **The VLM is not integrated.** §4.3's ladder stops at OCR. The accessibility tree plus
  OCR has been sufficient so far; whether a VLM can co-reside with the chat model depends
  on the new machine's memory.
- **`arc/model/custom.py` is not built** — it was Track B's socket.
- **5 mypy errors** in `arc/vision/hands/{tracker,cameras}.py`: two are missing mediapipe
  stubs, three are a real `int | None` assignment in `cameras.py:100`.

---

## Where things live

```
arc/
├── __main__.py      CLI — every command dispatches from here
├── config.py        layered YAML + env; every key IS read (ADR-022)
├── hardware.py      probe → sizing; reads config, does not hardcode
├── audit/           append-only log + standalone kill switch
├── model/           base.py is the swappable brain — 5 members, keep it narrow
├── memory/          store, 3 layers, hybrid retrieval, consolidation
├── agent/           loop, executor, tolerant parser, journal
├── tools/           42 tools; @tool decorator derives schemas from type hints
├── web/             fetch, extract, search, research, deep
├── vision/          capture, accessibility tree, OCR, hands/
├── control/         session, blue-glow overlay, input
├── interface/       chat REPL, HTTP server, app window, webui/
├── voice/           macOS speech in, Gemini speech out, live session
└── platform/        macos.py real; windows/linux stubs
```

Runtime state is all under `~/.arc/` — `memory.db` is the only irreplaceable part.

ARC's accent colour is `#4A9EFF`. Dhruv asked for that glow to look like
claude-in-chrome.

**Ask him about scope before building.** He has been decisive and has changed direction
several times when something was not worth doing — the fastest path is to lay out options
rather than guess.

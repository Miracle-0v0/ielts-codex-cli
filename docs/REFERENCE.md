# IELTS Codex — Reference

This document describes version `1.0.0`. Native terminal checks have passed on
Windows/Python 3.13.9, Ubuntu/Python 3.12.13 and macOS/Python 3.10.20. The scope,
validation evidence and publication process are in [1.0_PLAN.md](1.0_PLAN.md).

The trainer uses only the Python standard library. Core study works offline,
without an account or API key. Optional updates and custom-pet generation
connect only when requested. This independent project is neither an OpenAI
product nor an official IELTS service.

## Installation

Python 3.10+ is required. Run `install.bat` on Windows or `./install.sh`
on macOS/Linux, then open a new terminal and check `ielts --version`. These
commands point to the source checkout, which must remain in place. Portable
launchers are `run.bat` and `./run.sh`; `python ielts.py` also works.

Without compatible Python, launchers offer an explicitly confirmed Astral uv
installation of isolated Python 3.12 under `.ielts-bootstrap`. Bootstrap
requires network access; study does not. `IELTS_CODEX_PYTHON` selects an
interpreter, and `IELTS_CODEX_NO_AUTO_INSTALL=1` prohibits the download.

A package installation uses `python -m pip install .` and provides both
`ielts` and `ielts-codex`. Source and installed versions depend on the
revision obtained; check `ielts --version` after installation. See
[INSTALLATION.md](INSTALLATION.md) for troubleshooting and uninstalling while
keeping study records.

## Interactive commands

| Command | Description |
| --- | --- |
| `/study [minutes]` | Start or resume a saved plan; 5–60 minute budget |
| `/study new [minutes]` | Replace remaining tasks; keep completed history |
| `/learn [count] [topic]` | Learn unseen words; default 10 |
| `/review [count] [topic]` | Due recognition review |
| `/quiz [count] [topic]` | Chinese-to-English spelling |
| `/context [count]` | Fixed-choice word forms and collocations |
| `/mistakes [recall\|spelling\|context]` | Pending weaknesses and task-specific recent results |
| `/mistakes practice [task]` | Practice pending difficulties |
| `/import "file.csv" [deck]` | Preview, validate and confirm CSV or JSON import |
| `/import undo [batch_id]` | Undo an import, retaining learning history |
| `/add <word>` | Collect a pending entry |
| `/decks [name\|all]` | List or select a content pack |
| `/search <query>` | Search English, Chinese meaning or synonyms |
| `/words [topic]` / `/topics` | Browse vocabulary or topic progress |
| `/today` / `/today words` | Today's progress or bilingual word list |
| `/curve` | Conceptual forgetting curve |
| `/stats` | Exposure, task-specific results and stable review |
| `/goal <count>` | Daily review goal |
| `/backup` / `/backups` / `/restore <name>` | Backup, list and restore |
| `/update` / `/update status` | Offline program/content/reference status |
| `/update program [--dry-run]` | Check or install a stable program release |
| `/update dictionary [--force] [--dry-run]` | Preview external references, then confirm saving |
| `/update dictionary status` | Local reference details |
| `/update dictionary rollback` | Confirm restoration of the last prior reference |
| `/game [count] [topic]` | Pixel spelling expedition |
| `/game pet create <image>` / `/game pet status` | Optional custom pet |
| `/game providers` | API provider profiles |
| `/game code [code]` | Session-only game codes |
| `/game music [on\|off\|status]` | Local BGM preference |
| `/clear` / `/quit` | Clear the screen / exit |

The legacy `/sync` command aliases the dictionary-specific operation. An
unqualified `/update` now shows offline choices; it no longer starts both
network jobs. Select program or dictionary explicitly.

Type `/` for the command palette. Arrow keys select, Enter runs, and Tab or
right arrow completes. Non-interactive input uses a line prompt. Counts and
topics can appear in either order, such as `/learn environment 8`. A bare
word opens its dictionary card; stable IDs distinguish same-spelling senses.

## Daily study and answers

The first `/study 20` asks for time, primary difficulty and content pack.
The time is a task budget rather than a countdown or scoring criterion. Due
tasks come first; new words and unverified abilities follow. Backlogs reduce
new material. After at least three inactive days, a recovery plan temporarily
pauses new words and limits the initial task count.

A completed answer and plan position save together. `q` returns; the next
`/study` resumes the same saved plan, including across process restarts.
`s` skips without inventing an answer. `/study new` replaces remaining
tasks while preserving history. Selecting another deck does not silently
replace an existing plan.

On recall cards, Enter reveals the answer, `h` shows a hint, and ratings
1/2/3/4 mean Again/Hard/Good/Easy. Again returns later in the group, at most
three attempts per word. Daily plans also have a total attempt limit. Hints and
retries are recorded separately. Successful relearning is due tomorrow;
same-day success and early practice do not repeatedly lengthen intervals, and
Hard does not add successful repetitions.

Spelling ignores only outer whitespace and case. Other accepted answers must
be explicitly listed; internal spaces, digits and punctuation are not removed.
Context questions accept the correct option number or exact option text,
ignoring outer whitespace and case. Unlisted free text prompts another choice;
the program does not grade open-ended sentence production.

## Independent abilities and data

Recognition, spelling and context each have their own schedule and evidence.
One ability's success does not postpone another's review. `/mistakes`
shows unresolved difficulties and the related word's recent task statuses;
untested abilities stay unverified. No context question is automatically
generated for a personal word.

Stable review requires at least three consecutive delayed, unhinted Good/Easy
results in one task, with the latest gap since recorded practice at least seven
days. The first detailed event establishes a baseline. Again, Hard or hints
reset the streak, and session retries do not build it. Game outcomes are
separate; navigation or survival failures do not create core study backlog.

These are project rules, not IELTS score predictions or measurements of memory.
Context stability refers only to the fixed-choice tasks and does not establish
free sentence production or complete command of a word. The forgetting curve
is likewise a conceptual illustration.

Data lives in `~/.ielts-codex`, relocatable with `--data-dir` or
`IELTS_CODEX_HOME`. Schema 3 stores cards, independent schedules, settings,
attempts, personal vocabulary, import history and the saved plan. OS locking
and snapshot comparison reject stale writers before replacement.

Schema 1/2 progress is read without overwriting it. Before the first save as
schema 3, the exact original bytes are backed up. Existing settings, cards,
daily totals and available detailed history remain. Legacy shared schedules
become recall schedules only; other ability schedules start from actual
practice, and legacy counts do not become invented delayed-review evidence.

`ielts backups` and `ielts restore <name>` work even with a corrupt main
file. Restore preserves the current file first. The latest ten automatic
backups are retained; migration, manual and pre-restore copies are not pruned.
Game preferences and dictionary references are separate files, so copy the
whole data directory for a complete backup. See
[LEARNING_DATA.md](LEARNING_DATA.md) for recovery and returning to an old version.

## Vocabulary and content

The project includes 72 words across 9 topics, plus 24 original context
questions (13 word-form and 11 collocation). Editorial levels are core,
advanced and extension, not official Band scores. Fixed-choice exercises have
explicit answers, hints and explanations; automatic checks and agent reading
have been performed, without claiming human or external review.

Personal imports accept UTF-8 CSV or JSON. A ready entry requires a word, part
of speech and Chinese sense. Word-only entries stay pending and are excluded
from training; missing optional definitions are left blank. Stable IDs support
different senses of the same spelling. Invalid rows block a batch; duplicate
senses are skipped. Confirmed imports can be undone unless a later edit would
be overwritten, and learning history is retained. See
[VOCABULARY.md](VOCABULARY.md) and the [CSV](../examples/vocabulary.csv) /
[JSON](../examples/vocabulary.json) examples.

## Program and dictionary updates

Program, bundled content and external reference versions are displayed
separately. Ordinary startup and `/update status` never check the network.

`/update program` selects stable three-part releases only. Local
`1.0.0rc1` is ahead of `0.6.10` and can upgrade to final `1.0.0`;
prerelease downloads and downgrades remain rejected. `--dry-run` checks
without installation.

Source updates require official `main`, the expected HTTPS origin, a clean
index and a fast-forward to the release. Pip updates require the exact
pure-Python wheel and verified size, GitHub digest, metadata, paths and RECORD
hashes. Unsupported, dirty or forked installs are refused before changes.

On Windows the validated wheel and `ielts-update.cmd` are staged. Exit all
IELTS Codex windows, then run the printed script; it verifies the wheel again,
installs and checks both commands. Restart and run `ielts --version`.
The trust boundary is GitHub TLS, the official repository and GitHub's digest;
the digest is not an independent maintainer signature.

`/update dictionary` downloads into a temporary preview, shows old/new
references alongside the teaching definition, then asks for confirmation.
`--dry-run` only previews; `--force` refreshes the download. External
references never overwrite teaching definitions, personal senses or answers.
OEWN sense matching is automatic and explicitly labeled as reference-only.

The last prior reference is retained with version and checksum metadata.
Rollback previews and asks for confirmation; the first installation rolls
back to having no external reference. Conflicting changes stop replacement.
Network failure leaves local study available. Keep OEWN source and license
attribution; see [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md).

Direct shell examples:

```text
ielts study 20
ielts import "reading.csv" reading
ielts context -n 5
ielts mistakes practice spelling
ielts update status
ielts update program --dry-run
ielts update dictionary --dry-run
```

## Pocket pixel game mode

The game mode evolves the survival expedition into an original handheld
monster-adventure-style terminal game:

```text
› /game
› /game 3 environment
```

On an interactive terminal, a 14-by-8-tile camera renders each map tile as an
8-by-8 pixel scene: layered grass blades, flower clusters, pebble paths, tree
canopies, a full capped-trainer sprite, a complete companion, and animated
letter creatures. A compact field map, party-style HUD, spell meter, and
dialogue box retain the feeling of an original handheld monster adventure. The
renderer packs a 2-by-4 group of artwork pixels into one Unicode Braille cell,
so each visible pixel is smaller while the detailed art, scene, minimap, and
HUD still fit an 80-by-24 terminal.

Move with `WASD` or the arrow keys and walk into letter monsters in the exact
spelling order. A wrong monster costs hunger. Only a small circular pool around
the player and companion is lit, walls block that light, and animated fog hides
the rest of the map. The minimap remembers explored terrain without exposing
unseen monsters or walls. Taking too long progresses through hunger, dizziness,
and health loss; the companion follows behind and adds a second small light.

The two help channels are intentionally separate:

- `h` advances through learning hints: phonetics, a cloze example, synonyms,
  and finally the next letter.
- `g` asks the pet for a rough direction to the current target without
  revealing the letter.

Hints are drawn only from the existing curated word fields. The vision API does
not invent definitions, etymologies, examples, or mnemonics. A successful round records game performance separately from independent
accuracy and stable-review evidence. Passive pet visibility is free. Requesting a learning hint or pet
direction caps the result at `Hard`, while directly revealing the next letter
records `Again`.

Interactive terminals use a smooth alternate-screen animation that redraws
only changed rows and caps display output at 10 frames per second, even while a
key is held down. On POSIX terminals, IELTS Codex measures the micro-pixel
Braille glyph's cell width before starting: profiles that render it as
double-width, or do not answer the width probe, automatically use the text-only
turn-based fallback. Non-TTY input and `TERM=dumb` use the same fallback so
redirected or assistive input is not punished by wall-clock time. A pixel
session requires at least 80 columns and 24 rows. Resizing below that limit
pauses the game clock until the window is restored.

### Original BGM

The game can play a short, original chiptune loop in a compatible interactive
terminal. The WAV is synthesized locally from the built-in score—nothing is
downloaded, and no third-party game music or samples are used. macOS uses
`afplay`; other platforms use an available local `ffplay`, `mpv`, `aplay`, or
the Windows standard-library player. If no player is available, the game stays
silent without affecting play or timing.

Use `m` during an expedition for an immediate toggle, or manage the saved
preference before a session:

```text
› /game music status
› /game music off
› /game music on
```

Set `IELTS_CODEX_GAME_BGM=0` to start a process with music disabled.

Set `IELTS_CODEX_GAME_TURN_BASED=1` to choose the compatibility mode directly.
`IELTS_CODEX_GAME_FORCE_PIXEL=1` skips only the POSIX character-width probe;
TTY and minimum-size checks still apply.

### Mystery codes

Enter a code from the main interactive prompt:

```text
› /game code WhosYourDaddy
```

You may also put the code on the same line. `WhosYourDaddy` enables
invincibility, while `ISeeDeadPeople` reveals the full map and removes the fog
of war. These effects last only for the current IELTS Codex process and are
never written to `game.json`.

```text
› /game code ISeeDeadPeople
› /game code status
› /game code reset
```

### Create a pet from an image

The game includes a complete, animated offline puppy that follows the player
and opens the fog by default. Creating a custom pet is optional and replaces
that puppy's appearance using your own vision-model account and API key. The
API returns a strictly validated three-colour 8-by-8 indexed pixel sprite; it
cannot return terminal escape sequences or executable drawing instructions.
Existing 7-by-6 companion saves remain supported and are safely padded into the
larger field canvas when rendered.
Configure a provider before launching IELTS Codex:

```bash
export IELTS_CODEX_GAME_PROVIDER=kimi
export IELTS_CODEX_GAME_MODEL='<vision-capable-model-id>'
read -rsp 'API key: ' IELTS_CODEX_GAME_API_KEY
export IELTS_CODEX_GAME_API_KEY
ielts
```

Then run:

```text
› /game pet create ./my-pet-photo.jpg
```

Supported provider profiles use OpenAI-compatible Chat Completions:

| Provider value | Default request endpoint |
| --- | --- |
| `openai` | `https://api.openai.com/v1/chat/completions` |
| `kimi` | `https://api.moonshot.ai/v1/chat/completions` |
| `qwen` | `https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions` |
| `glm` | `https://open.bigmodel.cn/api/paas/v4/chat/completions` |
| `custom` | Set `IELTS_CODEX_GAME_API_URL` |

No model ID is hard-coded because available multimodal models change. Select a
model that accepts image input. `IELTS_CODEX_GAME_API_URL` can also override a
named profile, which is useful for a regional or workspace-specific Qwen
endpoint and for compatible gateways.

Before any upload, the CLI displays the provider, model, destination host,
image type, and image size, then asks for explicit confirmation. The API key,
raw image, Base64 payload, and original path are never written to the progress
store, and redirects are refused so the request cannot silently change hosts.
Only the validated pet profile, provider/model metadata, destination host,
timestamp, and image SHA-256 digest are saved locally.

The provider schemas and endpoints follow the official
[OpenAI Chat Completions](https://platform.openai.com/docs/api-reference/chat),
[Kimi Chat Completion](https://platform.kimi.ai/docs/api/chat),
[Qwen OpenAI-compatible Chat](https://help.aliyun.com/en/model-studio/qwen-api-via-openai-chat-completions),
and [GLM vision-model](https://docs.bigmodel.cn/cn/guide/models/free/glm-4.6v-flash)
documentation. Provider charges and data policies belong to the selected
service; IELTS Codex does not proxy the request.

## Contributing and license

See [CONTRIBUTING.md](../CONTRIBUTING.md), [1.0_PLAN.md](1.0_PLAN.md) and
[CHANGELOG.md](../CHANGELOG.md). Code and project-maintained content use the
[MIT License](../LICENSE); optional OEWN references retain their own terms in
[THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md).

# Contributing to IELTS Codex

Thank you for helping improve IELTS Codex. Bug fixes, terminal-experience
improvements, carefully reviewed vocabulary additions, and documentation
updates are welcome.

## Development setup

The project requires only Python 3.10 or later:

```bash
git clone https://github.com/Miracle-0v0/ielts-codex-cli.git
cd ielts-codex-cli
./run.sh
```

On Windows Command Prompt, use the native launcher instead:

```bat
run.bat
```

To register the same Codex-style `ielts` command that users run, execute
`./install.sh` on Ubuntu/macOS or `install.bat` on Windows. These installers
operate at user level and keep the command linked to the source checkout.

Run the local checks (on Windows, use `python` in place of `python3`; the tests
use temporary directories and do not read personal progress):

```bash
# Ensure python3 resolves to Python 3.10 or later.
python3 -m unittest discover -s tests -v
python3 -m compileall -q src
./run.sh --version
python3 -m pip wheel --no-deps --wheel-dir dist .
IELTS_CODEX_TEST_WHEEL_DIR=dist python3 -m unittest discover -s tests -p test_release.py -v
python3 -m pip install .
ielts --version
ielts-codex --version
```

After package installation, exercise native terminal input with the command for
your platform:

```bash
# Linux / macOS
python3 scripts/smoke_posix_terminal.py --report-dir terminal-results/posix
# Windows
python scripts/smoke_windows_terminal.py --installed --output terminal-results/windows
```

These checks use temporary synthetic progress; reports go into the ignored
`terminal-results/` directory.

The repository's launcher compatibility workflow runs learning/data tests, builds
and checks the wheel (including every bundled JSON resource), installs that
wheel, and checks native terminal interaction, commands, source installers and
launchers on Ubuntu, macOS, and Windows. It runs for pushes to `main` or
`release/**`, pull requests, manual dispatches, and the `v1.0.0` release tag.
Behavior and terminal reports are kept as workflow artifacts. Record the
actual run result when reporting validation.

In PowerShell, set `$env:IELTS_CODEX_TEST_WHEEL_DIR = "dist"` before the wheel
test and use `python` instead of `python3`. Core tests use temporary directories.
Before a final release, exercise onboarding, study/resume, import/undo, recovery
and terminal input on each supported platform; automatic version checks do not
replace those interactions.

## Project layout

```text
src/ielts_codex/     Application code and bundled data
tests/              Behavioral tests and small synthetic migration fixtures
examples/           Editable CSV and JSON vocabulary examples
docs/               Learning, vocabulary, installation and release guidance
scripts/            Maintenance, demo generation and native terminal checks
.github/workflows/  Compatibility and behavioral checks
```

The root keeps project metadata, README/changelog/license files and the public
`ielts.py`, `run.*` and `install.*` entry points. Keep these paths stable so source
installations continue to work. Store new documentation under `docs/` and tests
under `tests/`; do not put learning progress or temporary verification output in
the repository.

The 1.0 scope and release process are described in [docs/1.0_PLAN.md](docs/1.0_PLAN.md).
The current command reference is [docs/REFERENCE.md](docs/REFERENCE.md).

## Refresh the README demo

Run the recorder from the repository root with the current source:

```text
python -m pip install Pillow
python scripts/render_demo_gif.py
```

On macOS/Linux also install `pexpect` and a CJK font, or pass `--font PATH`.
Windows uses the standard-library ConPTY helper and installed system fonts.
The recorder runs real commands against a fresh temporary profile, saves the
GIF to `docs/demo.gif`, and keeps the raw session in ignored
`terminal-results/demo/`. It reads the title version from the application.
Personal progress is never opened; Pillow and pexpect are development tools,
not application dependencies. Re-record when the demonstrated flow changes.

## Pull requests

1. Create a focused branch from `main`.
2. Keep the application free of runtime dependencies unless a new dependency
   provides clearly justified value.
3. Validate the affected interactive flows locally when behavior changes.
4. Keep vocabulary IDs stable. Bundled teaching entries should include accurate
   part of speech, English/Chinese meanings, examples and relevant usage fields;
   record the source and license. Use `core`, `advanced` or `extension` as
   editorial levels, not a claimed IELTS score. List spelling variants in
   `accepted_answers`; never use similarity as an acceptance rule. Personal
   imports may omit optional fields, and unfinished entries stay pending.
   See [docs/VOCABULARY.md](docs/VOCABULARY.md).
   Context questions need explicit choices, an unambiguous answer and an
   explanation. Check meaning, grammar, distractors and copyright; structural
   validation alone does not establish educational quality. Do not claim a
   human review unless it actually happened.
5. Explain the purpose of the change and the local verification performed in
   the pull request.

Do not commit personal learning progress, virtual environments, build
artifacts, or credentials.

## Versioning

The 1.0.0 release workflow runs behavior, package and native terminal checks
on Windows, Ubuntu and macOS with Python 3.10. Publication requires every
platform job to pass for the release tag, matching package versions and a tag
commit in official main history. See [the release process](docs/1.0_PLAN.md).

Update `pyproject.toml` and `src/ielts_codex/__init__.py` together.
Bundled-content and external-reference versions are separate. The updater
downloads stable releases only; a local prerelease can upgrade to its
same-number final version.

The project follows Semantic Versioning. Iterative visual, interaction,
animation, compatibility, and other small improvements to an existing feature
use patch releases, such as `0.5.0` to `0.5.1`. New commands or substantial new
capabilities use a minor release; incompatible changes require a major release.

## Reporting issues

Include your operating system, Python version, reproduction command, actual
output, and expected behavior. Do not post security-sensitive or private
information in a public issue.

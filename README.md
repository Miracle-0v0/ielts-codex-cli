# IELTS Codex

[![Tests](https://github.com/Miracle-0v0/ielts-codex-cli/actions/workflows/tests.yml/badge.svg)](https://github.com/Miracle-0v0/ielts-codex-cli/actions/workflows/tests.yml)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

A Codex-inspired IELTS vocabulary trainer for the terminal, featuring slash
commands, focused word cards, spelling practice, instant feedback, spaced
repetition, and local progress tracking.

IELTS Codex uses only the Python standard library. It requires no account,
network connection, API key, or runtime dependency. The current learning
interface is Chinese-first.

> [!NOTE]
> This is an independent open-source learning tool. It is not an official
> OpenAI product and is not affiliated with or endorsed by OpenAI.

## Features

- Codex-style interactive prompt and slash commands
- New-word learning and due-card review
- Chinese-to-English spelling quizzes
- Again / Hard / Good / Easy spaced-repetition ratings
- 72 bundled words across 9 IELTS-oriented topics
- English definitions, Chinese meanings, phonetics, bilingual examples, and synonyms
- Local, atomic JSON progress storage
- Daily goals, streaks, accuracy, and mastery statistics
- English, Chinese, and synonym search
- Zero runtime dependencies

## Quick start

Python 3.10 or later is required.

```bash
git clone https://github.com/Miracle-0v0/ielts-codex-cli.git
cd ielts-codex-cli
./ielts.py
```

You can also install the `ielts-codex` command:

```bash
python3 -m pip install .
ielts-codex
```

## Interactive commands

| Command | Description |
| --- | --- |
| `/learn [count] [topic]` | Learn unseen words; defaults to 10 |
| `/review [count] [topic]` | Review cards that are due today |
| `/quiz [count] [topic]` | Run a Chinese-to-English spelling quiz |
| `/search <query>` | Search by English, Chinese meaning, or synonym |
| `/words [topic]` | Browse words and learned status |
| `/topics` | Show progress by topic |
| `/today` | Show today's plan and goal |
| `/stats` | Show coverage, accuracy, streak, and mastery |
| `/goal <count>` | Change the daily review goal |
| `/clear` | Clear the terminal |
| `/quit` | Save and exit |

The count and topic can appear in either order:

```text
› /learn environment 8
› /review 15 education
› /quiz 5 technology
```

During a learning or review card:

- `Enter` reveals the answer.
- `h` shows a cloze-example hint.
- `s` skips the card without changing its schedule.
- `q` ends the current session.
- `1` / `2` / `3` / `4` rates recall as Again / Hard / Good / Easy.

Enter a bare word to open its dictionary card:

```text
› ubiquitous
```

## Spelling practice

Run:

```text
› /quiz 10
```

The quiz shows a Chinese meaning and asks for the English spelling. A correct
answer advances the card. A misspelling reveals the answer and schedules the
card as `Again`. Use `h` for a hint, `s` to skip, or `q` to stop.

## Non-interactive mode

Commands can also be called directly from a shell:

```bash
python3 ielts.py stats
python3 ielts.py topics
python3 ielts.py search biodiversity
python3 ielts.py learn -n 5 -t environment
python3 ielts.py --no-color stats
```

## Vocabulary data and provenance

The bundled vocabulary is a small, static, project-maintained dataset stored in
[`src/ielts_codex/data/words.json`](src/ielts_codex/data/words.json).

The current 72 entries were written and curated specifically for this project
using general IELTS-oriented vocabulary knowledge. They were not copied from a
commercial dictionary or textbook and are released under the repository's MIT
License. The dataset is not an official Cambridge IELTS word list.

IELTS Codex is not currently connected to Cambridge, Oxford, Collins, or any
other external dictionary or knowledge-base API. There is therefore no upstream
database version to poll and no automatic synchronization process.

Each entry contains:

- `word`
- `phonetic`
- `part_of_speech`
- `meaning_zh`
- `definition_en`
- `example`
- `example_zh`
- `synonyms`
- `topic`
- `band`

### Updating the vocabulary dataset

For a normal manual update:

1. Edit [`words.json`](src/ielts_codex/data/words.json).
2. Keep `word` values lowercase and unique.
3. Provide every required field and verify the phonetic transcription,
   definition, translation, example, topic, and band value.
4. If the total word or topic count changes, update the corresponding
   expectations in
   [`tests/test_word_bank.py`](tests/test_word_bank.py).
5. Run the complete test suite:

   ```bash
   PYTHONPATH=src python3 -m unittest discover -s tests -v
   ```

6. Record the dataset change in [`CHANGELOG.md`](CHANGELOG.md). When publishing
   a new package release, update the version in both `pyproject.toml` and
   `src/ielts_codex/__init__.py`.

If an external vocabulary source is adopted in the future, do not copy or
automatically import it until its redistribution license has been verified.
The project should then add a reproducible importer, record the source name,
source version, retrieval date, and license, validate all imported entries, and
review changes before replacing bundled data.

## Progress storage

Learning progress is stored by default at:

```text
~/.ielts-codex/progress.json
```

Each rating is saved immediately using a temporary file followed by an atomic
replacement. This reduces the chance of corruption after an unexpected exit.

Use `IELTS_CODEX_HOME` or `--data-dir` to choose another location:

```bash
IELTS_CODEX_HOME=./my-progress python3 ielts.py
python3 ielts.py --data-dir ./my-progress stats
```

## Spaced-repetition behavior

- `Again`: a new word remains in today's queue; a lapsed review returns tomorrow.
- `Hard`: uses a short interval and slightly lowers the ease factor.
- `Good`: advances through 1 day, 3 days, and then adaptive intervals.
- `Easy`: moves directly to a longer interval.

A card is counted as mastered after reaching a 21-day interval or at least five
successful repetitions.

## Testing

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

The suite covers vocabulary loading and search, scheduling, atomic persistence,
damaged-file protection, mixed-width terminal rendering, scripted learning, and
spelling quiz behavior.

## Contributing

Issues and pull requests are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md)
before submitting a change.

Release history is available in [CHANGELOG.md](CHANGELOG.md).

## License

This project is available under the [MIT License](LICENSE).

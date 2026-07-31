# Contributing to IELTS Codex

Thank you for helping improve IELTS Codex. Bug fixes, terminal-experience
improvements, carefully reviewed vocabulary additions, and documentation
updates are welcome.

## Development setup

The project requires only Python 3.10 or later:

```bash
git clone https://github.com/Miracle-0v0/ielts-codex-cli.git
cd ielts-codex-cli
python3 ielts.py
```

Run the local checks:

```bash
python3 -m py_compile src/ielts_codex/*.py
python3 ielts.py --version
```

## Pull requests

1. Create a focused branch from `main`.
2. Keep the application free of runtime dependencies unless a new dependency
   provides clearly justified value.
3. Validate the affected interactive flows locally when behavior changes.
4. New vocabulary entries must include an accurate phonetic transcription,
   part of speech, English and Chinese definitions, bilingual examples,
   synonyms, topic, and band level.
5. Explain the purpose of the change and the local verification performed in
   the pull request.

Do not commit personal learning progress, virtual environments, build
artifacts, or credentials.

## Versioning

The project follows Semantic Versioning. Iterative visual, interaction,
animation, compatibility, and other small improvements to an existing feature
use patch releases, such as `0.5.0` to `0.5.1`. New commands or substantial new
capabilities use a minor release; incompatible changes require a major release.

## Reporting issues

Include your operating system, Python version, reproduction command, actual
output, and expected behavior. Do not post security-sensitive or private
information in a public issue.

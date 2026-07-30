# Changelog

This project follows [Semantic Versioning](https://semver.org/).

## [0.3.0] - 2026-07-30

### Added

- Optional, user-approved synchronization of English definitions from the
  Open English WordNet 2025 JSON release.
- An online-sync choice at every interactive startup, plus `/sync` and the
  non-interactive `sync` command.
- A validated, atomically written local OEWN overlay with offline fallback to
  the last valid cache or the bundled vocabulary.
- Per-definition source and license metadata while preserving the
  project-maintained Chinese learning content.
- Open English WordNet, CC BY 4.0, and Princeton WordNet attribution in
  `THIRD_PARTY_NOTICES.md`.
- A reproducible GIF recorded from the real CLI, covering the complete learning
  and synchronization-status workflow.

## [0.2.0] - 2026-07-30

Initial public release.

### Added

- A Codex-inspired interactive shell with slash commands.
- New-word learning, due-card review, and Chinese-to-English spelling quizzes.
- Again, Hard, Good, and Easy spaced-repetition ratings.
- A bilingual vocabulary bank with 72 words across 9 IELTS-oriented topics.
- Atomic local progress storage, daily goals, streaks, and accuracy statistics.
- Search across English words, Chinese meanings, and synonyms.
- An MIT license and open-source contribution guide.

[0.3.0]: https://github.com/Miracle-0v0/ielts-codex-cli/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/Miracle-0v0/ielts-codex-cli/releases/tag/v0.2.0

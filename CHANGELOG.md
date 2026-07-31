# Changelog

This project follows [Semantic Versioning](https://semver.org/).

## [0.4.2] - Unreleased

### Added

- Added a persistent north-up minimap that remembers explored terrain without
  revealing unseen monsters, walls, or floor tiles.
- Added session-only `/game code` input: `WhosYourDaddy` enables
  invincibility and `ISeeDeadPeople` reveals the full map.

### Changed

- Rebalanced the expedition around a small player-and-companion light pool,
  with walls now blocking light and dense fog covering the remaining map.
- Replaced the pixel rain overlay with animated fog and retained the complete
  80-by-24 terminal layout by placing an 8-by-5 scene beside the minimap.
- Reduced macOS terminal load with row-differential redraws and a slower,
  independently animated fog layer while keeping character animation responsive.
- Capped cheat-assisted rounds at `Hard` so mystery codes cannot be recorded as
  unaided recall.

## [0.4.1] - 2026-07-30

### Changed

- Rebuilt `/game` as a terminal-native pixel-art viewport with full player,
  companion, and letter-monster sprites instead of single-character markers.
- Added animated sprite frames, camera tracking, and layered pixel rain and fog.
- Extended image-inspired companion creation with a strictly validated pixel
  palette and sprite.
- Added automatic migration for local 0.4.0 companion saves and retained the
  text-only turn-based fallback for non-interactive terminals.

## [0.4.0] - 2026-07-30

Experimental gameplay release focused on making spelling practice more playful.

### Added

- A `/game` storm expedition where letter monsters must be defeated in exact
  spelling order while hunger, dizziness, health, rain, and fog evolve.
- A Codex-style slash-command palette with filtering, arrow-key selection, and
  command completion.
- A built-in puppy that follows the player, extends visible terrain, and can
  point toward the next target without revealing its letter.
- Progressive learning hints built from the existing curated phonetics,
  examples, and synonyms.
- Optional bring-your-own-key pet creation from an uploaded image through
  OpenAI, Kimi, Qwen, GLM, or a custom OpenAI-compatible endpoint.
- Animated TTY play and an automatic turn-based fallback for non-interactive or
  limited terminals.

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

# Changelog

This project follows [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.6.7] - 2026-08-03

### Added

- Added an interval-scaled terminal Ebbinghaus forgetting curve after every
  `/learn` session, including the current sample size and real upcoming review
  nodes from the spaced-repetition scheduler.
- Added `/curve` so learners can reopen the conceptual forgetting curve at any
  time; empty profiles receive a clear prompt to learn a first group.
- Added a bilingual daily word list to `/today`, separating words completed
  today from cards still due, with `/today words` as a list-only shortcut.

### Changed

- Updated the slash palette, command help, direct CLI commands, English
  documentation, and full-project GIF for the new memory views.

## [0.6.6] - 2026-07-31

### Added

- Added one-time user-level command installers for Ubuntu/macOS (`install.sh`)
  and Windows (`install.bat`) so a source checkout launches with the short
  `ielts` command, backed by the existing Python 3.10+ and isolated uv bootstrap
  launchers.
- Added CI coverage for the `ielts` source-checkout command installer on all
  three operating systems while retaining the established package entry point
  for seamless updates from earlier releases.

### Changed

- Redrew the one-tile trainer around the terminal renderer's 2-by-4 Braille
  micro-cell boundaries, preserving a vivid red cap, cream face and eye, blue
  coat, yellow satchel, and dark silhouette after pixel compression.
- Recoloured the built-in dog with a dark keyline and saturated orange-and-cream
  body so the companion remains distinct on both grass and pale paths.
- Preserved the 14-by-8 field of view, fog, minimap, and animation cadence while
  increasing actor-versus-terrain contrast.
- Updated command help and documentation to use the shorter Codex-style
  `ielts` invocation.

## [0.6.5] - 2026-07-31

### Added

- Added a native Windows Command Prompt launcher, `run.bat`, backed by a
  PowerShell bootstrap that preserves the Python 3.10+ requirement and the
  isolated uv-managed Python 3.12 fallback.
- Added Ubuntu, macOS, and Windows launcher checks, including a Windows uv
  cold-bootstrap test, to the GitHub Actions workflow.

### Fixed

- Documented platform-specific startup commands so Windows users no longer
  attempt to run the Unix-only `./run.sh` command in `cmd.exe`.
- Kept Windows bootstrap files, caches, and Python installations under the
  ignored `.ielts-bootstrap` directory without changing system Python or PATH.

## [0.6.4] - 2026-07-31

### Changed

- Kept Python 3.10 as the minimum supported runtime and replaced all system
  package-manager and third-party-PPA bootstrap paths with a user-confirmed,
  project-local Python 3.12 installation managed by Astral uv.
- Download pinned uv 0.11.32 only when uv is unavailable, without requiring
  `sudo`, modifying APT sources, or depending on the distribution's packages.
- Kept the interpreter, uv executable, and download cache isolated under the
  ignored `.ielts-bootstrap` directory; the fallback does not replace the
  system Python, edit shell profiles, or change PATH.

## [0.6.3] - 2026-07-31

### Fixed

- Switched exact APT package checks to literal package-name-list matching for
  compatibility with older Ubuntu APT releases.
- Allowed the launcher to continue with verified cached package metadata when
  an unrelated configured APT repository fails to refresh, while clearly
  reporting that the broken repository still needs repair.

## [0.6.2] - 2026-07-31

### Fixed

- Made APT package detection use exact, escaped package names, preventing a
  missing `python3.12` package from incorrectly selecting similarly named
  packages such as PostgreSQL's Python extension.
- Added a user-confirmed Ubuntu PPA fallback when configured APT repositories
  do not provide Python 3.10+. The launcher never adds the third-party source
  without an explicit interactive `y` response.

## [0.6.1] - 2026-07-31

### Added

- Added executable `run.sh`, a source-checkout launcher that finds Python 3.10+
  or, with user-visible system package-manager authorization, installs a
  compatible interpreter through APT, DNF, pacman, or Homebrew.

### Changed

- Made direct `ielts.py` launches on Python older than 3.10 fail immediately
  with an actionable compatibility message instead of an internal dataclass
  error.

## [0.6.0] - 2026-07-31

### Added

- Added an optional, locally synthesized original chiptune BGM for interactive
  game sessions, with persisted on/off preference, in-game `m` toggle, and a
  safe silent fallback when no compatible system audio player is available.
- Added `/game music [on|off|status]` for inspecting and managing BGM without
  starting an expedition.

### Changed

- Refined the original pocket-adventure field from 7-by-6 into more detailed
  8-by-8 pixel art: layered grass, flower clusters, pebble paths, textured tree
  canopies, a fuller trainer, companion, and animated letter-creature sprites.
- Repacked artwork into 2-by-4 micro-pixel Braille cells, expanding the field
  camera from 7-by-4 to 14-by-8 tiles while keeping the same 80-by-24 layout.
- Preserved custom companion saves from the earlier 7-by-6 sprite format by
  padding them into the new rendering canvas; newly generated pets use 8-by-8.

## [0.5.1] - 2026-07-31

### Changed

- Display the running IELTS Codex version in the interactive startup and
  `/clear` masthead.

## [0.5.0] - 2026-07-31

### Added

- Added `/game`, a terminal-native pixel-art spelling expedition with complete
  player, companion, and letter-monster sprites, survival pressure, progressive
  learning hints, and shared spaced-repetition progress.
- Added a built-in animated puppy plus optional image-inspired pet creation
  through user-configured OpenAI, Kimi, Qwen, GLM, or compatible vision APIs.
- Added a persistent, fog-safe minimap, wall-blocked local light, and
  session-only `WhosYourDaddy` and `ISeeDeadPeople` mystery codes.
- Added an explicit `/update` command that independently refreshes Open English
  WordNet definitions and checks the official GitHub repository for a newer
  stable IELTS Codex release.
- Added safe source-checkout fast-forward updates and digest-verified
  pure-Python wheel updates with downgrade and prerelease protection.
- Added an offline `/update status` view and online `--dry-run` support.

### Changed

- Removed the network prompt from startup; launching IELTS Codex is now always
  offline unless the user explicitly runs `/update`.
- Replaced the early rain overlay with dense animated fog and an 8-by-5 scene
  beside the minimap.
- Reduced macOS terminal load with a true 10 FPS output cap, row-differential
  redraws, compact colour sequences, and independently paced actor and fog
  animation.
- Added POSIX pixel-width detection, safe turn-based fallback for double-width
  terminal profiles, resize-aware clock pausing, and bounded CSI/SS3 key input.
- Kept the legacy `/sync` route for existing scripts while removing it from the
  command palette, help, and user-facing documentation.
- Preserved project-maintained Chinese meanings when OEWN English definitions
  are refreshed.
- Capped hint- and code-assisted game rounds so assisted recall is not recorded
  as unaided mastery.

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

## [0.3.1] - 2026-07-30

### Changed

- Added a filterable Codex-style slash-command palette with Up/Down selection,
  Enter submission, and Tab/Right completion.
- Kept command rows aligned during repeated keyboard navigation by preserving
  terminal newline processing.

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

[0.6.7]: https://github.com/Miracle-0v0/ielts-codex-cli/compare/v0.6.6...v0.6.7
[0.6.6]: https://github.com/Miracle-0v0/ielts-codex-cli/compare/v0.6.5...v0.6.6
[0.6.5]: https://github.com/Miracle-0v0/ielts-codex-cli/compare/v0.6.4...v0.6.5
[0.6.4]: https://github.com/Miracle-0v0/ielts-codex-cli/compare/v0.6.3...v0.6.4
[0.6.3]: https://github.com/Miracle-0v0/ielts-codex-cli/compare/v0.6.2...v0.6.3
[0.6.2]: https://github.com/Miracle-0v0/ielts-codex-cli/compare/v0.6.1...v0.6.2
[0.6.1]: https://github.com/Miracle-0v0/ielts-codex-cli/compare/v0.6.0...v0.6.1
[0.6.0]: https://github.com/Miracle-0v0/ielts-codex-cli/compare/v0.5.1...v0.6.0
[0.5.1]: https://github.com/Miracle-0v0/ielts-codex-cli/compare/v0.5.0...v0.5.1
[0.5.0]: https://github.com/Miracle-0v0/ielts-codex-cli/compare/v0.3.1...v0.5.0
[0.3.1]: https://github.com/Miracle-0v0/ielts-codex-cli/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/Miracle-0v0/ielts-codex-cli/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/Miracle-0v0/ielts-codex-cli/releases/tag/v0.2.0

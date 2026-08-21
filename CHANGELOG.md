# Changelog

All notable changes to Anko Director are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Planned

- Signed Windows builds and signed/notarized macOS builds.
- Save import/export with compatibility validation.
- Additional rule systems and AI provider adapters.

## [0.7.0] - 2026-08-22

### Added

- Infinite asset canvas for characters, scenes, items, rules, relationships, outcomes, and status cards.
- Hash-verified world snapshots, chapter history, revisions, references, and branching timelines.
- Character stats, templates, memories, and COC 7e derived attributes and combat adjudication.
- Explicit and sequential dice groups that can bypass AI parsing.
- Independent world-delta and character-memory analysis with targeted retries.
- OpenAI, Anthropic, Poe, and Discord adapters with OS credential-vault storage.
- Cross-platform CI and unsigned preview packaging for Windows, Linux, and macOS.

### Security

- Local loopback binding by default.
- Credentials excluded from campaign data and Git; keyring storage is preferred.
- SHA-256 counter random stream, roll evidence, event hash chain, and world-commit verification.

[Unreleased]: https://github.com/chaoyian/anko-director/compare/v0.7.0...HEAD
[0.7.0]: https://github.com/chaoyian/anko-director/releases/tag/v0.7.0

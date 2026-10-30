# malf — the public build-toolchain repo (malf-toolchain)

The one public home of *how CodeRoast builds*: the `malf` build driver (Python
over Conan editables), the canonical Conan profiles, the shared dev config, the
cross-repo CI composite actions, and the pinned-compiler release workflows.
Root CLAUDE.md owns malf *usage*; this file is for working ON the tooling.

## Arrival

- `malf` (the script) + `malf_graph.py`, `bench_compare.py`, `global.conf` —
  the command reference is `MALF.md`.
- `profiles/` — the canonical Conan profiles, one per toolchain leg.
- `config/` — the shared `.clang-format` / `.clang-tidy` / `.clangd` (dotfiles;
  `ls -a`), symlinked into every repo.
- `.github/actions/` + `.github/workflows/` — the composite actions consumed
  cross-repo via `uses:` and the toolchain-build workflows.
- `runner/` — self-hosted runner install/start scripts.
- Tests: `tests/test_malf.sh`.

## Constraints & traps

- PUBLIC and consumed **pinned**: sibling repos take actions and profiles
  straight from this repo (`uses: CodeRoasted/malf-toolchain/...@<40-hex-sha>`),
  and every such ref — theirs AND this repo's own self-references — is a commit
  SHA, never `@main`. So a change here does NOT reach a consumer by itself, and
  it is not meant to: **the reach is re-pinned ONCE PER CUT, never after a malf
  commit** (the Founder, 2026-10-14). Commit to `main` and stop; the cut's one
  re-pin (the superproject's `OPS-1.S15` layering rule: the self-references one
  commit per nesting layer, then the consumers) carries every commit since the
  last one, and step 0 reds at the cut until it has (`pin_coherence` INV-17 (g),
  `OPS-1.S3`). Re-pin between cuts only on a stated need, a consumer change that
  needs malf code the pin lacks, and name that need in the re-pin's message.
  **Every re-pin cuts a toolchain release** (`DN-108.D38`): `./pharos repin
  --toolchain X.Y.Z` and `./pharos bump <V> --toolchain X.Y.Z` tag the commit the
  reach then pins as the ANNOTATED `toolchain-vX.Y.Z`, strictly above the newest
  `toolchain-v*` on origin. Never cut, move or delete a `toolchain-v*` tag by
  hand: consumers pin the SHA, and the tag is the version a human reads.
  Treat every edit as a cross-repo change all the same: it ships at the next cut.
- `profiles/` and `config/` are single-source by design — never fork a per-repo
  copy; repos symlink or fetch them.
- The toolchain contracts (which compiler legs exist, why, their fix floors)
  are owned by `README.md` — point at it, don't restate in scripts/workflows.

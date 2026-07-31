# malf — the public build-toolchain repo (malf-toolchain)

The one public home of *how CodeRoast builds*: the `malf` orchestrator (Python
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

- PUBLIC and consumed live: sibling repos take actions and profiles straight
  from this repo (`uses: CodeRoasted/malf-toolchain/...`) — a change here lands
  in consumers' CI with nothing between you and them. Treat every edit as a
  cross-repo change and check the consumers.
- `profiles/` and `config/` are single-source by design — never fork a per-repo
  copy; repos symlink or fetch them.
- The toolchain contracts (which compiler legs exist, why, their fix floors)
  are owned by `README.md` — point at it, don't restate in scripts/workflows.

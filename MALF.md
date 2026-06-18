# malf

Unified build system for coderoast C++ projects. Replaces per-repo `dev.sh` scripts with a single portable tool.

Cross-repo package pins and release sequencing live in [../technical_docs/README.md](../technical_docs/README.md) and [../technical_docs/ROADMAP.md](../technical_docs/ROADMAP.md). `malf` is a local build helper, not the source of product roadmap or compatibility policy.

## Installation

Add `malf/` to your PATH, or symlink the script:

```sh
ln -sf /path/to/coderoast/malf/malf ~/.local/bin/malf
```

## Usage

Run `malf` from any directory that contains a `conanfile.py` (or a parent of subdirs that contain one).

```
malf build   [subdir] [--debug|--release]
malf test    [subdir] [--debug|--release]
malf lint    [--all-files] [-c|--console]
malf format  [--check]
malf commands
malf compile-commands
malf bench   [subdir] [--quick] [--filter REGEX] [--compare] [--repetitions N] [--threshold F] [--debug|--release]
malf clean   [build|conan|all]
malf run     <exe-name> [args...]
```

### Commands

| Command | Description |
|---|---|
| `build` | bootstrap local workspace package deps, `conan install`, configure, then compile into `build/[subdir]` |
| `test` | same build flow, then `ctest` |
| `lint` | clang-tidy on git-changed files (or `--all-files`) |
| `format` | clang-format all C++ files in-place |
| `commands` / `compile-commands` | merge all `compile_commands.json` under `build/` |
| — | build trees are ALWAYS profile-named: `build-clang21-libcxx-release/` (the dev default), `build-gcc15-release/`, `build-clang21-asan/`, … — a bare `build/` is never created, so a tree always self-documents its toolchain (`.clangd` reads the default leg's root DB) |
| `bench` | build + run benchmark executables, output JSON to `bench_results/` (plain runs update `baseline.json`) |
| `bench --compare` | variance-aware regression gate vs `baseline.json` (`bench_compare.py`): runs 5 repetitions by default, compares MEDIANS, and widens the `--threshold` floor (default ±10%) to 3×cv per bench — so honestly-noisy benches (the ±40–90pt pipeline swings) no longer false-positive while tight benches keep the floor |
| `clean` | remove `build/` and/or local Conan cache packages |
| `run` | execute a binary from the build tree |

### Cache discovery

| Cache | Resolution |
|---|---|
| **Local** | `MALF_WORKSPACE_ROOT/.conan2` by default (override with `CONAN_HOME`) |

### Workspace bootstrap

`build`, `test`, and `bench` automatically `conan create` sibling workspace packages whose exact `name/version` refs are required by the target recipe. Override the scan root with `MALF_WORKSPACE_ROOT` or disable it with `MALF_AUTO_WORKSPACE_DEPS=0`.

Any `scripts/conan_hooks/hook_*.py` files found under `MALF_WORKSPACE_ROOT` are copied into `CONAN_HOME/extensions/hooks` and activated automatically — the mechanism for applying any repo-local Conan source/build patch without manual Conan cache setup. (No such hooks ship today.)

Example (`~/.bashrc`):
```sh
export MALF_WORKSPACE_ROOT="$HOME/workspace/coderoast"
```

### Compile commands

`build` and `test` now configure with `CMAKE_EXPORT_COMPILE_COMMANDS=ON`, so each target build directory gets its own `compile_commands.json` automatically.

Use `commands` or `compile-commands` only when you want a merged database at `build/compile_commands.json`, for example:
- you built multiple packages from the same repo root
- you want `test_package` entries merged in for IDE navigation
- your editor is pinned to a single root `build/compile_commands.json`

### Conan profile

Profile `linux-gcc15-release` is resolved from `malf/profiles/` (copied into `$CONAN_HOME/profiles/` on every run so an edit always propagates). Override the name with `MALF_PROFILE_NAME`.

## Self-hosted CI runner (unmetered minutes)

`runner/` provisions an org-level self-hosted runner (label `malf-local`) so the
**private** repos' CI + `release-publish` stop consuming GitHub-hosted minutes. Toggle
hosted⇄local with one org variable (`CI_RUNS_ON=malf-local`). A Windows twin
(`install-runner.ps1` + `start-runner.ps1`, label `malf-windows`, toggle `WIN_RUNS_ON`)
serves the private eidos Windows probe. Public repos stay on the GitHub-hosted runners and
must never target either (fork-PR RCE). Full guide: `runner/README.md`.

## Bundled fallbacks

| File | Purpose |
|---|---|
| `global.conf` | Conan global config (seeded into local cache if missing) |
| `.clang-tidy` | Default clang-tidy config (overridden by project's own `.clang-tidy`) |
| `.clang-format` | Default clang-format style (overridden by project's own `.clang-format`) |

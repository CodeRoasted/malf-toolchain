# malf

Unified build system for coderoast C++ projects. Replaces per-repo `dev.sh` scripts with a single portable tool.

Cross-repo package pins and release sequencing live in [../technical_docs/README.md](../technical_docs/README.md) and [../technical_docs/ROADMAP.md](../technical_docs/ROADMAP.md). `malf` is a local build helper, not the source of product roadmap or compatibility policy.

## Installation

Add `malf/` to your PATH, or symlink the script:

```sh
ln -sf /path/to/coderoast/malf/malf ~/.local/bin/malf
```

## Usage

Run `malf` from any directory that contains a `conanfile.py`, or from any parent of dirs that contain one (a repo root, the workspace root).

```
malf build   [target] [--debug|--release]
malf test    [target] [--debug|--release] [--verbose] [--filter PATTERN]
malf lint    [--all-files] [-c|--console]
malf format  [--check]
malf commands
malf compile-commands
malf bench   [target] [--quick] [--filter REGEX] [--compare] [--repetitions N] [--threshold F] [--debug|--release]
malf clean   [build|conan|editables|stale|all]
malf run     <exe-name> [args...]
```

### Verbs — one compile surface

`build` compiles **everything** for the target: the package, its tests, and its
benches. `test` and `bench` run (`ctest` / the benchmark executables) on top of
the **same incremental build** — the verbs differ only in what they run, never
in what they compile, so a test- or bench-only TU can't rot unseen and first
fail in the release pipeline (the 1.7.2 drift-at-tag class).

### Targets — packages under the invocation point

* `[target]` omitted → **every package under the current dir, dependency-ordered**:
  a multi-package repo root sweeps the whole repo, the workspace root sweeps the
  whole workspace, a mono-package root degenerates to the single package. Each
  member runs as its own `malf <verb> <pkg>` process — the exact `&&`-chain
  semantics, first failure stops the sweep.
* `[target]` = a package dir → exactly that package (also the escape hatch for a
  repo whose *root* is itself a package with sibling sub-packages, e.g.
  `malf build .` at `insight-eidos`).
* Content-only packages (a `conanfile.py` but no `CMakeLists.txt` — umbrella
  metapackages, scenario corpora) are skipped with a note.

### Build trees — package-anchored

Every build tree lives at `<pkg>/build-<profile-key>` (`build-clang21-libcxx-release/`
(the dev default), `build-gcc15-release/`, `build-clang21-asan/`, …) — anchored at
the **package**, never the invocation CWD, and always profile-named. The target
build, the editable-dep build, and a subfolder invocation all share **one tree per
(package, profile)**: a bare `build/` is never created, an ASan tree can never
clobber a release one, and the old "cd into a package subdir → second,
inconsistent build tree that links a stale dependency" footgun is structurally
gone. The merged clangd DB lands at the repo root's default-leg tree
(`<repo>/build-clang21-libcxx-release/compile_commands.json` — what `.clangd` reads).

### Commands

| Command | Description |
|---|---|
| `build` | bootstrap local workspace package deps, `conan install`, configure, then compile package + tests + benches into `<pkg>/build-<key>/` |
| `test` | same build flow, then `ctest` |
| `bench` | same build flow, then run benchmark executables, output JSON to `bench_results/` (plain runs update `baseline.json`) |
| `bench --compare` | variance-aware regression gate vs `baseline.json` (`bench_compare.py`): runs 5 repetitions by default, compares MEDIANS, and widens the `--threshold` floor (default ±10%) to 3×cv per bench — so honestly-noisy benches (the ±40–90pt pipeline swings) no longer false-positive while tight benches keep the floor |
| `lint` | clang-tidy on git-changed files (or `--all-files`) |
| `format` | clang-format all C++ files in-place |
| `commands` / `compile-commands` | configure every member package (+ its `test_package`) and merge all `compile_commands.json` into the repo root's default-leg DB |
| `clean` | remove build trees (CWD + every member package) and/or local Conan cache packages |
| `run` | execute a binary from any member package's build tree |

### Cache discovery

| Cache | Resolution |
|---|---|
| **Local** | `MALF_WORKSPACE_ROOT/.conan2` by default (override with `CONAN_HOME`) |

### Workspace bootstrap

`build`, `test`, and `bench` automatically register sibling workspace packages whose exact `name/version` refs are required by the target recipe as Conan editables, and build them in dependency order into their own package-anchored trees. Override the scan root with `MALF_WORKSPACE_ROOT` or disable it with `MALF_AUTO_WORKSPACE_DEPS=0`. (The recipe-graph queries live in `malf_graph.py` — one AST parser for deps / whole-workspace / member enumeration.)

Any `scripts/conan_hooks/hook_*.py` files found under `MALF_WORKSPACE_ROOT` are copied into `CONAN_HOME/extensions/hooks` and activated automatically — the mechanism for applying any repo-local Conan source/build patch without manual Conan cache setup. (No such hooks ship today.)

Example (`~/.bashrc`):
```sh
export MALF_WORKSPACE_ROOT="$HOME/workspace/coderoast"
```

### Compile commands

`build`, `test`, and `bench` configure with `CMAKE_EXPORT_COMPILE_COMMANDS=ON`, so each package build tree gets its own `compile_commands.json` automatically — and every build folds it into the repo root's merged default-leg DB (`<repo>/build-clang21-libcxx-release/compile_commands.json`, what `.clangd` reads), wherever the build was invoked from.

Use `commands` or `compile-commands` only when you want that merged DB rebuilt from scratch, for example:
- you want `test_package` entries merged in for IDE navigation
- the merged DB carries stale entries from deleted/renamed packages

### Conan profile

The dev-default profile `linux-clang21-libcxx-release` (and any `--profile <name>` selection) is resolved from `malf/profiles/` (copied into `$CONAN_HOME/profiles/` on every run so an edit always propagates). Override the default with `MALF_DEFAULT_PROFILE` or the active name with `MALF_PROFILE_NAME`; `malf profiles` lists the registry.

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

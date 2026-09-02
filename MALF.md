# malf

Unified build system for coderoast C++ projects. Replaces per-repo `dev.sh` scripts with a single portable tool.

`malf` is a local build helper — not the source of cross-repo package pins, release sequencing, or compatibility policy. Those live with the consuming projects.

## Installation

Add `malf/` to your PATH, or symlink the script:

```sh
ln -sf /path/to/coderoast/malf/malf ~/.local/bin/malf
```

## Usage

Run `malf` from any directory that contains a `conanfile.py`, or from any parent of dirs that contain one (a repo root, the workspace root).

```
malf build   [target] [--asan|--profile <name>]
malf test    [target] [--asan|--profile <name>] [--verbose] [--filter PATTERN]
malf lint    [--all-files] [-c|--console]
malf format  [--check]
malf commands
malf compile-commands
malf bench   [target] [--quick] [--filter REGEX] [--compare] [--repetitions N] [--threshold F] [--asan|--profile <name>]
malf clean   <build|conan|editables|stale|all>   # no default; `all` is the nuclear form
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
(the dev default), `build-gcc16-release/`, `build-clang21-asan/`, …) — anchored at
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
| `lint` | clang-tidy on git-changed files (or `--all-files`). Requires a compile database — it refuses to run without one, because a flag-less clang-tidy reports phantom errors it cannot be right about. Under `--all-files` an empty file set is a scoping failure, not a pass. A TU clang-tidy **dies** on is reported as **NOT LINTED** by name, apart from the findings, and fails the run: a crash dump carries real paths and real line numbers, so merged into the findings it reads as a source defect while the file's zero coverage leaves no trace. It also DE-SYSTEMS first-party include roots before handing the database to clang-tidy: CMake marks an IMPORTED target's include directories SYSTEM, so our own editable packages arrive as `-isystem` and the checker reports nothing about them; the run rewrites those to `-I` in a derived database and prints what it rewrote and what it left. Third-party stays `-isystem` |
| `format` | clang-format all C++ files in-place (`--check` = `--dry-run --Werror`, non-zero on any violation). Resolves the style explicitly and refuses to run if it cannot, rather than let clang-format fall back to LLVM style unannounced |
| `commands` / `compile-commands` | configure every member package (+ its `test_package`) and merge all `compile_commands.json` into the repo root's default-leg DB |
| `clean` | remove build trees (CWD + every member package), cached Conan packages, or the editable registry — **one explicit target, no default**. A bare `malf clean` refuses and removes nothing; `malf clean all` is the nuclear form (all three at once). It defaulted to `all` until 2026-09-02, so one missing word wiped the workspace |
| `run` | execute a binary from any member package's build tree |

### Cache discovery

| Cache | Resolution |
|---|---|
| **Local** | `MALF_WORKSPACE_ROOT/.conan2` by default (override with `CONAN_HOME`) |

### Workspace bootstrap

`build`, `test`, and `bench` automatically register the target's sibling workspace packages as Conan editables and build them in dependency order into their own package-anchored trees. The set is the **build closure**, not the link closure: the target's transitive workspace `requires`, plus the workspace `test_requires` of *every member in the closure* — each dependency is configured as the top-level project of its own CMake preset, so its `PROJECT_IS_TOP_LEVEL` test and bench subtrees are ON and its own `test_requires` must resolve as well. Override the scan root with `MALF_WORKSPACE_ROOT` or disable it with `MALF_AUTO_WORKSPACE_DEPS=0`. (The recipe-graph queries live in `malf_graph.py` — one AST parser for deps / whole-workspace / member enumeration.)

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

**The build type is a coordinate of the profile, and there is no verb-level build-type flag.** A profile's `[settings] build_type` is the only declaration of it: it is read at load for the default profile and re-read by `--profile` / `--asan`, and every verb inherits it. `--debug` and `--release` were removed on 2026-09-02 — they were the mechanism by which a build tree came to carry a configuration the profile it is *named* for does not declare, and the tree name is the only thing anyone reads. Measured that day: **all 23** `build-clang21-libcxx-release` trees that a `malf build` / `malf test` had ever configured carried `CMAKE_BUILD_TYPE=Debug`, under a profile declaring `Release`, so the shipped translation units compiled with **no `-DNDEBUG`** and at `SPDLOG_ACTIVE_LEVEL=TRACE` while the directory name said `release`. It bought no compile time either: `-O3` comes from the profile's own `tools.build:cxxflags` whatever the build type. Every `cmake --preset` configure now asserts the tree's `CMAKE_BUILD_TYPE` against the profile file on disk and aborts **before** the compile on a mismatch, so the drift cannot come back silently. Want a Debug leg? Name a profile that declares one: `linux-clang21-libcxx-debug` (the desk debug leg — clang-21 + libc++ at Debug, no sanitizer) or `--asan` (`linux-clang21-asan`, the same build type with AddressSanitizer/UBSan attached).

The dev-default profile `linux-clang21-libcxx-release` (and any `--profile <name>` selection) is resolved from `malf/profiles/` (copied into `$CONAN_HOME/profiles/` on every run so an edit always propagates). Override the default with `MALF_DEFAULT_PROFILE` or the active name with `MALF_PROFILE_NAME`; `malf profiles` lists the registry.

## Self-hosted CI runner (unmetered minutes)

`runner/` provisions an org-level self-hosted runner (label `malf-local`) so the
**private** repos' CI + `release-publish` stop consuming GitHub-hosted minutes. Toggle
hosted⇄local with one org variable (`CI_RUNS_ON=malf-local`). A Windows twin
(`install-runner.ps1`, which registers a Windows **service**; label `malf-windows`, toggle
`WIN_RUNS_ON`) serves the private eidos Windows probe. Public repos stay on the GitHub-hosted runners and
must never target either (fork-PR RCE). Full guide: `runner/README.md`.

## Bundled fallbacks

| File | Purpose |
|---|---|
| `global.conf` | Conan global config (seeded into local cache if missing) |
| `config/.clang-tidy` | Default clang-tidy config (overridden by the project's own `.clang-tidy`) |
| `config/.clang-format` | Default clang-format style (overridden by the project's own `.clang-format`) |
| `config/.clangd` | Default clangd config |

Every C++ repo except `insight-twin` commits these three as **relative symlinks** into
`../malf/config/`. Those resolve in a workspace checkout and **dangle in a standalone
clone** — which is what a CI checkout is. `lint` and `format` both treat a dangling link
as absent and fall back to the path above, so both resolve the same config either way;
neither ever falls through to a tool default (`format` refuses to run rather than let
clang-format silently format to LLVM style).

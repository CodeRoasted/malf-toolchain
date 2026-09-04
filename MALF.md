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
malf slot    [status|acquire|release]
```

### Verbs — one compile surface

`build` compiles **everything** for the target: the package, its tests, and its
benches. `test` and `bench` run (`ctest` / the benchmark executables) on top of
the **same incremental build** — the verbs differ only in what they run, never
in what they compile, so a test- or bench-only TU can't rot unseen and first
fail in the release pipeline (the 1.7.2 drift-at-tag class).

### Targets — packages under the invocation point

**The rule is invocation-point INDEPENDENCE: `malf <verb> <dir>` is exactly
`cd <dir> && malf <verb>`.** How the path is spelled never changes what gets built.

* Any resolved dir → **every package under it, dependency-ordered**: a
  multi-package repo root sweeps the whole repo, the workspace root sweeps the
  whole workspace, a mono-package root or a leaf subfolder degenerates to the
  single package. Each member runs as its own `malf <verb> <pkg>` process — the
  exact `&&`-chain semantics, first failure stops the sweep.
* `--only` → exactly the recipe **at** the resolved dir and nothing below it.
  It refuses (exit 1) when that dir carries no `conanfile.py`, rather than
  quietly sweeping instead.

> **Until 2026-09-04 an explicit arg naming a package dir meant "that package
> only", and it was a defect.** It made the two spellings of one intent disagree
> in exactly the two repos that have a root recipe *and* sub-recipes —
> `insight-eidos` (5) and `coderoast-ipc` (4): `malf build insight-eidos` built
> ONE package while `cd insight-eidos && malf build` built five. **It failed
> silently** — the short build exits 0, so the operator reads a green over a
> surface that was never compiled. Measured during the `N135` suite rename: a
> `ctest -N` check reported the OLD suite names as still live, because
> `malf build insight-eidos` had left `insight-eidos/sift` at the previous day's
> binary. The rename looked incomplete; the build was. Rebuilding one package is
> still a real need — eidos's root recipe is a three-module compile target, not an
> umbrella — so it survives as `--only`, a stated intent rather than an accident
> of typing.
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

The key is `(package, profile)` and has **no dimension for role**, deliberately — see
*Compile commands* below for what that costs the editor index, why the tree is not split by
role, and the rule that replaces splitting it (**the richer role wins the tree**).

### Commands

| Command | Description |
|---|---|
| `build` | bootstrap local workspace package deps, `conan install`, configure, then compile package + tests + benches into `<pkg>/build-<key>/` |
| `test` | same build flow, then `ctest` |
| `bench` | same build flow, then run benchmark executables, output JSON to `bench_results/`. A run updates `baseline.json` **only when it is a baseline measurement**: unfiltered, not `--quick`, not `--compare`, and more than one repetition. Anything else prints, by name, which condition refused and how to make a baseline (`malf bench <pkg> --repetitions 5`), and leaves the file untouched — because a baseline replaced by a partial or low-precision run makes the NEXT comparison read noise as a regression or a win, with nothing in its output saying the baseline was the unusual half |
| `bench --compare` | variance-aware regression gate vs `baseline.json` (`bench_compare.py`): runs 5 repetitions by default, compares MEDIANS, and widens the `--threshold` floor (default ±10%) to 3×cv per bench — so honestly-noisy benches (the ±40–90pt pipeline swings) no longer false-positive while tight benches keep the floor |
| `lint` | clang-tidy on git-changed files (or `--all-files`). Requires a compile database — it refuses to run without one, because a flag-less clang-tidy reports phantom errors it cannot be right about. Under `--all-files` an empty file set is a scoping failure, not a pass. A TU clang-tidy **dies** on is reported as **NOT LINTED** by name, apart from the findings, and fails the run: a crash dump carries real paths and real line numbers, so merged into the findings it reads as a source defect while the file's zero coverage leaves no trace. It also DE-SYSTEMS first-party include roots before handing the database to clang-tidy: CMake marks an IMPORTED target's include directories SYSTEM, so our own editable packages arrive as `-isystem` and the checker reports nothing about them; the run rewrites those to `-I` in a derived database and prints what it rewrote and what it left. Third-party stays `-isystem`. **Every run ends with a one-line `SUMMARY`** naming the selection mode (`git diff --name-only HEAD` vs `--all-files`), the directory, the population the mode selected, how many of those actually reached clang-tidy, and the finding / not-linted counts — because a green that does not state its scope reads the same whether it checked 400 files or none. A run that selected **zero** files says `CHECKED 0 — NOTHING WAS INSPECTED` and states in words that it is not a clean verdict |
| `format` | clang-format all C++ files in-place (`--check` = `--dry-run --Werror`, non-zero on any violation). Resolves the style explicitly and refuses to run if it cannot, rather than let clang-format fall back to LLVM style unannounced. **Every run ends with a one-line `SUMMARY`** in the same vocabulary as `lint`'s, naming what the run did and where its population came from (`check`/`write` × `sweep`/`paths`), the directory, `selected` (every file the walk matched), `checked` (those handed to clang-format), the misformatted count in check mode, and `skipped` (the `MALF_SOURCE_MAX_FILE_KB` overruns) — so `selected = checked + skipped` is an identity on the line itself and a non-zero `skipped` is a visible coverage gap. A run that selected **zero** files says `CHECKED 0 — NOTHING WAS INSPECTED` and states in words that it is not a clean verdict, replacing `nothing to do`, which read as a pass. `misformatted` counts FILES, not diagnostics; the write mode reports no such count, because `clang-format -i` never says what it changed. The exit status is `xargs`'s 123 on any failing batch, so it is printed beside the count: rc non-zero with **zero** violations means clang-format failed for a reason that is not formatting, and those files were not checked at all |
| `commands` / `compile-commands` | configure every member package (+ its `test_package`) and merge all `compile_commands.json` into the repo root's default-leg DB. **Every run states its own scope** — members split `selected = configured + content-only + buildless`, a `test_package` census split `present = configured + failed + unreached`, the entry total split first-party vs external, and per member its entries and how many are test/bench TUs. Four states are **fatal** rather than a smaller number: a member left in **dependency role**; one that never configured; a **buildless** member that nonetheless declares a CMake project; and a `test_package` that failed to configure or was never reached |
| `clean` | remove build trees (CWD + every member package), cached Conan packages, or the editable registry — **one explicit target, no default**. A bare `malf clean` refuses and removes nothing; `malf clean all` is the nuclear form (all three at once). It defaulted to `all` until 2026-09-02, so one missing word wiped the workspace |
| `run` | execute a binary from any member package's build tree |
| `slot` | the **workspace build slot** — the coarse claim that one *lane* builds at a time across every repo, distinct from the per-build-directory lock `build`/`test` take for themselves. Its stamp carries two independent proofs, because a holder poses two different questions: an **anchor** (the holder's session pid plus that pid's start time) answers *is the holder still alive?*, and a 32-hex **token** answers *am I the holder?*. The anchor is the lane's SESSION and never an individual run — a per-run pid is gone between runs by design, so its death proves nothing, which is exactly why the hand-rolled `holder`-file protocol it replaces could not tell a corpse from a lane that was thinking. `acquire` claims a free slot, refuses a live one by name, and reclaims a provably dead one automatically. `release` needs `--token`; `--force` covers only a slot with no readable stamp, and a **live** holder cannot be forced at all — kill its anchor pid and the slot frees itself. A directory carrying no stamp `malf` wrote is `UNKNOWN`: never reclaimed for you, always shown to you. `status` prints `FREE` / `HELD` / `STALE` / `UNKNOWN` and exits `0` / `1` / `2` / `3`. Path: `MALF_BUILD_SLOT_DIR` (default `/tmp/coderoast-build-slot`) |

### Cache discovery

| Cache | Resolution |
|---|---|
| **Local** | `MALF_WORKSPACE_ROOT/.conan2` by default (override with `CONAN_HOME`) |

### Workspace bootstrap

`build`, `test`, and `bench` automatically register the target's sibling workspace packages as Conan editables and build them in dependency order into their own package-anchored trees. The set is the **build closure**, not the link closure: the target's transitive workspace `requires`, plus the workspace `test_requires` of *every member in the closure* — each dependency is configured as the top-level project of its own CMake preset, so its `PROJECT_IS_TOP_LEVEL` test and bench subtrees are ON and its own `test_requires` must resolve as well. Override the scan root with `MALF_WORKSPACE_ROOT` or disable it with `MALF_AUTO_WORKSPACE_DEPS=0`. (The recipe-graph queries live in `malf_graph.py` — one AST parser for deps / whole-workspace / member enumeration.)

Example (`~/.bashrc`):
```sh
export MALF_WORKSPACE_ROOT="$HOME/workspace/coderoast"
```

### Compile commands

`build`, `test`, and `bench` configure with `CMAKE_EXPORT_COMPILE_COMMANDS=ON`, so each package build tree gets its own `compile_commands.json` automatically — and every build folds it into the repo root's merged default-leg DB (`<repo>/build-clang21-libcxx-release/compile_commands.json`, what `.clangd` reads), wherever the build was invoked from.

Use `commands` or `compile-commands` only when you want that merged DB rebuilt from scratch, for example:
- you want `test_package` entries merged in for IDE navigation
- the merged DB carries stale entries from deleted/renamed packages

**One package, two roles, one tree.** In a single `commands` pass a package can be configured twice: once as the indexed **target** (tests and benches ON) and once as a sibling member's **dependency** (both OFF). Both writes land in the same `<pkg>/build-<key>` tree, because the build-tree key is `(package, profile)` and has **no dimension for role** — and the second write wins, so the merge would read a database with the target's test and bench TUs already removed. Measured 2026-09-03 at `clang21-libcxx-release`: `coderoast-ipc` produced **15 entries where the complete database is 19** (4 first-party TUs lost, 0 external) and `insight-canon` **60 where the complete database is 123** (63 first-party TUs lost, 0 external) — every one of the 67 a test or bench TU, both runs exiting 0.

The key does **not** gain a role dimension: the recipes bind a consumer to its dependency's build directory *by name*, so a second role-keyed tree would be the one consumers link while the other is the one `malf test` greens, and clangd reads one database anyway, so the role choice would only move. Instead the **richer role wins the tree** — the dependency configuration is a strict subset of the target one (verified: the two `CMakeCache.txt` differ only in the two feature variables and in `CMAKE_CTEST_COMMAND-ADVANCED`, which `enable_testing()` derives). So `commands` re-asserts the target role on every member a dependency bootstrap demoted, *before* the merge, and then reads each tree's own `CMakeCache.txt` back: a member still in dependency role, or one with no cache at all, exits **1** and names the variable and the file. The reasoning, the measurements and the rejected alternatives are in `malf` above `cmd_compile_commands`.

**A buildless member still has its `test_package` indexed.** A header-library recipe (`coderoast-server/server-logging`) declares no CMake generators, so its `conan install` writes no `CMakePresets.json` and its own preset configures nothing — but its `test_package` needs no preset at all: `commands` installs a synthetic conan consumer for it and runs `cmake -S <member>/test_package` against the toolchain that install wrote. Until 2026-09-03 the member loop `continue`d on the missing preset and skipped that configure too, so `coderoast-server` reported `test_package 4 present = 3 configured + 0 failed + 1 unreached` and **392 entries of which zero were `server-logging`'s** — the one translation unit proving that header-only logging seat's contract had never been in any editor index. The predicate is the **preset**, read off the build dir, never the absence of a `CMakeLists.txt`: a member that *does* declare a CMake project and produced no preset had a failed install and is fatal, which reading the source file could not tell apart. Both summary residuals — `unaccounted` members and `unreached` `test_package`s — are set differences against a census taken before the loop's first gate, so a future early exit is caught by arithmetic it cannot opt out of and the member is named.

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

# malf-toolchain

The pinned, public, reproducible build toolchain every CodeRoast repository compiles on — so a
CodeRoast build is a pure function of its source: the same on our CI, on your fork, and on a
private repo none of us can see.

## What this is — and why it's its own repo

CodeRoast distils a raw log stream into a **MetaLog**: a small, deterministic fingerprint of what
your system actually did — *bit-identical across runs*, citable, and re-derivable to the exact
source line. Every product — Sift's structural CI diff, anomaly detection, AI triage — is just a
lens on that one artifact. "Bit-identical across runs" isn't a slogan; it's the property that lets
a structural fact sit behind a hard CI gate instead of a dashboard.

A bit-identical *artifact* needs a controlled *build*. Floating-point contraction, ABI, and
standard-library internals all leak the compiler into the output — so *which compilers, built how*
is part of the determinism contract, not an environment detail to leave to chance. CodeRoast's
answer is stronger than "pin one compiler": the deterministic core is engineered (integer /
fixed-point math, no `libm` in any path feeding the artifact) to be **bit-identical across a pinned
set of toolchains** — gcc-15.3 / libstdc++, clang-21 / libc++, and MSVC 14.52 — *the same bytes on
Linux and Windows, across two standard libraries.* This repo provisions all three, identically, for
every CodeRoast repo's CI: no per-repo drift, no "works on my runner," no private toolchain a fork
can't reach. That cross-toolchain × cross-OS diagonal is the standing oracle that keeps the claim
honest — every release re-proves it.

It's also the short answer to *how we build*: the toolchain is open even where the product is
closed. You don't have to take our word that the builds are reproducible — the compiler, its full
source, and the exact recipe that produced it are all right here.

## The three compiler legs

CodeRoast builds C++23 (named modules + `import std`) on three toolchains, each with a distinct
job. Determinism is proven *across* them — the MetaLog and the Sift diff are bit-identical on all
three on the shared **plain-text** corpus — so this is the diagonal, not a fallback ladder. (The
JSON-ingestion surfaces — numeric/ordinal drift, OTEL fields — are now bit-identical by execution on
all three too, end to end: the canon→metalog instrument **and** the eidos detection leg, with no cell
left by construction.)

| Leg | Toolchain | Role |
|---|---|---|
| **Ship / reference** | gcc-15.3 / libstdc++ | the Linux release build; the determinism golden's reference leg |
| **Dev / cross-stdlib** | clang-21 / libc++ | the day-to-day dev compiler; the *second standard library* that makes the determinism diagonal real (libc++ ≠ libstdc++) |
| **Windows / cross-OS** | MSVC 14.52 / MSVC STL | the native Windows build; proves the artifact is bit-identical *across operating systems* and a third STL |

### gcc-15.3 — Linux ship + reference (PR124309 fix)

gcc < 15.3 mis-records linemaps for a partitioned module interface that imports a cross-package
module (upstream PR124309) → "Bad import dependency" — so the workspace pins a from-source
**gcc-15.3** at `/opt/gcc-15.3`. This repo builds that compiler once and publishes it as a public
release; it is also the leg the determinism goldens are frozen from.

```yaml
- name: Provision gcc-15.3
  uses: CodeRoasted/malf-toolchain/.github/actions/setup-gcc153@main
# → extracts to /opt/gcc-15.3 ; point your conan profile's CC/CXX there.
```

The release is **public**, so the download is anonymous — no token, no `packages:` scope, no
dependency on any private repo (public→public and private→public are both fine; a public repo
never depends on a private one).

**Rebuild (on a gcc bump):** run the `Build gcc toolchain` workflow (`workflow_dispatch`, pinned
source sha, self-gated by a minimal PR124309 repro). It publishes the binary + the Corresponding
Source to a new `gcc-<version>` release. ≈ 2 h; consumers then pull in ≈ 1-2 min.

### clang-21 / libc++ — dev default + the cross-stdlib determinism leg

The dev compiler, and the leg that makes "deterministic" mean something: libc++'s containers,
`std::hash`, and float formatting differ from libstdc++, so a clang-21/libc++ build matching the
gcc-15.3/libstdc++ golden proves the artifact is free of standard-library leakage — not just
compiler-flag-stable. `setup-clang21-libcxx` provisions clang-21 + libc++ and the `import std` /
modules bridge the determinism gates build on.

### MSVC 14.52 — Windows + cross-OS bit-identity

MSVC `cl.exe` is the only mature C++23 named-modules + `import std` path on Windows (it ships
`std.ixx`; CMake drives it under Ninja). 14.52 is the **fix floor**: earlier toolsets miscompile
exported defaulted `operator==` on module-defined DTOs (a consumer-TU double-free) and have an
`import std`/`<stop_token>` C1116 — both fixed at 14.52. It is gatherable for free from the VS
Insiders channel (the version-agnostic Preview component); `setup-msvc1452` installs it, asserts the
14.52 floor, activates the dev env, and exports `MSVC1452_INSTALL` so Conan points `vcvars` at it.
The MSVC build is bit-identical to the Linux golden across canon, metalog, and the Sift diff
(eidos) on the shared **plain-text** determinism corpus — the cross-OS half of the claim, measured,
not assumed. The JSON-ingestion surfaces (W1 ordinal histograms, OTEL trace/severity fields) are now
**bit-identical by execution on MSVC too**, end to end: a JSON-ordinal corpus line forces the simdjson
slow path at the canon→metalog instrument, and the eidos parse-replay leg carries a JSON-ordinal drift
row through to the structural diff — every Windows probe reproduces its JSON-bearing golden
byte-identically (a corpus-input refreeze, not a format change). **No cell is held by construction**:
all three legs are MSVC-by-execution on the JSON surfaces, the cross-OS half measured across the whole
chain, not assumed.

## CI actions

Composite actions consumed cross-repo by `uses:` — true single source, no per-repo copy:

| Action | Purpose |
|---|---|
| `setup-gcc153` | Provision the from-source gcc-15.3 to `/opt/gcc-15.3` (above) — the Linux ship + determinism reference leg. |
| `setup-clang21-libcxx` | Provision clang-21 + libc++ and the `import std` / modules bridge — the dev compiler and the cross-stdlib determinism leg (the gates run gcc-15.3 ≡ clang-21/libc++). |
| `setup-msvc1452` | On a Windows runner: install MSVC 14.52 from the VS Insiders Preview component, assert the 14.52 fix floor, activate the dev env (`cl.exe`/`link.exe` + INCLUDE/LIB), and export `MSVC1452_INSTALL` (the `windows-msvc-release` profile points Conan's `vcvars` at it). The Windows + cross-OS leg. |
| `setup-build-env` | The shared Linux C++ build env as one step: apt base + pinned CMake 4.3.x + Conan (no autodetect) + the conan dep-binary cache + the canonical `linux-gcc15-release` profile (fetched from `profiles/` here — no per-repo vendored copy). Owns `CONAN_HOME`. Run after `setup-gcc153`; pass repo-specific apt via `extra-apt`. |
| `conan-module` | Test / create one Conan module (gated by `test`/`create` inputs from `dorny/paths-filter`), with a gdb crash-diagnose fallback for `-march` SIGILLs. Reconciled from the three drifted per-repo copies. |

A consumer build job is then: `checkout` → `setup-gcc153` → `setup-build-env` → `conan-module` (×N).

## malf — the build orchestrator

`malf` (+ `global.conf`, `bench_compare.py`) is CodeRoast's build orchestrator, a thin layer over
Conan editable workspaces — `malf build` / `malf test` / `malf bench`. It lives here so the whole
of *how we build* is one public, reproducible reference: same compilers, same profiles, same build
tool, on our CI and on your fork. Usage: **[MALF.md](MALF.md)**.

## Shared config & profiles

- `profiles/` — the canonical Conan profiles, one per leg (single source; CI gets them via the
  setup actions, local `malf` reads them from here):
  - `linux-gcc15-release` — gcc-15.3 / libstdc++ (ship + determinism reference)
  - `linux-clang21-libcxx-release` — clang-21 / libc++ (dev default; the cross-stdlib leg)
  - `linux-clang21-release` — clang-21 / libstdc++ (keyed, for isolating the compiler axis from the stdlib axis)
  - `windows-msvc-release` — MSVC 14.52 (`compiler.version=195` → conan maps to vs_version 18 + `vcvars_ver=14.5` → the 14.52 toolset; Ninja generator; `vcvars` pointed at `MSVC1452_INSTALL`)
- `config/` — the canonical developer/editor config (`.clang-format`, `.clang-tidy`, `.clangd`).
  Each repo symlinks these, so there is one source of truth and drift is impossible; `malf lint`
  falls back to `config/.clang-tidy`.

## License

Repo content (the action, workflow, scripts) is CodeRoast's. **The published gcc binaries are
GNU GCC, licensed under GPLv3** — each release ships the **Corresponding Source** (the pristine,
unmodified `gcc-<version>.tar.xz`, sha-verified against ftp.gnu.org) alongside the binary, and
the build recipe is `.github/workflows/build-gcc-toolchain.yml` (no patches applied), per
GPLv3 §6.

## Roadmap

Everything that defines *how we build* now lives here: the three pinned compiler legs (gcc-15.3,
clang-21/libc++, MSVC 14.52), the conan profiles, the shared dev config, the CI actions, and `malf`
itself. The cross-toolchain × cross-OS determinism diagonal is measured green — the MetaLog and the
Sift structural diff are bit-identical across gcc/libstdc++, clang/libc++, and MSVC, on Linux and
Windows alike, on the shared plain-text corpus — and the JSON-ingestion surfaces (numeric/ordinal
drift, OTEL fields) are now bit-identical **by execution** on all three too, end to end — the
canon→metalog instrument and the eidos structural-diff leg alike, with no cell left by construction.
The toolchain is fully self-contained and public — the open
half of a product that is otherwise closed, and the public proof behind "deterministic by construction."

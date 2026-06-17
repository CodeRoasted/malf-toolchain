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

A bit-identical *artifact* needs a bit-identical *build*. Floating-point contraction, ABI, and
standard-library internals all leak the compiler into the output — so *which compiler, built how*
is part of the determinism contract, not an environment detail to leave to chance. This repo is
where that part is made explicit and public: **one compiler, pinned, built reproducibly from
pristine upstream source, provisioned identically by every CodeRoast repo's CI.** No per-repo
drift, no "works on my runner," no private toolchain a fork can't reach.

It's also the short answer to *how we build*: the toolchain is open even where the product is
closed. You don't have to take our word that the builds are reproducible — the compiler, its full
source, and the exact recipe that produced it are all right here.

## gcc-15.3 (PR124309 fix)

CodeRoast builds C++23 with gcc-15.3. gcc < 15.3 mis-records linemaps for a partitioned
module interface that imports a cross-package module (upstream PR124309) → "Bad import
dependency" — so the workspace pins a from-source **gcc-15.3** at `/opt/gcc-15.3`. This repo
builds that compiler once and publishes it as a public release.

### Use it in CI

```yaml
- name: Provision gcc-15.3
  uses: CodeRoasted/malf-toolchain/.github/actions/setup-gcc153@main
# → extracts to /opt/gcc-15.3 ; point your conan profile's CC/CXX there.
```

The release is **public**, so the download is anonymous — no token, no `packages:` scope, no
dependency on any private repo (public→public and private→public are both fine; a public repo
never depends on a private one).

### Rebuild (on a gcc bump)

Run the `Build gcc toolchain` workflow (`workflow_dispatch`, pinned source sha, self-gated by
a minimal PR124309 repro). It publishes the binary + the Corresponding Source to a new
`gcc-<version>` release. ≈ 2 h; consumers then pull in ≈ 1-2 min.

## CI actions

Composite actions consumed cross-repo by `uses:` — true single source, no per-repo copy:

| Action | Purpose |
|---|---|
| `setup-gcc153` | Provision the from-source gcc-15.3 to `/opt/gcc-15.3` (above). |
| `setup-build-env` | The shared C++ build env as one step: apt base + pinned CMake 4.3.x + Conan (no autodetect) + the conan dep-binary cache + the canonical `linux-gcc15-release` profile (fetched from `profiles/` here — no per-repo vendored copy). Owns `CONAN_HOME`. Run after `setup-gcc153`; pass repo-specific apt via `extra-apt`. |
| `conan-module` | Test / create one Conan module (gated by `test`/`create` inputs from `dorny/paths-filter`), with a gdb crash-diagnose fallback for `-march` SIGILLs. Reconciled from the three drifted per-repo copies. |

A consumer build job is then: `checkout` → `setup-gcc153` → `setup-build-env` → `conan-module` (×N).

## malf — the build orchestrator

`malf` (+ `global.conf`, `bench_compare.py`) is CodeRoast's build orchestrator, a thin layer over
Conan editable workspaces — `malf build` / `malf test` / `malf bench`. It lives here so the whole
of *how we build* is one public, reproducible reference: same compiler, same profiles, same build
tool, on our CI and on your fork. Usage: **[MALF.md](MALF.md)**.

## Shared config & profiles

- `profiles/` — the canonical Conan profiles. `linux-gcc15-release` is the single source; CI gets
  it via `setup-build-env`, and local `malf` reads it from here (`profiles/`) too.
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

Everything that defines *how we build* now lives here: the pinned compiler, the conan profiles,
the shared dev config, the CI actions, and `malf` itself. The toolchain is fully self-contained
and public — the open half of a product that is otherwise closed.

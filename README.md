# malf-toolchain

CodeRoast's public build toolchain — the compiler (and, later, the `malf` build system and
shared lint/format config) that every CodeRoast repo's CI provisions, so public and private
repos build on the **identical** toolchain with no cross-repo credential.

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

## License

Repo content (the action, workflow, scripts) is CodeRoast's. **The published gcc binaries are
GNU GCC, licensed under GPLv3** — each release ships the **Corresponding Source** (the pristine,
unmodified `gcc-<version>.tar.xz`, sha-verified against ftp.gnu.org) alongside the binary, and
the build recipe is `.github/workflows/build-gcc-toolchain.yml` (no patches applied), per
GPLv3 §6.

## Roadmap

The `malf` build system and shared developer config (clangd / clang-tidy / clang-format) move
here as a follow-up, so "how we build" is one public, reproducible reference.

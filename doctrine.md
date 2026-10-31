# C++ engineering doctrine — the digest

Owned by malf; every first-party C++ repository's `CLAUDE.md` imports it with `@../malf/doctrine.md`
(malf is a sibling of every repository in both workspace layouts). Digest of `ADR-26` — ADR-26 owns the
decisions and their why; a rule change lands in both in the same pass.

**Language & design:** C++23 baseline, modern only (jthread, `std::expected`, ranges, atomic
wait/notify). RAII and single responsibility enforced. Every name explicit, identifiers ≥ 3 chars (the
lint's floor and exemptions); no magic numbers; member defaults as `static constexpr` in the owning
struct; prefer standard concepts over unconstrained templates.

**Concurrency:** hot path lock-free/atomics; cold path mutexes; no manual lock/unlock, no detached
threads, no busy wait; no nested locks without a documented lock order. Determinism over
micro-optimization.

**Comments — code and comment as contract (`ADR-26.D7`):** a comment is a residual claim with a declared
form; everything else is deleted. Six one-line forms — `pre:` `post:` `invariant:` `assert:` (a contract
form may carry one untagged continuation) and `note:` `refs:` (one line each; two consecutive `note:`
lines are a violation) — a framed `D-LSRC-n` law block, and the forms a machine reads (`} // namespace x`,
`/*name=*/`, `/*name*/`, an own-line `NOLINT…` under a `note:`/`refs:`, `clang-format off/on`, the
`wall-clock:` waiver, SPDX). `refs:` carries registry addresses only. Bare prose, `///`, spacers, rulers,
trailing comments and suppressions without a why are violations. Read the derived contract before
opening a package's code: `malf contract-gen <package>` (`ADR-26.O3`). The gate is the second phase of
`malf format --check`.

**Ownership & data flow:** ownership, allocation boundaries and transfer explicit; lifetimes statically
understandable; immutable publication preferred; shared mutable state forbidden unless justified; prefer
message passing.

**Determinism & replay — sacred.** Deterministic content and causal order are bit-identical across runs.
The race carve-out is narrow: only the raw pre-reconciliation transport interleave (global sequence
counter, cross-shard arrival order, physical landing order of per-shard seal frames); never assert on
those. Everything the causal merge reconciles must hold: per-window frame membership, data-before-seal
ordering, closed-window count for a fixed replay target — a varying count is a determinism bug, never a
"seal-order race". No wall-clock dependence in replay logic. No float→int in a path feeding
deterministic content (integer, fixed-point, or round-to-nearest). Ordering assumptions explicit.

**Performance — sacred.** Arenas, PMR, slab or bounded allocators in hot paths; no global coordination;
cache-local, predictable data layout; unbounded allocation growth forbidden; hidden allocations
documented. Exceptions forbidden in hot paths — recoverable failures use `std::expected`; fatal
invariants terminate explicitly. Hot-path work is measurement-gated.

**Observability.** A blocked investigation is a logging gap: add permanent debug/trace logging (elided at
compile time, gated by level), never temporary prints. Tests are verbose on failure: actual vs expected
(ids, counts, diffs), enough to diagnose without a debugger.

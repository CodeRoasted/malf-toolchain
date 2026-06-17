#!/usr/bin/env python3
"""Variance-aware google-benchmark regression gate (the `malf bench --compare` engine).

Replaces the original single-run point comparison, which false-positived on the
2-3-iteration pipeline benches (BM_Pipeline_Drop/Block/16/*): identical
back-to-back runs swing +/-40-90 points, so a fixed +/-10% threshold flagged
noise as regression (WIP 1.5.2 handoff item).

Two fixes, both statistical:

  1. CENTRAL VALUE: each side's per-benchmark time is the MEDIAN across
     repetitions, not a single run. Sources, in preference order:
       a. google-benchmark aggregate rows (run_type == "aggregate":
          median + cv) - produced by --benchmark_repetitions=N, which
          `malf bench --compare` now passes by default;
       b. raw repetition rows (run_type == "iteration", same run_name
          repeated) - median + cv computed here;
       c. a single run (legacy baseline.json) - the value itself, cv unknown.

  2. VARIANCE-AWARE THRESHOLD: per benchmark,
         thr_eff = max(threshold_floor, K_SIGMA * max(cv_base, cv_curr))
     where cv = stddev/mean (google-benchmark's own "cv" aggregate when
     present). A bench whose honest run-to-run noise is 30% is only flagged
     beyond ~3 sigma of that noise; a tight bench keeps the floor. When a
     side has no cv (legacy single-run baseline), the other side's cv is
     used; when neither has one, the floor applies (the old behavior).

Usage:  bench_compare.py BASELINE.json CURRENT.json THRESHOLD_FLOOR
Exit:   0 = within tolerance, 1 = regression or missing benchmark, 2 = usage.

The report prints the effective threshold per row whenever variance widened
it past the floor, so a "pass" on a noisy bench is visibly a wide pass.
"""

from __future__ import annotations

import json
import statistics
import sys

K_SIGMA = 3.0


def load_runs(path: str) -> dict[str, dict]:
    """Map base benchmark name -> {'central': float, 'cv': float|None, 'unit': str}."""
    with open(path) as handle:
        data = json.load(handle)

    iterations: dict[str, list[float]] = {}
    aggregates: dict[str, dict[str, float]] = {}
    units: dict[str, str] = {}

    for bench in data.get("benchmarks", []):
        if "real_time" not in bench:
            continue
        base = bench.get("run_name") or bench["name"]
        units.setdefault(base, bench.get("time_unit", "ns"))
        if bench.get("run_type") == "aggregate":
            aggregates.setdefault(base, {})[bench.get("aggregate_name", "")] = \
                float(bench["real_time"])
        else:
            iterations.setdefault(base, []).append(float(bench["real_time"]))

    runs: dict[str, dict] = {}
    for base in sorted(set(iterations) | set(aggregates)):
        agg = aggregates.get(base, {})
        samples = iterations.get(base, [])
        if "median" in agg:
            central = agg["median"]
            # google-benchmark's cv aggregate is stddev/mean as a fraction.
            cv = agg.get("cv")
            if cv is None and "stddev" in agg and agg.get("mean"):
                cv = agg["stddev"] / agg["mean"]
        elif len(samples) > 1:
            central = statistics.median(samples)
            mean = statistics.fmean(samples)
            cv = (statistics.stdev(samples) / mean) if mean > 0 else None
        elif samples:
            central, cv = samples[0], None
        else:
            continue  # aggregates without a median (shouldn't happen) - skip
        if central <= 0:
            continue
        runs[base] = {"central": central, "cv": cv, "unit": units[base]}
    return runs


def effective_threshold(floor: float, base_cv, curr_cv) -> float:
    cvs = [cv for cv in (base_cv, curr_cv) if cv is not None]
    return max(floor, K_SIGMA * max(cvs)) if cvs else floor


def main() -> int:
    if len(sys.argv) != 4:
        print("usage: bench_compare.py BASELINE.json CURRENT.json THRESHOLD_FLOOR",
              file=sys.stderr)
        return 2
    baseline_path, current_path, floor = sys.argv[1], sys.argv[2], float(sys.argv[3])
    base_map = load_runs(baseline_path)
    curr_map = load_runs(current_path)

    regressions, missing, improvements, stable, new = [], [], [], [], []
    for name, base in base_map.items():
        if name not in curr_map:
            missing.append(name)
            continue
        curr = curr_map[name]
        thr = effective_threshold(floor, base["cv"], curr["cv"])
        delta = (curr["central"] - base["central"]) / base["central"]
        row = (name, base["central"], curr["central"], delta, base["unit"], thr)
        if delta > thr:
            regressions.append(row)
        elif delta < -thr:
            improvements.append(row)
        else:
            stable.append(row)
    for name in curr_map:
        if name not in base_map:
            new.append(name)

    col_name = max((len(r[0]) for r in improvements + regressions + stable),
                   default=20)
    col_name = max(col_name, 20)

    def row_line(name, base_t, curr_t, delta, unit, thr):
        marker = "✓" if abs(delta) <= thr else ("▲" if delta > 0 else "▼")
        widened = f"  (thr ±{thr*100:.0f}%)" if thr > floor else ""
        return (f"  {marker}  {name:<{col_name}}  "
                f"{base_t:>12.3f} {unit:<3}  "
                f"{curr_t:>12.3f} {unit:<3}  "
                f"{delta*100:>+7.1f}%{widened}")

    if stable or improvements or regressions:
        print(f"{'':5}{'Benchmark':<{col_name}}  {'Baseline':>16}  "
              f"{'Current':>16}  {'Delta':>8}")
        print("-" * (5 + col_name + 2 + 16 + 2 + 16 + 2 + 8))
        for row in sorted(stable, key=lambda x: x[0]):
            print(row_line(*row))
        for row in sorted(improvements, key=lambda x: x[0]):
            print(row_line(*row))
        for row in sorted(regressions, key=lambda x: x[0]):
            print(row_line(*row))
        print()

    summary = [f"{len(stable)} stable"]
    if improvements:
        summary.append(f"{len(improvements)} improved")
    if new:
        summary.append(f"{len(new)} new")
    if missing:
        summary.append(f"{len(missing)} missing")
    if regressions:
        summary.append(f"{len(regressions)} REGRESSED")
    print("Summary: " + ", ".join(summary))

    if new:
        print("\nNew benchmarks (no baseline):")
        for name in new:
            print(f"  {name}")
    if missing:
        print("\nREGRESSION — benchmarks missing from current run:")
        for name in missing:
            print(f"  {name}")
    if regressions:
        print(f"\nREGRESSION — slower beyond the per-bench tolerance "
              f"(floor ±{floor*100:.1f}%, widened by {K_SIGMA:.0f}×cv where measured):")
        for row in regressions:
            print(f"  {row[0]}  Δ={row[3]*100:+.1f}%  (thr ±{row[5]*100:.1f}%)")

    print()
    if regressions or missing:
        print("FAILED: bench regression gate.")
        return 1
    print("OK: all benchmarks within tolerance.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

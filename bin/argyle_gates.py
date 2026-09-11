# Provenance: lifted verbatim from cipherholdingsllc/nakatomi
# branch t2-argyle-frontier, path crypto-crawler/crypto_crawler/argyle/gates.py
# (commit 02be13ca139e847089d3ce7c6503d9f5dae914ba).
# Pure stdlib. Do not edit here; upstream fixes land in nakatomi first.
"""
Promotion gates — the bar every learned knob must clear before it may touch
the mock book. Three tests, ALL required (logical AND):

  1. Deflated Sharpe (Bailey & López de Prado 2014): the observed Sharpe must
     be significant AFTER deflating for the number of configurations actually
     tried. Sweeping N configs and reporting the best is the quant original
     sin; the deflation term grows with N, so a small sample cannot launder a
     lucky config into a promotion.
  2. Block-permutation test: the strategy's edge statistic must beat p < 0.05
     against block-shuffled nulls (blocks preserve short-range autocorrelation
     that a naive i.i.d. shuffle would destroy — shuffling away autocorrelation
     makes nulls too easy to beat).
  3. Out-of-sample calibration: mean Brier of the candidate's p_up on held-out
     outcomes must beat CLIMATOLOGY — the constant base-rate predictor, whose
     Brier is p̄(1−p̄) ≤ 0.25. A fixed 0.25 bar is launderable at skewed base
     rates (constant p=0.9 on 90%-up outcomes scores 0.09 with zero skill —
     found by adversarial audit 2026-07-04); climatology is not.

Everything is pure Python + math (no scipy — this box is minimal, and a gate
must not fail-open because a dependency is missing). Normal CDF via math.erf;
inverse normal via Acklam's rational approximation (|ε| < 1.15e-9, far below
any decision boundary here).

Honest-refusal posture: with too little data the gate returns passed=False
with reason "insufficient data" — it never extrapolates a pass. On ~teens of
settled findings the EXPECTED outcome is failure; that is the gate working.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

EULER_GAMMA = 0.5772156649015329


def norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def norm_ppf(p: float) -> float:
    """Acklam's inverse-normal approximation. Pure, dependency-free."""
    if not 0.0 < p < 1.0:
        raise ValueError(f"p must be in (0,1), got {p}")
    a = (-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00)
    b = (-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01)
    c = (-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00)
    d = (7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00)
    p_low, p_high = 0.02425, 1 - 0.02425
    if p < p_low:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
               ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    if p > p_high:
        q = math.sqrt(-2 * math.log(1 - p))
        return -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
                ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    q = p - 0.5
    r = q * q
    return (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q / \
           (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1)


def _moments(xs: list[float]) -> tuple[float, float, float, float]:
    """(mean, std, skew, kurtosis) — kurtosis is NON-excess (normal = 3.0),
    matching the PSR formula's convention. Sample std (ddof=1)."""
    n = len(xs)
    mean = sum(xs) / n
    var = sum((x - mean) ** 2 for x in xs) / (n - 1)
    std = math.sqrt(var)
    if std == 0.0:
        return mean, 0.0, 0.0, 3.0
    skew = sum(((x - mean) / std) ** 3 for x in xs) / n
    kurt = sum(((x - mean) / std) ** 4 for x in xs) / n
    return mean, std, skew, kurt


def sharpe(returns: list[float]) -> float:
    """Per-period, non-annualized. Sample std."""
    mean, std, _, _ = _moments(returns)
    if std == 0.0:
        return 0.0
    return mean / std


@dataclass
class GateResult:
    passed: bool
    reason: str
    detail: dict = field(default_factory=dict)


def deflated_sharpe(
    returns: list[float],
    n_trials: int,
    var_sr_trials: float | None = None,
    confidence: float = 0.95,
) -> GateResult:
    """DSR = PSR evaluated at the expected-max-Sharpe benchmark under n_trials.

    SR0 = sqrt(V[SR_n]) * ((1-γ)·Φ⁻¹(1-1/N) + γ·Φ⁻¹(1-1/(N·e)))
    DSR = Φ( (SR - SR0)·sqrt(T-1) / sqrt(1 - γ₃·SR + (γ₄-1)/4·SR²) )

    `var_sr_trials` is the cross-trial variance of the Sharpe estimates from
    the actual config sweep — pass it when the sweep recorded per-config SRs.
    When unavailable, the estimator's own asymptotic variance is used as a
    stand-in (documented compromise: it under-deflates a heterogeneous sweep,
    so prefer real trial variance for any promotion decision)."""
    t = len(returns)
    if t < 10:
        return GateResult(False, f"insufficient data: {t} returns < 10")
    if n_trials < 1:
        return GateResult(False, f"n_trials must be >= 1, got {n_trials}")
    sr = sharpe(returns)
    _, _, skew, kurt = _moments(returns)
    denom = 1.0 - skew * sr + (kurt - 1.0) / 4.0 * sr * sr
    if denom <= 0.0:
        return GateResult(False, "PSR variance term non-positive (pathological moments)",
                          {"sr": sr, "skew": skew, "kurt": kurt})
    if var_sr_trials is None:
        var_sr_trials = denom / (t - 1)
    if n_trials == 1:
        sr0 = 0.0
    else:
        e = math.e
        sr0 = math.sqrt(max(var_sr_trials, 0.0)) * (
            (1 - EULER_GAMMA) * norm_ppf(1 - 1.0 / n_trials)
            + EULER_GAMMA * norm_ppf(1 - 1.0 / (n_trials * e))
        )
    dsr = norm_cdf((sr - sr0) * math.sqrt(t - 1) / math.sqrt(denom))
    passed = dsr >= confidence
    return GateResult(
        passed,
        f"DSR={dsr:.4f} {'≥' if passed else '<'} {confidence} (SR={sr:.4f}, SR0={sr0:.4f}, "
        f"N={n_trials}, T={t})",
        {"dsr": dsr, "sr": sr, "sr0": sr0, "n_trials": n_trials, "t": t},
    )


def block_permutation(
    directions: list[float],
    returns: list[float],
    n_perm: int = 2000,
    block: int | None = None,
    seed: int = 0,
    alpha: float = 0.05,
) -> GateResult:
    """H0: directions carry no information about returns. Statistic is the
    mean directional capture mean(d_i·r_i). Null draws are a CIRCULAR BLOCK
    BOOTSTRAP of the return series (blocks drawn with replacement, order
    within blocks preserved) — short-range autocorrelation survives, and the
    p-value is approximate rather than an exact permutation p. Seeded — same
    inputs, same p. Add-one estimator (Phipson & Smyth) so p is never 0."""
    n = len(returns)
    if n != len(directions):
        return GateResult(False, "directions/returns length mismatch")
    if n < 8:
        return GateResult(False, f"insufficient data: {n} pairs < 8")
    if block is None:
        block = max(1, round(n ** (1.0 / 3.0)))  # standard b ~ n^(1/3) rule
    obs = sum(d * r for d, r in zip(directions, returns)) / n
    rng = random.Random(seed)
    n_blocks = math.ceil(n / block)
    hits = 0
    for _ in range(n_perm):
        starts = [rng.randrange(n) for _ in range(n_blocks)]
        permuted: list[float] = []
        for s in starts:
            for j in range(block):
                if len(permuted) == n:
                    break
                permuted.append(returns[(s + j) % n])
        stat = sum(d * r for d, r in zip(directions, permuted)) / n
        if stat >= obs:
            hits += 1
    p = (hits + 1) / (n_perm + 1)
    passed = p < alpha
    return GateResult(
        passed,
        f"block-permutation p={p:.4f} {'<' if passed else '≥'} {alpha} "
        f"(obs={obs:.6f}, block={block}, n_perm={n_perm})",
        {"p": p, "obs": obs, "block": block, "n_perm": n_perm},
    )


def oos_calibration(p_up: list[float], outcomes: list[int]) -> GateResult:
    """Mean Brier of held-out p_up must beat the climatology Brier p̄(1−p̄) —
    the score of constantly predicting the realized base rate. This is the
    skill bar a constant predictor cannot pass by construction."""
    if len(p_up) != len(outcomes):
        return GateResult(False, "p_up/outcomes length mismatch")
    if len(p_up) < 10:
        return GateResult(False, f"insufficient data: {len(p_up)} pairs < 10")
    brier = sum((p - o) ** 2 for p, o in zip(p_up, outcomes)) / len(p_up)
    base_rate = sum(outcomes) / len(outcomes)
    climatology = base_rate * (1.0 - base_rate)
    passed = brier < climatology
    return GateResult(
        passed,
        f"OOS Brier={brier:.4f} {'<' if passed else '≥'} climatology "
        f"{climatology:.4f} (p̄={base_rate:.3f}, n={len(p_up)}, coin-flip bound 0.25)",
        {"brier": brier, "n": len(p_up), "climatology": climatology,
         "base_rate": base_rate, "coin_flip": 0.25},
    )


def promotion_gate(
    returns: list[float],
    n_trials: int,
    directions: list[float],
    p_up: list[float],
    outcomes: list[int],
    var_sr_trials: float | None = None,
    seed: int = 0,
) -> GateResult:
    """The full gate: deflated Sharpe AND block permutation AND OOS calibration.
    Returns one GateResult whose detail carries all three components — this
    dict IS the promotion receipt body."""
    dsr = deflated_sharpe(returns, n_trials, var_sr_trials)
    perm = block_permutation(directions, returns, seed=seed)
    cal = oos_calibration(p_up, outcomes)
    passed = dsr.passed and perm.passed and cal.passed
    reason = " | ".join(
        f"{name}: {'PASS' if g.passed else 'FAIL'} ({g.reason})"
        for name, g in (("deflated_sharpe", dsr), ("block_permutation", perm), ("oos_calibration", cal))
    )
    return GateResult(passed, reason, {
        "deflated_sharpe": {"passed": dsr.passed, **dsr.detail},
        "block_permutation": {"passed": perm.passed, **perm.detail},
        "oos_calibration": {"passed": cal.passed, **cal.detail},
    })

"""CRT solver tool for the modular_solver sub-agent."""

from __future__ import annotations

import math
from typing import Any


def _extended_gcd(a: int, b: int) -> tuple[int, int, int]:
    if b == 0:
        return a, 1, 0
    g, x, y = _extended_gcd(b, a % b)
    return g, y, x - (a // b) * y


def _mod_inverse(a: int, m: int) -> int | None:
    g, x, _ = _extended_gcd(a % m, m)
    if g != 1:
        return None
    return x % m


async def solve_crt(remainders: list, moduli: list) -> dict[str, Any]:
    """Solve a system of simultaneous congruences via the Chinese Remainder Theorem.

    Finds the smallest non-negative x satisfying x ≡ remainders[i] (mod moduli[i])
    for all i, or reports that no solution exists.
    """
    if len(remainders) != len(moduli):
        return {"exists": False, "error": "remainders and moduli must have equal length"}
    if len(remainders) == 0:
        return {"exists": False, "error": "system is empty"}

    moduli = [int(m) for m in moduli]
    remainders = [int(r) for r in remainders]

    if any(m <= 0 for m in moduli):
        return {"exists": False, "error": "all moduli must be positive integers"}

    r = [ri % mi for ri, mi in zip(remainders, moduli)]
    m = moduli[:]

    cur_r, cur_m = r[0], m[0]

    for i in range(1, len(r)):
        ri, mi = r[i], m[i]
        g = math.gcd(cur_m, mi)

        if (ri - cur_r) % g != 0:
            return {
                "exists": False,
                "error": (
                    f"Incompatible congruences at equation {i + 1}: "
                    f"x ≡ {cur_r} (mod {cur_m}) conflicts with x ≡ {ri} (mod {mi})"
                ),
                "num_equations": len(remainders),
                "input": {"remainders": remainders, "moduli": moduli},
            }

        lcm = cur_m * mi // g
        step = cur_m // g
        diff = (ri - cur_r) // g
        mod_small = mi // g
        inv = _mod_inverse(step, mod_small)
        t = (diff * inv) % mod_small
        cur_r = (cur_r + cur_m * t) % lcm
        cur_m = lcm

    pairwise_coprime = all(
        math.gcd(m[i], m[j]) == 1
        for i in range(len(m))
        for j in range(i + 1, len(m))
    )

    return {
        "exists": True,
        "solution": cur_r,
        "modulus": cur_m,
        "num_equations": len(remainders),
        "pairwise_coprime": pairwise_coprime,
        "input": {"remainders": remainders, "moduli": moduli},
    }

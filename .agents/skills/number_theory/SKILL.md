---
name: number_theory
description: "Use this skill when the user asks about prime factorization, number-theoretic properties of an integer (divisors, Euler's totient, Möbius function, perfect or squarefree numbers), or needs to solve a system of modular congruences / Chinese Remainder Theorem problem"
tools:
  - name: prime_factorize
    description: "Factorize n and compute number-theoretic properties including Euler totient φ(n), divisor count τ(n), divisor sum σ(n), Möbius function μ(n), squarefree flag, and perfect number flag"
    module: cuga.backend.skills.number_theory_tools.prime_factorizer
    function: prime_factorize
  - name: solve_crt
    description: "Solve a system of simultaneous congruences x ≡ r₁ (mod m₁), x ≡ r₂ (mod m₂), … via the Chinese Remainder Theorem; returns the smallest non-negative solution and the combined modulus"
    module: cuga.backend.skills.number_theory_tools.modular_solver
    function: solve_crt
---

# Number Theory Skill

Two math tools are available: **`prime_factorize`** and **`solve_crt`**.

⚠️ **USE SUBAGENTS** — even for a single calculation. Number theory requires exact arithmetic; a subagent with fresh context focuses entirely on the computation without the noise of the rest of the conversation.

## Factorization / number-theoretic properties

When the user asks for prime factors, φ(n), τ(n), σ(n), μ(n), squarefree, or perfect number status, spawn a subagent with this prompt:

```
Factorize <n> and compute its number-theoretic properties using the prime_factorize tool.

Report all of the following:
- Prime factorization with exponents
- Euler totient φ(n)
- Number of divisors τ(n)
- Sum of divisors σ(n)
- Möbius function μ(n)
- Whether n is squarefree
- Whether n is a perfect number
```

## CRT / simultaneous congruences

When the user needs to solve x ≡ r₁ (mod m₁), x ≡ r₂ (mod m₂), …, spawn a subagent with this prompt:

```
Solve the following system of simultaneous congruences using the solve_crt tool:

x ≡ <r1> (mod <m1>)
x ≡ <r2> (mod <m2>)
...

Call solve_crt with remainders=[<r1>, <r2>, ...] and moduli=[<m1>, <m2>, ...].
Report the smallest non-negative solution x and the combined modulus M,
so the complete solution class x ≡ solution (mod M) is clear.
```

## Both needed

Spawn two subagents. Await each result, then combine into a single final answer.

## Presenting results

Return the subagent's output verbatim, then add a one-sentence plain-English summary. Never compute or guess values yourself — the tools provide exact arithmetic.

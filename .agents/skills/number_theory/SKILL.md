---
name: number_theory
description: "Use this skill when the user asks about prime factorization, number-theoretic properties of an integer (divisors, Euler's totient, Möbius function, perfect or squarefree numbers), or needs to solve a system of modular congruences / Chinese Remainder Theorem problem"
agents:
  - agents/prime_factorizer
  - agents/modular_solver
---

# Number Theory Skill

Two specialized sub-agents are available:

| Agent | Specialization | Trigger |
|-------|---------------|---------|
| `prime_factorizer` | Prime factorization and derived properties | User asks for prime factors, φ(n), τ(n), σ(n), μ(n), perfect/squarefree status |
| `modular_solver` | Chinese Remainder Theorem | User asks to solve x ≡ r₁ (mod m₁), x ≡ r₂ (mod m₂), … or "what number leaves remainder R when divided by M with multiple conditions" |

## Routing rules

**Factorization / properties only:**
```
spawn_agent(name="prime_factorizer", task="Factorize <n> and return all number-theoretic properties")
```

**CRT / congruences only:**
```
spawn_agent(name="modular_solver", task="Solve the system: x ≡ <r1> (mod <m1>), x ≡ <r2> (mod <m2>), ...")
```

**Both needed** (e.g. "factorize n, then check if the CRT solution is a divisor of n"):
Spawn both agents. Await each result before combining into a final answer.

## Presenting results

Return the tool's structured output verbatim, then add a plain-English one-sentence summary. Do not guess or calculate values yourself — the agents provide exact arithmetic.

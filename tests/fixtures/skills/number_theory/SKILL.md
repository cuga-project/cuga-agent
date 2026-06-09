---
name: number_theory
description: "Use this skill when the user asks about prime factorization, number-theoretic properties of an integer, or Chinese Remainder Theorem problems"
agents:
  - ../../agents/prime_factorizer
  - ../../agents/modular_solver
---

# Number Theory Skill

Two specialized sub-agents are available:

| Agent | Specialization | Trigger |
|-------|---------------|---------|
| `prime_factorizer` | Prime factorization and derived properties | User asks for prime factors, φ(n), τ(n), σ(n), μ(n), perfect/squarefree status |
| `modular_solver` | Chinese Remainder Theorem | User asks to solve x ≡ r₁ (mod m₁), x ≡ r₂ (mod m₂), … |

## Routing rules

**Factorization / properties only:**
```
spawn_agent(name="prime_factorizer", task="Factorize <n> and return all number-theoretic properties")
```

**CRT / congruences only:**
```
spawn_agent(name="modular_solver", task="Solve the system: x ≡ <r1> (mod <m1>), x ≡ <r2> (mod <m2>), ...")
```

Return the tool's structured output verbatim, then add a plain-English one-sentence summary.

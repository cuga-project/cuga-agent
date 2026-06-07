import math
import random


def _miller_rabin(n: int, a: int) -> bool:
    if n % a == 0:
        return n == a
    d, r = n - 1, 0
    while d % 2 == 0:
        d //= 2
        r += 1
    x = pow(a, d, n)
    if x == 1 or x == n - 1:
        return True
    for _ in range(r - 1):
        x = x * x % n
        if x == n - 1:
            return True
    return False


_WITNESSES = (2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37)


def _is_prime(n: int) -> bool:
    if n < 2:
        return False
    for p in _WITNESSES:
        if n == p:
            return True
        if n > p and not _miller_rabin(n, p):
            return False
    return True


def _pollard_rho(n: int) -> int | None:
    if n % 2 == 0:
        return 2
    x = random.randint(2, n - 1)
    y = x
    c = random.randint(1, n - 1)
    d = 1
    while d == 1:
        x = (x * x + c) % n
        y = (y * y + c) % n
        y = (y * y + c) % n
        d = math.gcd(abs(x - y), n)
    return d if d != n else None


def _full_factor(n: int, result: dict[int, int]) -> None:
    if n <= 1:
        return
    if _is_prime(n):
        result[n] = result.get(n, 0) + 1
        return
    d = None
    for _ in range(64):
        d = _pollard_rho(n)
        if d and 1 < d < n:
            break
    if not d or d == n:
        result[n] = result.get(n, 0) + 1
        return
    _full_factor(d, result)
    _full_factor(n // d, result)


async def prime_factorize(n: int) -> dict:
    """Factorize n and compute number-theoretic properties.

    Returns factors, Euler totient, divisor count, divisor sum,
    Möbius function, squarefree flag, and perfect number flag.
    """
    if not isinstance(n, int) or n < 1:
        return {"error": "n must be a positive integer"}
    if n > 10 ** 18:
        return {"error": "n exceeds maximum supported value (10^18)"}

    factors: dict[int, int] = {}

    # Trial division up to 10^6
    remaining = n
    for p in range(2, 10 ** 6 + 1):
        if p * p > remaining:
            break
        if remaining % p == 0:
            exp = 0
            while remaining % p == 0:
                exp += 1
                remaining //= p
            factors[p] = exp

    # Remaining factor is either 1, prime, or semi-prime — use Pollard's rho
    if remaining > 1:
        _full_factor(remaining, factors)

    # Derived properties
    euler_totient = 1
    num_divisors = 1
    sum_divisors = 1
    is_squarefree = True
    num_distinct_primes = len(factors)

    for p, e in factors.items():
        euler_totient *= (p - 1) * (p ** (e - 1))
        num_divisors *= (e + 1)
        sum_divisors *= (p ** (e + 1) - 1) // (p - 1)
        if e >= 2:
            is_squarefree = False

    mobius = 0 if not is_squarefree else ((-1) ** num_distinct_primes)
    is_perfect = n > 1 and sum_divisors == 2 * n

    return {
        "n": n,
        "factors": {str(p): e for p, e in sorted(factors.items())},
        "euler_totient": euler_totient,
        "num_divisors": num_divisors,
        "sum_divisors": sum_divisors,
        "is_squarefree": is_squarefree,
        "is_perfect": is_perfect,
        "mobius": mobius,
    }

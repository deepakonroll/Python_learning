"""
Lesson 11 - Decorators: Java annotations with actual behaviour.

Run:  python 11_decorators.py

Java annotations (@Override, @Transactional) are METADATA - they need a
framework + reflection to do anything. A Python decorator WRAPS the
function at definition time:  @deco  is sugar for  func = deco(func).
It is the Decorator pattern / AOP @Around advice, built into the language.

You already met some: @property @staticmethod @classmethod
@abstractmethod @dataclass @cache
"""

import functools
import time


def timed(func):
    """Print how long the call took (like a timer advice in AOP)."""

    @functools.wraps(func)           # keeps func.__name__/__doc__ intact
    def wrapper(*args, **kwargs):    # transparently accepts any signature
        start = time.perf_counter()
        result = func(*args, **kwargs)
        elapsed = time.perf_counter() - start
        print(f"  [timed] {func.__name__}() took {elapsed * 1000:.3f} ms")
        return result

    return wrapper


def repeat(times: int):
    """Decorator WITH arguments = a factory that returns a decorator."""

    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            result = None
            for _ in range(times):
                result = func(*args, **kwargs)
            return result

        return wrapper

    return decorator


@timed
def slow_add(a: int, b: int) -> int:
    time.sleep(0.01)                 # pretend this is expensive
    return a + b


@repeat(times=3)
def greet(name: str) -> str:
    print(f"  hello {name}")
    return name


@functools.cache                    # memoization - like a LoadingCache
def fib(n: int) -> int:
    return n if n < 2 else fib(n - 1) + fib(n - 2)


def main() -> None:
    # @timed above is exactly equivalent to:  slow_add = timed(slow_add)
    print("result:", slow_add(2, 40))

    greet("Ada")

    # Decorators stack and apply BOTTOM-UP (innermost first):
    @timed
    @repeat(times=2)
    def noisy():
        print("  noisy body")

    noisy()

    # Without @functools.wraps the wrapped function would report itself
    # as "wrapper" - a classic bug, so always use it.
    print("name preserved:", slow_add.__name__)

    # @cache in action: the second call is (near) instant.
    start = time.perf_counter()
    print("fib(35):", fib(35), f"({(time.perf_counter() - start) * 1000:.2f} ms)")
    start = time.perf_counter()
    print("fib(35):", fib(35), f"({(time.perf_counter() - start) * 1000:.2f} ms)")

    # Decorating manually - the @ syntax is just a call:
    def shout(s: str) -> str:
        return s.upper() + "!"

    loud = timed(shout)
    print(loud("hey"))


if __name__ == "__main__":
    main()

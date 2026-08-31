"""
Lesson 10 - The iteration protocol and generators (lazy pipelines).

Run:  python 10_iterators_and_generators.py

Java -> Python highlights:
  Iterator<E> hasNext/next   -> __iter__/__next__ + StopIteration
  Stream.generate (lazy)     -> generator functions (yield)
  stream().map().filter()    -> generator expressions
  Stream.flatMap             -> yield from / nested generators
  Collectors.toList()        -> list(generator)
"""

import itertools
from collections.abc import Iterator


# ------------------------------------------------------------------
# 1) The protocol: any object with __iter__/__next__ works in for loops.
#    `for x in xs:` is really `for x in iter(xs):`
# ------------------------------------------------------------------
class Countdown:
    """An iterable AND its own iterator (like a Java Iterator class)."""

    def __init__(self, start: int):
        self.current = start

    def __iter__(self) -> "Countdown":
        return self                    # protocol entry point

    def __next__(self) -> int:
        if self.current <= 0:
            raise StopIteration        # the "hasNext() == false" signal
        self.current -= 1
        return self.current + 1


# ------------------------------------------------------------------
# 2) Generators: write an iterator with `yield` - a state machine for
#    free. Execution PAUSES at each yield and RESUMES on the next
#    request (lazy, like a Stream that pulls instead of pushes).
# ------------------------------------------------------------------
def fibonacci(limit: int) -> Iterator[int]:
    a, b = 0, 1
    while a < limit:
        yield a                        # produce a value, pause here
        a, b = b, a + b


def chunked(items: list, size: int) -> Iterator[list]:
    for i in range(0, len(items), size):
        yield items[i:i + size]


def deep_count(tree) -> int:
    """Count leaves of a nested structure recursively."""
    if isinstance(tree, int):
        return 1
    return sum(deep_count(child) for child in tree)   # generator + recursion


def main() -> None:
    print(list(Countdown(5)))          # list() drains any iterable

    for n in Countdown(3):
        print(n, end=" ")
    print()

    # Nothing runs until the generator is consumed (lazy!).
    gen = fibonacci(100)
    print(next(gen), next(gen), next(gen))     # 0 1 1
    print(list(fibonacci(50)))

    print(list(chunked([1, 2, 3, 4, 5], 2)))   # [[1, 2], [3, 4], [5]]
    print(deep_count([1, [2, [3, 4]], 5]))     # 5

    # Generator expressions: lazy pipelines in parentheses.
    squares = (n * n for n in range(1_000_000))    # nothing computed yet
    print(next(squares), next(squares))
    print(sum(n for n in range(100) if n % 7 == 0))  # lazy even inside sum()

    # O(1) memory: values are produced and consumed one at a time.
    print(sum(n * n for n in range(1000)))

    # itertools = the iteration utilities library (Guava-flavoured).
    print(list(itertools.islice(itertools.count(10, 5), 4)))   # 10 15 20 25
    print(list(itertools.chain("ab", [1, 2])))                 # chain any iterables
    print(list(itertools.product("AB", repeat=2)))             # cartesian product


if __name__ == "__main__":
    main()

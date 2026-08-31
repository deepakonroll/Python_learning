"""
Lesson 05 - Functions: far more flexible than Java methods.

Run:  python 05_functions.py

Java -> Python highlights:
  method overloading       -> default args + keyword args (one def does it)
  String... args           -> *args           (varargs)
  (no named arguments)     -> keyword arguments everywhere
  return a record/Pair     -> return a, b     (a tuple, unpacked by caller)
  Function<T,R>            -> functions are plain objects
  anonymous classes        -> closures (with `nonlocal`)
"""


def main() -> None:
    # ------------------------------------------------------------------
    # Basics: def name(params) -> return_type. Docstring = javadoc-lite.
    # A function without `return` returns None.
    # ------------------------------------------------------------------
    def power(base: float, exp: int = 2) -> float:
        """Return base ** exp. Type hints are optional but recommended."""
        return base ** exp

    print(power(3), power(3, exp=3), power(exp=1, base=9))

    # ------------------------------------------------------------------
    # Multiple return values: just return a tuple (no wrapper class!)
    # ------------------------------------------------------------------
    def min_max(nums):
        return min(nums), max(nums)

    lo, hi = min_max([4, 1, 7, 3])
    print(lo, hi)

    # ------------------------------------------------------------------
    # *args / **kwargs  (Java: varargs; kwargs is a whole extra world)
    # ------------------------------------------------------------------
    def report(tag, *values, **options):
        return f"{tag}: {values} verbose={options.get('verbose', False)}"

    print(report("run", 1, 2, 3))
    print(report("run", 1, verbose=True, level=2))

    # Unpack sequences/dicts INTO a call with * and **.
    def connect(host, port, timeout=1.0):
        return f"{host}:{port} (timeout {timeout})"

    config = {"port": 5432, "timeout": 2.5}
    print(connect("db.local", **config))    # pass the whole config dict

    # ------------------------------------------------------------------
    # PITFALL (asked in every interview): mutable default arguments.
    # Defaults are evaluated ONCE at definition, like a static field.
    # ------------------------------------------------------------------
    def buggy(item, bucket=[]):     # the SAME list is shared by all calls
        bucket.append(item)
        return bucket

    print(buggy(1))                 # [1]
    print(buggy(2))                 # [1, 2]  <- the [1] is still in there!

    def fixed(item, bucket=None):   # idiomatic: None as sentinel
        bucket = [] if bucket is None else bucket
        bucket.append(item)
        return bucket

    print(fixed(1))                 # [1]
    print(fixed(2))                 # [2]

    # ------------------------------------------------------------------
    # First-class functions + lambda (like java.util.function)
    # ------------------------------------------------------------------
    ops = {"add": lambda a, b: a + b, "mul": lambda a, b: a * b}
    print(ops["add"](2, 3), ops["mul"](2, 3))

    def apply(fn, value):           # higher-order function
        return fn(value)

    print(apply(str.upper, "shout"), apply(lambda n: n * 10, 4))

    # lambda = single-expression anonymous function (Java: kv -> kv[1])
    pairs = [("b", 2), ("a", 3), ("c", 1)]
    print(sorted(pairs, key=lambda kv: kv[1]))

    # ------------------------------------------------------------------
    # Closures: the inner function captures outer variables.
    # Rebind captured names with `nonlocal` (no effectively-final rule).
    # ------------------------------------------------------------------
    def make_counter(start=0):
        count = start

        def bump(step=1):
            nonlocal count          # like boxing an int in a int[] in Java
            count += step
            return count

        return bump

    tick = make_counter()
    print(tick(), tick(), tick(10))

    # Scope (LEGB): assignment inside a function creates a LOCAL name
    # unless declared global/nonlocal. Avoid `global` in real code.
    level = "outer"

    def shadow():
        level = "inner"             # local; does NOT touch the outer one
        return level

    print(shadow(), level)

    # Generic-style hints: list[str], dict[str, int], str | None (3.10+)
    def find(keys: list[str], key: str) -> str | None:
        return key if key in keys else None

    print(find(["a", "b"], "b"), find(["a"], "z"))


if __name__ == "__main__":
    main()

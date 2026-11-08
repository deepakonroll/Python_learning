"""
Lesson 01 - Python basics, from a Java developer's perspective.

Run:  python 01_basics_and_running.py

Java -> Python highlights in this lesson:
  * JVM/bytecode           -> CPython also compiles to bytecode, but interprets it
  * static typing          -> dynamic typing (optional type hints below)
  * public static void main-> module code + `if __name__ == "__main__":`
  * primitives vs objects  -> everything is an object (even ints)
  * null                   -> None
  * .equals() vs ==        -> == (value) vs `is` (identity)
"""

import sys


def main() -> None:
    # ------------------------------------------------------------------
    # 1) Variables: no declarations, no types. A name is just a label
    #    bound to an object (a reference you can re-point any time).
    # ------------------------------------------------------------------
    x = 42           # Java: int x = 42;
    print(type(x))   # <class 'int'> - ints are objects, not primitives

    x = "now a string"    # REBINDING to another type is legal (dynamic typing!)
    print(type(x), x)

    # Optional type HINTS - not enforced at runtime, but checked by tools
    # (mypy, PyCharm, VS Code Pylance), like a lightweight static analyzer.
    count: int = 10
    name: str = "Ada"
    print(f"{name} has {count} items")

    # ------------------------------------------------------------------
    # 2) Core built-in types
    # ------------------------------------------------------------------
    big = 2 ** 100          # int is ARBITRARY precision -> no overflow,
    print(big)              # no BigInteger needed (Java: Math.addExact!)

    f = 0.1 + 0.2           # float = IEEE 754 double, same as Java
    print(f, f == 0.3)      # so yes, 0.30000000000000004 here too

    flag = True             # bool is a SUBCLASS of int (True == 1)
    print(flag + flag)      # 2 - a quirk, rarely useful, good to know

    nothing = None          # like null, but a real singleton object
    print(nothing is None)  # always compare to None with `is`

    # ------------------------------------------------------------------
    # 3) == vs is   (Java: .equals() vs ==)
    # ------------------------------------------------------------------
    a = [1, 2, 3]
    b = [1, 2, 3]
    print(a == b, a is b)   # True False - == calls __eq__ (equals);
                            # `is` compares object identity (same reference)

    # ------------------------------------------------------------------
    # 4) Truthiness - objects decide their own boolean value
    # ------------------------------------------------------------------
    for value in [0, "", [], {}, None, "x", [1]]:
        print(repr(value), "->", bool(value))
    # Java-ish:  if (s != null && !s.isEmpty()) ...
    # Python:    if s: ...    (same intent, far less ceremony)

    # ------------------------------------------------------------------
    # 5) The `__main__` guard - the closest thing to a Main-Class
    # ------------------------------------------------------------------
    # A .py file is a MODULE. Its code runs top-to-bottom whether it is
    # run directly or imported. The guard lets one file be BOTH a library
    # and a script:
    #   python 01_basics_and_running.py -> __name__ == "__main__" -> runs
    #   import some_module              -> __name__ == "some_module" -> skips
    print("__name__ of this file:", __name__)
    print("python version:", sys.version.split()[0])

    # Style: PEP 8 is the Java style-guide equivalent -> 4-space indent,
    # snake_case for functions/variables, PascalCase for classes,
    # ALL_CAPS for constants.


if __name__ == "__main__":
    main()

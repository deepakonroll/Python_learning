"""
Lesson 04 - Control flow (mostly familiar; watch the quirks).

Run:  python 04_control_flow.py

Java -> Python highlights:
  if / else if / else  -> if / elif / else (colon + indentation, no braces)
  switch (Java 21)     -> match / case     (structural pattern matching)
  for (int i; i<n; i++)-> for i in range(n) (loops ALWAYS iterate something)
  ? :                  -> A if cond else B
  labeled break + flag -> break, plus a unique `for/else` construct
"""

import datetime


def main() -> None:
    # ------------------------------------------------------------------
    # if / elif / else - indentation IS the syntax (no braces, ever)
    # ------------------------------------------------------------------
    score = 83
    if score >= 90:
        grade = "A"
    elif score >= 80:
        grade = "B"                 # this branch runs
    else:
        grade = "C"
    print(grade)

    # Ternary is an expression:  value = A if cond else B
    parity = "even" if score % 2 == 0 else "odd"
    print(parity)

    # Truthiness in conditions (lesson 01): empty list -> False
    items = ["a"]
    if items:
        print("non-empty list")

    # ------------------------------------------------------------------
    # for loops always iterate a collection (like enhanced-for only).
    # Java's counting loop -> range(start, stop, step)  [stop EXCLUDED]
    # ------------------------------------------------------------------
    for i in range(3):
        print(i, end=" ")           # 0 1 2
    print()
    for i in range(10, 0, -2):      # countdown, step -2
        print(i, end=" ")
    print()

    # enumerate -> when you need the index too (no manual counter)
    for idx, lang in enumerate(["C", "Java", "Python"], start=1):
        print(idx, lang, end="; ")
    print()

    # zip -> iterate two collections in parallel (no index juggling)
    for who, s in zip(["Ada", "Alan"], [91, 84]):
        print(f"{who}={s}", end=" ")
    print()

    # ------------------------------------------------------------------
    # break / continue, plus Python's UNIQUE for/else: the else runs only
    # if the loop finished WITHOUT break - it replaces Java's
    # "boolean found = false" flag-variable pattern.
    # ------------------------------------------------------------------
    target = 99
    for n in [4, 8, 15, 16, 23, 42]:
        if n == target:
            print("found", target)
            break
    else:
        print(f"{target} not in list (no flag variable needed)")

    # ------------------------------------------------------------------
    # while + the walrus operator := (assign INSIDE an expression, 3.8+)
    # ------------------------------------------------------------------
    data = [3, 1, 4, 1, 5, 9, 2, 6]
    it = iter(data)
    while (value := next(it)) < 5:  # consume until first value >= 5
        print(value, end=" ")
    print("(stopped at the first 5 via walrus)")

    # ------------------------------------------------------------------
    # match/case (3.10+) - like Java 21 switch expressions, but it can
    # DESTRUCTURE data structures, not just compare constants.
    # ------------------------------------------------------------------
    def handle(event) -> str:
        match event:
            case {"type": "click", "x": x, "y": y}:
                return f"click at {x},{y}"
            case {"type": "key", "key": k} if k in ("esc", "enter"):
                return f"special key {k}"        # guard = extra `if`
            case {"type": kind}:                 # capture any other type
                return f"generic {kind}"
            case _:                              # default
                return "unknown"

    print(handle({"type": "click", "x": 10, "y": 20}))
    print(handle({"type": "key", "key": "esc"}))
    print(handle({"type": "resize"}))
    print(handle("bogus"))

    # Sequences can be matched and split too:
    match [1, 2, 3]:
        case [first, *rest]:
            print(f"head={first} tail={rest}")

    # pass = an empty block (Java: { })
    class Stub:  # placeholder class, real OOP in lesson 06
        pass

    print("today's year:", datetime.date.today().year)


if __name__ == "__main__":
    main()

"""
Lesson 09 - Exceptions: familiar, with two big differences.

Run:  python 09_exceptions.py

Java -> Python highlights:
  try/catch/finally        -> try/except/finally
  throw                    -> raise
  extends Exception        -> subclass Exception
  checked exceptions       -> DO NOT EXIST. Nothing forces callers to catch.
  e.addSuppressed/initCause-> raise ... from e   (chaining)
  containsKey() pre-check  -> EAFP: try the operation, handle the failure.
  Philosophy: EAFP - "Easier to Ask Forgiveness than Permission" - is the
  idiomatic Python style; Java usually checks first (LBYL).
"""

import json


class InsufficientFundsError(Exception):
    """Custom exception = extends Exception, but it can carry state."""

    def __init__(self, needed: float, available: float):
        super().__init__(f"need {needed}, but only {available} available")
        self.needed = needed
        self.available = available


def withdraw(balance: float, amount: float) -> float:
    if amount > balance:
        raise InsufficientFundsError(amount, balance)     # throw new ...
    return balance - amount


def parse_age(text: str) -> int:
    try:
        return int(text)
    except ValueError as e:
        # Re-raise with context, preserving the original cause.
        raise ValueError(f"age must be numeric, got {text!r}") from e


def main() -> None:
    # The full shape: try / except / else / finally
    try:
        print(withdraw(100, 30))
        withdraw(100, 999)                     # this one raises
        print("unreachable")
    except InsufficientFundsError as e:        # catch (InsufficientFunds e)
        print(f"business error: {e} [{e.needed=} {e.available=}]")
    else:                                      # runs only if NO exception
        print("else: no exception happened")
    finally:                                   # like Java finally
        print("finally: always runs")

    # Catching multiple types; no generics tricks needed.
    for payload in ["12", "abc", None]:
        try:
            print(int(payload))                # TypeError for None
        except (ValueError, TypeError) as e:   # multi-catch
            print(f"bad input {payload!r}: {type(e).__name__}: {e}")

    # Catch SPECIFIC types; a bare `except:` catches everything - even
    # Ctrl+C - the Python equivalent of `catch (Exception e)` abuse.
    try:
        json.loads("{not json}")
    except json.JSONDecodeError as e:          # subclass of ValueError
        print(f"json: {e} (line {e.lineno})")

    # Chaining: `from e` preserves the cause, like initCause.
    try:
        parse_age("abc")
    except ValueError as e:
        print(f"wrapped: {e} | cause: {e.__cause__!r}")

    # EAFP vs LBYL on a dict lookup:
    scores = {"ada": 91}
    # LBYL (Java-style): if (map.containsKey("ada")) ...
    if "ada" in scores:
        print("LBYL:", scores["ada"])
    # EAFP (Python-style): just do it, handle the KeyError
    try:
        print("EAFP:", scores["alan"])
    except KeyError:
        print("EAFP: KeyError for missing key")

    # Raising your own, and `assert` (disabled with python -O; NOT for
    # input validation - use exceptions for that).
    try:
        raise RuntimeError("explicit raise")
    except RuntimeError as e:
        print("caught:", e)


if __name__ == "__main__":
    main()

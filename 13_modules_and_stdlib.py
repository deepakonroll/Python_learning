"""
Lesson 13 - Modules, imports, the standard library, and the ecosystem.

Run:  python 13_modules_and_stdlib.py

Java -> Python highlights:
  package + import       -> module = one .py file; package = a folder
  import com.x.Y;        -> from mypackage import Y   (or: import mypkg.Y)
  fully qualified names  -> module.func (namespaces are explicit and free)
  Maven/Gradle           -> pip + virtual environments (see README)
  SLF4J/Logback          -> the logging module
"""

import logging
import math
import random as rnd                    # aliasing, like `import x as y`
import sys
from datetime import date, timedelta    # selective import


def main() -> None:
    # ------------------------------------------------------------------
    # Three import styles - pick per taste/convention:
    # ------------------------------------------------------------------
    print(math.sqrt(2))                      # namespace kept: math.x
    print(date.today() + timedelta(days=7))  # names pulled into scope
    print(rnd.randint(1, 6))                 # aliased module

    # A module is just a file; everything in it is importable.
    # Browse a module's public API like you would a class:
    print(sorted(n for n in dir(math) if not n.startswith("_"))[:8])

    # Modules are objects too: they have __name__, __doc__, attributes.
    print(math.__name__, sys.__name__)

    # ------------------------------------------------------------------
    # The standard library ("batteries included") - zero dependencies:
    # ------------------------------------------------------------------
    samples = {
        "os / pathlib": "files & paths",
        "json, csv, sqlite3": "data formats & an embedded SQL DB",
        "datetime / zoneinfo": "dates, times, time zones",
        "re": "regular expressions",
        "collections, itertools, functools": "data & functional helpers",
        "logging": "logging (SLF4J-ish)",
        "argparse": "CLI argument parsing",
        "unittest / pytest": "testing",
        "typing, dataclasses, enum, abc": "type & structure tools",
        "asyncio": "async I/O (CompletableFuture-ish)",
    }
    for lib, note in samples.items():
        print(f"  {lib:<38} {note}")

    # logging quickstart (Java: LoggerFactory + logback config)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    log = logging.getLogger("tour")
    log.info("application started, python %s", sys.version.split()[0])
    log.warning("this is what WARN looks like")

    # ------------------------------------------------------------------
    # Your own modules/packages:
    #   mypkg/__init__.py    <- marks the folder as a package (convention)
    #   mypkg/orders.py      <- import mypkg.orders / from mypkg import orders
    # Imports resolve via sys.path (the classpath equivalent); the
    # script's own directory is on it automatically. Virtual environments
    # keep each project's dependencies isolated (README shows how).
    # ------------------------------------------------------------------

    # The Zen of Python - the culture in 19 aphorisms. Importing the
    # `this` module prints it (an Easter egg you should actually read):
    import this  # noqa: F401

    print("lesson complete - see README.md for tooling & next steps")


if __name__ == "__main__":
    main()

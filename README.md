# Python for Java Developers — a 13-lesson guided tour

Every lesson is a **standalone runnable file**: run it, read its output,
then read the source top-to-bottom. Java equivalents are called out in the
docstring at the top of every file and in `# Java:`-style comments inline.

Tested on Python 3.11 (needs **3.10+** for the `match` lesson).

## How to run (PowerShell)

```powershell
# from this folder (it is the project root):
python 01_basics_and_running.py       # one lesson at a time, or run all:
Get-ChildItem *.py | Sort-Object Name | ForEach-Object { python $_.FullName }
```

> The filenames start with digits on purpose: it keeps the reading order
> obvious and makes them non-importable as modules — each lesson is meant
> to be read and run standalone.

## Lessons

| File | Topic | Closest Java thing |
|------|-------|--------------------|
| 01_basics_and_running.py | dynamic typing, core types, `__main__` guard, truthiness | static typing, `main()`, `null`, `equals()` vs `==` |
| 02_strings_and_formatting.py | f-strings, slicing, string methods | `String.format`, `substring`, immutability |
| 03_collections.py | list / tuple / dict / set, comprehensions | `ArrayList`, records, `HashMap`, `HashSet`, Streams |
| 04_control_flow.py | if/elif, for-in, range, enumerate, zip, `match`, walrus | switch expressions, enhanced for, labeled break |
| 05_functions.py | defaults, kwargs, `*args/**kwargs`, lambdas, closures | overloading, varargs, `Function<T,R>`, anonymous classes |
| 06_oop_basics.py | classes, `__init__`, inheritance, dunder methods, operator overloading | constructors, `extends`, `toString`/`equals` |
| 07_dataclasses_enums_properties.py | `@dataclass`, `Enum`, `@property`, `@classmethod` | records, enums, JavaBeans, static factories |
| 08_duck_typing_and_interfaces.py | duck typing, `abc.ABC`, `Protocol`, MRO | interfaces, abstract classes, multiple inheritance |
| 09_exceptions.py | try/except/else/finally, custom exceptions, EAFP | try/catch/finally — but **no checked exceptions** |
| 10_iterators_and_generators.py | iterator protocol, `yield`, generator expressions, itertools | `Iterator`, lazy Streams, `flatMap` |
| 11_decorators.py | decorators with and without arguments, `@cache` | annotations that actually run (AOP / Decorator pattern) |
| 12_context_managers_and_files.py | `with`, files, `pathlib`, JSON | try-with-resources, `java.nio.file`, Jackson |
| 13_modules_and_stdlib.py | imports, packages, stdlib tour, logging, pip/venv | packages, classpath, SLF4J, Maven/Gradle |

## The five differences that matter most

1. **Dynamic typing** — names have no type; objects do. Type hints are
   optional and checked by tools (mypy, IDEs), not enforced at runtime.
2. **Indentation is syntax** — no braces, ever. 4 spaces, PEP 8 style.
3. **No checked exceptions** — nothing forces callers to catch; failures
   are handled with the EAFP idiom ("easier to ask forgiveness...").
4. **Everything is an object** — even `int`; even classes and functions,
   which is what makes decorators and lambdas trivial.
5. **Operator overloading & "magic" methods** — `__add__`, `__len__`,
   `__getitem__`, ... replace lots of Java boilerplate interfaces.

## Tooling map

| Java world | Python world |
|------------|--------------|
| Maven/Gradle, pom.xml | `pip`, `requirements.txt`, `pyproject.toml` |
| Checkstyle/SpotBugs | `ruff` (lint), `black` (format) |
| JUnit | `pytest` |
| javadoc | docstrings + `help(obj)` |
| IntelliJ IDEA | PyCharm / VS Code + Pylance |

Quickstart for a real project:

```powershell
python -m venv .venv                  # per-project isolated deps
.\.venv\Scripts\Activate.ps1
pip install ruff pytest
ruff check .
pytest
```

## Deliberately not covered (your next steps)

- `asyncio` (async/await) — the CompletableFuture / virtual-threads analog
- threads & multiprocessing (ask about the GIL)
- `pytest` in depth, `typing` deep dive (`Generic`, `TypeVar`, `TypedDict`)
- packaging & publishing (`pyproject.toml`, PyPI)
- web/API frameworks (FastAPI), data stack (pandas, numpy)
- See **ROADMAP.md** for the guided path into data science & LLMs

Read the official tutorial next: <https://docs.python.org/3/tutorial/> —
it is genuinely good, and short.

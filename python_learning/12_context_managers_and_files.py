"""
Lesson 12 - Context managers (`with`) and file I/O.

Run:  python 12_context_managers_and_files.py

Java -> Python highlights:
  try-with-resources / AutoCloseable -> with statement / __enter__ + __exit__
  java.nio.file.Path / Files         -> pathlib.Path
  Files.newBufferedReader + charset  -> open(..., encoding="utf-8")
  BufferedReader.lines()             -> for line in file:   (streams lazily)
  Jackson ObjectMapper               -> the json module
"""

import json
import pathlib
import tempfile
import time
from contextlib import contextmanager


class Timer:
    """A context manager class = AutoCloseable via __enter__/__exit__."""

    def __enter__(self):
        self.start = time.perf_counter()
        return self                       # this becomes the `as` target

    def __exit__(self, exc_type, exc_value, traceback):
        print(f"  [Timer] elapsed {time.perf_counter() - self.start:.4f}s")
        return False                      # False = do NOT swallow exceptions


@contextmanager
def section(title: str):
    """Generator-based context manager: code before `yield` = __enter__,
    code after = __exit__. Far less ceremony than a class."""
    print(f"--- {title} ---")
    yield
    print(f"--- end {title} ---")


def main() -> None:
    with Timer():                         # cleanup runs even on exceptions
        time.sleep(0.01)
        print("  work inside the with-block")

    with section("classic file io"):
        # tempfile gives a self-cleaning directory (itself a context manager).
        with tempfile.TemporaryDirectory() as tmp:
            base = pathlib.Path(tmp)

            # WRITE - always pass encoding (Java: StandardCharsets.UTF_8).
            notes = base / "notes.txt"    # `/` joins paths, nio-style
            with open(notes, "w", encoding="utf-8") as f:
                f.write("first line\n")
                f.writelines(["second line\n", "third line\n"])

            # READ - whole file at once, or lazily line by line.
            print(notes.read_text(encoding="utf-8").strip())
            with open(notes, encoding="utf-8") as f:
                for i, line in enumerate(f, 1):     # streams; safe for huge files
                    if i == 2:
                        print("line 2:", line.strip())

            # APPEND mode "a"; pathlib also has write_text/read_text one-liners.
            with open(notes, "a", encoding="utf-8") as f:
                f.write("appended line\n")

            # Globbing (DirectoryStream vibes).
            print([p.name for p in base.glob("*.txt")])

    with section("json (jackson-ish)"):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "config.json"
            config = {"app": "tour", "retries": 3, "tags": ["a", "b"]}
            path.write_text(json.dumps(config, indent=2), encoding="utf-8")
            loaded = json.loads(path.read_text(encoding="utf-8"))
            print(loaded["app"], loaded["retries"], loaded["tags"])

            # dumps/loads also work on plain strings, no files needed.
            print(json.dumps({"ok": True}))          # {"ok": true}
            print(json.loads('{"n": 42}')["n"])      # 42


if __name__ == "__main__":
    main()

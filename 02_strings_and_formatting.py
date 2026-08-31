"""
Lesson 02 - Strings: like Java's String, plus slicing and f-strings.

Run:  python 02_strings_and_formatting.py

Java -> Python highlights:
  String.format(...)   -> f-strings
  substring(a, b)      -> s[a:b]        (slicing)
  String is immutable  -> same in Python
  char                 -> a 1-character str (there is no char type)
  StringBuilder        -> "".join(parts) or just f-strings
"""


def main() -> None:
    # Literals: single or double quotes are identical (no char vs String).
    s1 = "double"
    s2 = "single"
    s3 = "it's easy"            # no escaping needed
    s4 = """triple-quoted,
spans lines"""                  # Java 15 text blocks """
    print(s1, s2, s3, s4, sep=" | ")

    raw = r"C:\Users\new\not-an-escape"   # r"" = raw string, backslashes win
    print(raw)

    # f-strings: the workhorse (Java: String.format / MessageFormat).
    user, score = "Ada", 91.4567
    print(f"{user} scored {score:.1f}%")   # format specifiers work
    print(f"{score=}")                     # debug form, prints score=91.4567
    print(f"{score:10.2f}|")               # width + precision, like %10.2f
    print(f"{'left':<10}|{'right':>10}|{'mid':^10}")

    # Indexing and slicing (immutable like Java String, but with negative
    # indices and slice syntax - very idiomatic, no substring()).
    word = "generator"
    print(word[0], word[-1])      # g r   (negative index = from the end)
    print(word[0:3], word[3:])    # gen erator   [start:stop), like substring
    print(word[::-1])             # reversed copy - the famous party trick
    print(len(word))              # length is a builtin function, not a method

    # Strings are immutable - "modifying" builds a new object.
    try:
        word[0] = "G"
    except TypeError as e:        # lesson 09 covers exceptions properly
        print("immutable:", e)
    upper = word.upper()          # new object, original untouched
    print(word, upper)

    # The methods you will actually use (all return NEW strings).
    csv = "  ada, grace, alan  "
    names = [n.strip().title() for n in csv.split(",")]
    print(names)                          # split + strip + title
    print("-".join(names))                # String.join("-", ...)
    print(csv.replace("ada", "ADA").strip())
    print("generator".startswith("gen"), "abc".find("b"))  # indexOf -> find
    print("pp" in "apple")                # contains -> the `in` operator
    print(",".join(str(i) for i in range(5)))   # join takes any iterable of str

    # Conversion: str() is toString(); int()/float() parse (parseInt etc.).
    n = int("42")
    print(n + 1, str(42) + "!", float("3.5"))

    # Reports: build them with join + f-strings, not StringBuilder chains.
    rows = [("Ada", 91.5), ("Alan", 84.0)]
    table = "\n".join(f"{name:<10}{s:>6.1f}" for name, s in rows)
    print(table)


if __name__ == "__main__":
    main()

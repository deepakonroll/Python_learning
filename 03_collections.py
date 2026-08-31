"""
Lesson 03 - Built-in collections: list, tuple, dict, set + comprehensions.

Run:  python 03_collections.py

Java -> Python mapping:
  ArrayList<String>  -> list[str]      (ordered, mutable, resizable)
  List.of(...)       -> tuple          (ordered, IMMUTABLE)
  HashMap<K,V>       -> dict           (insertion-ordered since 3.7!)
  HashSet<T>         -> set            (unique, unordered)
  Comparator<T>      -> key=lambda ... (no interface needed)
  stream()...collect -> comprehensions (one expression, no collectors)
"""

import collections


def main() -> None:
    # ------------------------------------------------------------------
    # list  (ArrayList)
    # ------------------------------------------------------------------
    langs = ["Java", "Python", "Go"]   # literal; mixed types allowed
    langs.append("Rust")               # add
    langs.insert(0, "C")               # add(index, e)
    last = langs.pop()                 # remove & return last element
    langs.remove("Go")                 # remove first matching value
    print(langs, "| popped:", last)
    print(len(langs), "Java" in langs) # size() / contains()
    print(langs[0], langs[-1])         # indexing; negative = from the end
    print(langs[1:3])                  # slice -> NEW list [start:stop)
    print(sorted(langs))               # sorted copy (in-place would be .sort())
    langs.sort()                       # in-place, like Collections.sort
    print(langs)

    # ------------------------------------------------------------------
    # tuple  (immutable list; also how functions return multiple values)
    # ------------------------------------------------------------------
    point = (3, 4)          # cannot append or assign elements
    x, y = point            # unpacking - destructure in one line
    a, b = 1, 2
    a, b = b, a             # THE swap, no temp variable
    print(x, y, a, b)

    # ------------------------------------------------------------------
    # dict  (HashMap, but keeps insertion order)
    # ------------------------------------------------------------------
    ages = {"Ada": 36, "Alan": 41}
    ages["Grace"] = 45                    # put
    print(ages["Ada"], ages.get("Bob"))   # [] raises KeyError, get -> None
    print(ages.get("Bob", 0))             # getOrDefault
    print("Ada" in ages)                  # containsKey
    del ages["Grace"]                     # remove
    for who, age in ages.items():         # entrySet()
        print(f"  {who}: {age}")
    print(list(ages.keys()), list(ages.values()))
    print(ages | {"Bob": 30})             # 3.9+ merge -> NEW dict (putAll)

    # ------------------------------------------------------------------
    # set  (HashSet)
    # ------------------------------------------------------------------
    seen = {1, 2, 3}
    seen.add(3)                           # duplicate ignored
    print(seen, len(seen))
    print({1, 2, 3} & {2, 3, 4})          # intersection (retainAll)
    print({1, 2, 3} | {2, 3, 4})          # union (addAll)
    print({1, 2, 3} - {2})                # difference (removeAll)
    print({1, 2} <= {1, 2, 3})            # subset test

    # ------------------------------------------------------------------
    # Sorting with key=  (Comparator without the interface)
    # ------------------------------------------------------------------
    people = [("Ada", 36), ("Alan", 41), ("Grace", 45)]
    print(sorted(people, key=lambda p: p[1]))
    print(sorted(people, key=lambda p: p[1], reverse=True))

    # ------------------------------------------------------------------
    # Comprehensions - Java Streams in a single expression
    # ------------------------------------------------------------------
    squares = [n * n for n in range(10)]                  # map + toList
    evens = [n for n in range(10) if n % 2 == 0]          # filter + toList
    labels = {n: ("even" if n % 2 == 0 else "odd") for n in range(4)}  # toMap
    unique = {w.lower() for w in "Hi hi HO".split()}      # set comprehension
    lazy = (n * n for n in range(10))                     # lazy, like a Stream
    print(squares, evens, labels, unique, next(lazy))

    # ------------------------------------------------------------------
    # collections module - batteries included
    # ------------------------------------------------------------------
    words = "the quick the lazy the dog".split()
    counts = collections.Counter(words)     # frequency Map, one line
    print(counts.most_common(2))
    grouped = collections.defaultdict(list) # computeIfAbsent pattern
    grouped["even"].append(2)
    grouped["odd"].append(3)
    print(dict(grouped))
    Pair = collections.namedtuple("Pair", "x y")  # immutable record-ish
    print(Pair(1, 2).x)


if __name__ == "__main__":
    main()

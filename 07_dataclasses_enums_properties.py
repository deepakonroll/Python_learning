"""
Lesson 07 - Removing boilerplate: dataclasses, enums, properties, classmethod.

Run:  python 07_dataclasses_enums_properties.py

Java -> Python highlights:
  record / Lombok @Data  -> @dataclass
  enum                   -> class X(Enum)
  getters/setters        -> @property (or skip them entirely - direct attrs)
  static factory method  -> @classmethod
  static utility method  -> @staticmethod
"""

from dataclasses import dataclass, field, asdict, FrozenInstanceError
from enum import Enum, auto


# ----------------------------------------------------------------------
# @dataclass generates __init__, __repr__, __eq__ (and more) for you -
# like Java records, but mutable by default.
# ----------------------------------------------------------------------
@dataclass
class Task:
    title: str
    priority: int = 1
    tags: list[str] = field(default_factory=list)   # mutable default, done RIGHT


@dataclass(frozen=True)        # frozen=True -> immutable like a record
class Point:
    x: float
    y: float


@dataclass(order=True)         # adds < <= > >= comparing fields in order
class Version:
    major: int
    minor: int


class Priority(Enum):
    LOW = 1
    MEDIUM = auto()            # auto() continues the numbering (2, 3, ...)
    HIGH = 3

    def label(self) -> str:    # enums can have methods, like Java
        return self.name.lower()


class Temperature:
    """@property: getter/setter without the JavaBean ceremony."""

    def __init__(self, celsius: float = 0.0):
        self.celsius = celsius          # NOTE: this calls the SETTER below!

    @property
    def celsius(self) -> float:
        return self._c

    @celsius.setter
    def celsius(self, value: float) -> None:
        if value < -273.15:
            raise ValueError("below absolute zero")
        self._c = value

    @property
    def fahrenheit(self) -> float:      # computed attribute, no storage
        return self._c * 9 / 5 + 32


class Pizza:
    def __init__(self, ingredients: list[str]):
        self.ingredients = ingredients

    @classmethod
    def margherita(cls) -> "Pizza":             # alternative constructor
        return cls(["mozzarella", "tomato"])

    @staticmethod
    def is_vegetarian(ingredients: list[str]) -> bool:   # no self needed
        return "pepperoni" not in ingredients


def main() -> None:
    t1 = Task("write lessons", 2, ["docs"])
    t2 = Task("write lessons", 2, ["docs"])
    print(t1)                    # auto __repr__
    print(t1 == t2)              # auto __eq__ (field by field)
    t1.tags.append("urgent")     # mutable, as designed
    print(asdict(t1))            # -> plain dict, handy for JSON later

    p = Point(1.5, 2.5)
    try:
        p.x = 99                 # frozen dataclass refuses assignment
    except FrozenInstanceError as e:
        print("frozen:", e)

    print(Version(1, 4) > Version(1, 2))    # ordering for free

    print(Priority.HIGH is Priority(3))     # lookup by value; compare with is
    print(Priority.MEDIUM.value, Priority.MEDIUM.name, Priority.HIGH.label())
    print([p.name for p in Priority])       # iterate, like Java values()

    t = Temperature(25)
    print(t.celsius, t.fahrenheit)          # property READ - no parentheses!
    t.celsius = 30                          # property WRITE - validation runs
    try:
        t.celsius = -300
    except ValueError as e:
        print("validation:", e)

    veg = Pizza.margherita()
    print(veg.ingredients, Pizza.is_vegetarian(veg.ingredients))


if __name__ == "__main__":
    main()

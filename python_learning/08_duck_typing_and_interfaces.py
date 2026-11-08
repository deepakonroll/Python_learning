"""
Lesson 08 - Interfaces, but different: duck typing, ABCs, Protocols.

Run:  python 08_duck_typing_and_interfaces.py

Java is NOMINAL: a class implements an interface by declaring it.
Python is mostly STRUCTURAL at runtime ("duck typing"): if the object
has the method, it works - no declaration needed.

Java -> Python options:
  interface            -> duck typing (implicit) | abc.ABC (formal, nominal)
                          | typing.Protocol (formal, structural - checked by mypy)
  abstract class       -> abc.ABC + @abstractmethod
  default methods      -> plain methods in the base class
  instanceof           -> isinstance(obj, Type)
  (Java forbids multiple CLASS inheritance; Python allows it - meet the MRO.)
"""

from abc import ABC, abstractmethod
from typing import Protocol, runtime_checkable


# 1) Duck typing: no interface, no cast, no declaration - it just works.
class Duck:
    def quack(self) -> str:
        return "quack"


class Robot:
    def quack(self) -> str:         # completely unrelated class, same shape
        return "beep-quack"


def make_it_quack(thing) -> str:    # Java would demand: interface Quacker
    return thing.quack()            # if it quacks like a duck...


# 2) ABC: formal inheritance + enforced abstract methods (abstract class).
class Shape(ABC):
    @abstractmethod
    def area(self) -> float:
        ...

    def describe(self) -> str:      # a "default method"
        return f"a shape with area {self.area():.2f}"


class Circle(Shape):
    def __init__(self, r: float):
        self.r = r

    def area(self) -> float:
        return 3.14159 * self.r ** 2


# 3) Protocol: structural typing, checked by mypy/PyCharm WITHOUT
# inheritance (like Go interfaces). @runtime_checkable also enables
# a (shallow) isinstance check: "does it have a close attribute?"
@runtime_checkable
class Closable(Protocol):
    def close(self) -> None:
        ...


class DatabaseConn:
    def close(self) -> None:
        pass


class NoClose:
    pass


# 4) Multiple inheritance + MRO (the diamond problem, solved by C3 order)
class A:
    def hello(self) -> str:
        return "A"


class B(A):
    def hello(self) -> str:
        return "B"


class C(A):
    def hello(self) -> str:
        return "C"


class D(B, C):          # Java: illegal for classes; Python: fine, well-defined
    pass


def main() -> None:
    print(make_it_quack(Duck()), "|", make_it_quack(Robot()))
    print("has close:", hasattr(DatabaseConn(), "close"))

    try:
        Shape()         # abstract - cannot instantiate (like Java)
    except TypeError as e:
        print("abstract:", e)

    print(Circle(2).describe())                   # abstract + default method

    print(isinstance(DatabaseConn(), Closable))   # True  (it has .close)
    print(isinstance(NoClose(), Closable))        # False

    d = D()
    print(d.hello())                              # "B" - first in MRO wins
    print([c.__name__ for c in D.__mro__])        # D, B, C, A, object


if __name__ == "__main__":
    main()

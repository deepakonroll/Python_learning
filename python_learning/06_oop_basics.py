"""
Lesson 06 - Classes and OOP, part 1 (the Java-comfortable part).

Run:  python 06_oop_basics.py

Java -> Python highlights:
  class / new         -> class ...:  /  just call it (there is no `new`)
  constructor         -> __init__(self, ...)   (self = this, but explicit)
  this                -> self, and it MUST be the first method parameter
  static fields       -> class attributes
  virtual methods     -> methods are ALWAYS virtual (no final methods)
  method overloading  -> not supported; use default arguments instead
  toString/equals     -> __str__/__repr__ and __eq__ (+ __hash__)
  private             -> _convention or __name_mangling; nothing is truly private
  (no equivalent)     -> operator overloading (__add__, __len__, ...)
"""


class Animal:
    species = "Animalia"                  # class attribute (like static field)

    def __init__(self, name: str):        # constructor
        self.name = name                  # instance attribute (a field)

    def speak(self) -> str:               # every method starts with self
        return f"{self.name} makes a sound"

    def __str__(self) -> str:             # toString()
        return f"Animal(name={self.name!r})"


class Dog(Animal):                        # extends Animal
    def __init__(self, name: str, breed: str):
        super().__init__(name)            # super(...) call is explicit
        self.breed = breed

    def speak(self) -> str:               # @Override is implicit (all virtual)
        return f"{self.name} says woof"


class Vector2D:
    """Operator overloading - something Java simply does not have."""

    def __init__(self, x: float, y: float):
        self.x, self.y = x, y

    def __add__(self, other: "Vector2D") -> "Vector2D":    # a + b
        return Vector2D(self.x + other.x, self.y + other.y)

    def __eq__(self, other) -> bool:                       # a == b (equals)
        return isinstance(other, Vector2D) and (self.x, self.y) == (other.x, other.y)

    def __hash__(self) -> int:                             # keep the equals/hash pair
        return hash((self.x, self.y))

    def __repr__(self) -> str:                             # debug string
        return f"Vector2D({self.x}, {self.y})"

    def __len__(self) -> int:                              # len(v)
        return 2

    def __getitem__(self, idx: int):                       # v[0], v[1] ...
        return (self.x, self.y)[idx]       # also enables iteration & unpacking


def main() -> None:
    a = Animal("Generic")                 # no `new` keyword
    d = Dog("Rex", "labrador")
    print(a.speak(), "|", d.speak())      # polymorphism: virtual dispatch
    print(isinstance(d, Animal))          # instanceof
    print(Animal.species, Dog.species)    # class attribute is inherited
    print(str(a), "|", repr(a))           # str vs repr (no __repr__ defined)

    v1, v2 = Vector2D(1, 2), Vector2D(3, 4)
    print(v1 + v2)                        # operator overloading!
    print(v1 == Vector2D(1, 2), v1 == v2)
    print(v1[0], v1[1], len(v1), list(v1))    # container-like behaviour

    # Encapsulation: everything is public. One underscore = "internal,
    # please don't touch"; two = name-mangled to _ClassName__attr.
    # Neither is security - it's convention, enforced by embarrassment.
    class Account:
        def __init__(self, balance: float):
            self._balance = balance       # convention: protected-ish

        @property
        def balance(self) -> float:       # getter without getBalance()
            return self._balance

    acc = Account(100.0)
    print(acc.balance)                    # attribute access, method behind it


if __name__ == "__main__":
    main()

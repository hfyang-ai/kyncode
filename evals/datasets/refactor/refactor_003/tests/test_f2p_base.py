from animal import Animal
from cat import Cat
from dog import Dog


def test_inheritance():
    assert issubclass(Cat, Animal)
    assert issubclass(Dog, Animal)

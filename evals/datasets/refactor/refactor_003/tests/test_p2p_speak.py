from cat import Cat
from dog import Dog


def test_cat():
    assert Cat().speak() == 'meow'


def test_dog():
    assert Dog().speak() == 'woof'

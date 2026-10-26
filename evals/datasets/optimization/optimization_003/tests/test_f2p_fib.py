from fib import fib


def test_small():
    assert fib(0) == 0
    assert fib(1) == 1
    assert fib(10) == 55


def test_large():
    # 用迭代自测大 n，要求秒级返回
    n = 200000
    a, b = 0, 1
    for _ in range(n):
        a, b = b, a + b
    assert fib(n) == a

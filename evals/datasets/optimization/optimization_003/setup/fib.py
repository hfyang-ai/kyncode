def fib_small(n):
    a, b = 0, 1
    for _ in range(n):
        a, b = b, a + b
    return a


def fib(n):
    raise NotImplementedError

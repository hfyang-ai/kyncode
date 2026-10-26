import threading
from counter import ThreadSafeCounter


def test_concurrent_increment():
    c = ThreadSafeCounter()
    n_threads, per = 8, 20000
    def worker():
        for _ in range(per):
            c.increment()
    threads = [threading.Thread(target=worker) for _ in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert c.value() == n_threads * per


def test_initial():
    assert ThreadSafeCounter().value() == 0

import threading
from kv import ThreadSafeKV


def test_concurrent_set():
    kv = ThreadSafeKV()
    def worker(i):
        for j in range(1000):
            kv.set(i, j)
    threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    for i in range(10):
        assert kv.get(i) == 999


def test_basic_ops():
    kv = ThreadSafeKV()
    kv.set('a', 1)
    assert kv.get('a') == 1
    kv.delete('a')
    assert kv.get('a', 'missing') == 'missing'

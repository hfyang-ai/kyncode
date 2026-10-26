import threading
from pipeline import BoundedQueue


def test_producer_consumer():
    q = BoundedQueue()
    consumed = []
    def producer():
        for i in range(100):
            q.put(i)
        q.close()
    def consumer():
        for item in q:
            consumed.append(item)
    t1 = threading.Thread(target=producer)
    t2 = threading.Thread(target=consumer)
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    assert consumed == list(range(100))

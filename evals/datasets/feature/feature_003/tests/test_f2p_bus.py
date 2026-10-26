from bus import EventBus


def test_on_emit():
    b = EventBus()
    got = []
    b.on('save', lambda x: got.append(x))
    b.emit('save', 42)
    assert got == [42]


def test_once():
    b = EventBus()
    got = []
    b.once('init', lambda: got.append(1))
    b.emit('init')
    b.emit('init')
    assert got == [1]


def test_unsubscribe():
    b = EventBus()
    got = []
    off = b.on('tick', lambda: got.append(1))
    off()
    b.emit('tick')
    assert got == []


def test_order():
    b = EventBus()
    order = []
    b.on('x', lambda: order.append(1))
    b.on('x', lambda: order.append(2))
    b.emit('x')
    assert order == [1, 2]

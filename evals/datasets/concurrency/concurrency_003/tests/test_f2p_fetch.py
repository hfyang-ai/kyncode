import asyncio
from fetcher import fetch_all


def test_fetch_all():
    result = asyncio.run(fetch_all(['a', 'b', 'c', 'd']))
    assert result == ['a!', 'b!', 'c!', 'd!']


def test_empty():
    assert asyncio.run(fetch_all([])) == []

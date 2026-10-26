import asyncio
from fetcher import fetch


def test_fetch_one():
    assert asyncio.run(fetch('x')) == 'x!'

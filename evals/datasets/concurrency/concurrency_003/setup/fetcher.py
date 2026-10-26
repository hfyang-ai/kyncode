import asyncio


async def fetch(url):
    await asyncio.sleep(0)
    return f'{url}!'


async def fetch_all(urls):
    raise NotImplementedError

from config_store import get


def snapshot(key):
    cfg = get(key)
    return {'name': cfg['name'], 'tags': list(cfg['tags'])}

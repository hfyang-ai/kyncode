def process(data):
    cleaned = [x.strip() for x in data]
    filtered = [x for x in cleaned if x]
    return filtered

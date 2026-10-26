def walk(entries):
    for entry in entries:
        if entry['type'] == 'file':
            yield entry


def total_size(entries):
    files = walk(entries)
    return sum(f['size'] for f in files)


def count_files(entries):
    return sum(1 for _ in walk(entries))

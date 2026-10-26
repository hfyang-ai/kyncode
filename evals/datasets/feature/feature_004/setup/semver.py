def parse(version):
    return tuple(int(x) for x in version.split('.'))


def compare(v1, v2):
    raise NotImplementedError

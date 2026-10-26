def strip_comment(line):
    return line.split('#')[0].split(';')[0].strip()


def parse_ini(text):
    raise NotImplementedError

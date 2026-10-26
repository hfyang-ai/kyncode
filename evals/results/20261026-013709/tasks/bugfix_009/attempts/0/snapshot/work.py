import re


def extract_digits(s):
    return re.findall(r'[a-z]+', s)

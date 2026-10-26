import re


def extract_digits(s):
    return re.findall(r'\d+', s)

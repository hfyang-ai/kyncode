def is_valid_name(name):
    return bool(name and name.strip())


def is_valid_email(email):
    return '@' in email

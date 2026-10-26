from models import make_user
from repo import Repo
from validators import is_valid_name, is_valid_email


class UserService:
    def __init__(self):
        self._repo = Repo()
        self._next_id = 1

    def register(self, name, email):
        raise NotImplementedError

    def find_by_email(self, email):
        raise NotImplementedError

    def count(self):
        raise NotImplementedError

    def all_names(self):
        raise NotImplementedError

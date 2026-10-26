class Repo:
    def __init__(self):
        self._users = []

    def add(self, user):
        self._users.append(user)

    def find(self, email):
        for u in self._users:
            if u['email'] == email:
                return u
        return None

    def all(self):
        return list(self._users)

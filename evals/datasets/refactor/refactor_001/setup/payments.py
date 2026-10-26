class CreditPayment:
    def validate(self, amount):
        if amount <= 0:
            raise ValueError('amount must be positive')

    def process(self, amount):
        self.validate(amount)
        return f'credit:{amount}'


class DebitPayment:
    def validate(self, amount):
        if amount <= 0:
            raise ValueError('amount must be positive')

    def process(self, amount):
        self.validate(amount)
        return f'debit:{amount}'


class CryptoPayment:
    def validate(self, amount):
        if amount <= 0:
            raise ValueError('amount must be positive')

    def process(self, amount):
        self.validate(amount)
        return f'crypto:{amount}'

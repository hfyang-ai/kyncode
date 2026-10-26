from payments import DebitPayment, CryptoPayment


def test_debit():
    assert DebitPayment().process(5) == 'debit:5'


def test_crypto():
    assert CryptoPayment().process(7) == 'crypto:7'

import pytest
from payments import PaymentProcessor, CreditPayment, DebitPayment, CryptoPayment


def test_inheritance():
    assert issubclass(CreditPayment, PaymentProcessor)
    assert issubclass(DebitPayment, PaymentProcessor)
    assert issubclass(CryptoPayment, PaymentProcessor)


def test_base_validate():
    with pytest.raises(ValueError):
        CreditPayment().process(0)


def test_process_via_base():
    assert CreditPayment().process(10) == 'credit:10'

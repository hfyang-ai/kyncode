import inspect
import funcs


def test_concat_annotated():
    sig = inspect.signature(funcs.concat)
    assert sig.return_annotation is not inspect.Signature.empty


def test_is_positive_annotated():
    sig = inspect.signature(funcs.is_positive)
    assert sig.return_annotation is not inspect.Signature.empty

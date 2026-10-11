"""utils.stale_integration_tests: which passed integration tests ran on an image
the registry no longer certifies as its name's latest."""
from utils import stale_integration_tests

A, B = "QmaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaA", "QmbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbB"


def test_the_same_latest_image_keeps_the_test():
    assert stale_integration_tests({"etny-pynithy-testnet": A}, {"etny-pynithy-testnet": A}) == set()


def test_another_latest_image_makes_the_test_stale():
    assert stale_integration_tests({"etny-pynithy-testnet": A}, {"etny-pynithy-testnet": B}) == {"etny-pynithy-testnet"}


def test_a_name_the_registry_no_longer_holds_is_stale():
    assert stale_integration_tests({"etny-pynithy-testnet": A}, {"etny-pynithy-testnet": None}) == {"etny-pynithy-testnet"}


def test_a_name_whose_latest_was_not_read_keeps_its_test():
    assert stale_integration_tests({"etny-pynithy-testnet": A, "etny-nodenithy-testnet": B},
                                   {"etny-nodenithy-testnet": B}) == set()


def test_several_names_are_judged_apart():
    passed = {"etny-pynithy-testnet": A, "etny-nodenithy-testnet": B}
    latest = {"etny-pynithy-testnet": B, "etny-nodenithy-testnet": B}
    assert stale_integration_tests(passed, latest) == {"etny-pynithy-testnet"}


def test_nothing_passed_is_nothing_stale():
    assert stale_integration_tests({}, {"etny-pynithy-testnet": A}) == set()

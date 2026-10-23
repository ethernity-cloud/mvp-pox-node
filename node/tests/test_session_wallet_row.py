"""utils.session_wallet_attestation: the trustedzone's task wallet attestation
is broadcast as the 'etny-sw' row only when it has the attestation's shape and
names the order it is staged for."""
from utils import session_wallet_attestation

WALLET = "0x31a5e548Ac23Ab95330f6A07D58fA41CfD2a0f58"
SIG = "3065023100ab" + "cd" * 46


def test_an_attestation_for_the_order_is_taken_apart():
    assert session_wallet_attestation(f"v1:2022:{WALLET}:{SIG}\n", 2022) == (WALLET, SIG)


def test_another_orders_attestation_is_refused():
    assert session_wallet_attestation(f"v1:2021:{WALLET}:{SIG}", 2022) is None


def test_other_shapes_are_refused():
    for value in ("", None, f"v2:2022:{WALLET}:{SIG}", f"v1:2022:{WALLET}", f"v1:2022:{WALLET}:{SIG}:x",
                  f"v1:x:{WALLET}:{SIG}", f"v1:2022:{WALLET[:-1]}:{SIG}", f"v1:2022:{WALLET}:zz", f"v1:2022:{WALLET}:abc"):
        assert session_wallet_attestation(value, 2022) is None, value

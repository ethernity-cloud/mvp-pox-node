"""The PoX contract's generation and the contracts a 1.2 PoX names.

A 1.2 PoX answers pricingVersion(); a legacy one (1.0, 1.1) has no such
function, so the call reverts or its answer does not decode. On 1.2 the
contract names its reader, its pricing and its reservation broker (the zero
address for none), and the broker names the ECLD token."""
from eth_abi.exceptions import DecodingError
from web3.exceptions import BadFunctionCallOutput, ContractLogicError

ZERO_ADDRESS = '0x' + '00' * 20


def pox_generation(call):
    """True when `call()` answers (a 1.2 PoX), False when it reverts or its
    answer does not decode (a legacy PoX). Any other failure propagates."""
    try:
        call()
    except (ContractLogicError, BadFunctionCallOutput, DecodingError):
        return False
    return True


def _bind(w3, address, abi):
    """The contract at `address` under `abi`, or None at the zero address."""
    if address.lower() == ZERO_ADDRESS:
        return None
    return w3.eth.contract(address=w3.to_checksum_address(address), abi=abi)


def chain_handles(pox, w3, abis):
    """{reader, pricing, broker, token} of a 1.2 PoX: its readerAddress(),
    pricingAddress() and reservationBrokerAddress() bound under
    abis['reader'], abis['pricing'] and abis['broker'], and the broker's
    token() under abis['erc20']; None for a zero address, and no token
    without a broker."""
    reader = _bind(w3, pox.functions.readerAddress().call(), abis['reader'])
    pricing = _bind(w3, pox.functions.pricingAddress().call(), abis['pricing'])
    broker = _bind(w3, pox.functions.reservationBrokerAddress().call(), abis['broker'])
    token = _bind(w3, broker.functions.token().call(), abis['erc20']) if broker is not None else None
    return {'reader': reader, 'pricing': pricing, 'broker': broker, 'token': token}

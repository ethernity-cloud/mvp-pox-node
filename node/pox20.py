"""The PoX contract's generation and the contracts a 2.0 PoX names.

A 2.0 PoX answers pricingVersion(); a legacy one (1.0, 1.1) has no such
function, so the call reverts or its answer does not decode. On 2.0 the
contract names its reader, its pricing and its reservation broker (the zero
address for none), and the broker names the ECLD token. Its VERSION() names
the form of its adds and of doStamp (EPC_VERSION)."""
from eth_abi.exceptions import DecodingError
from web3.exceptions import BadFunctionCallOutput, ContractLogicError

ZERO_ADDRESS = '0x' + '00' * 20
# What a call raises when the contract has no such function: it reverts, or
# its answer does not decode.
FUNCTION_ABSENT = (ContractLogicError, BadFunctionCallOutput, DecodingError)
# The VERSION() of the PoX whose addDPRequestV2 takes gflops as uint40 (EPC in
# bits 24 to 39), whose addDORequestV2 takes maxGflops as uint48 and whose
# doStamp answers three values, the third the request's minimum EPC
# (pox-smart-contract-litvm Ethernity20.sol VERSION, spec/matching.md section
# 3). A PoX that answers another VERSION() ("1.2", the deployed sets) takes
# the uint32 forms and answers two values.
EPC_VERSION = "2.0"


def pox_generation(call):
    """True when `call()` answers (a 2.0 PoX), False when it reverts or its
    answer does not decode (a legacy PoX). Any other failure propagates."""
    try:
        call()
    except FUNCTION_ABSENT:
        return False
    return True


def pox_version(call):
    """The PoX's VERSION() as `call()` answers it; '' when it reverts or its
    answer does not decode. Any other failure propagates."""
    try:
        return str(call())
    except FUNCTION_ABSENT:
        return ''


def takes_epc_forms(version):
    """Whether a PoX answering VERSION() `version` takes the EPC forms of its
    adds and answers doStamp's third value (EPC_VERSION)."""
    return version == EPC_VERSION


def _bind(w3, address, abi):
    """The contract at `address` under `abi`, or None at the zero address."""
    if address.lower() == ZERO_ADDRESS:
        return None
    return w3.eth.contract(address=w3.to_checksum_address(address), abi=abi)


def chain_handles(pox, w3, abis):
    """{reader, pricing, broker, token} of a 2.0 PoX: its readerAddress(),
    pricingAddress() and reservationBrokerAddress() bound under
    abis['reader'], abis['pricing'] and abis['broker'], and the broker's
    token() under abis['erc20']; None for a zero address, and no token
    without a broker."""
    reader = _bind(w3, pox.functions.readerAddress().call(), abis['reader'])
    pricing = _bind(w3, pox.functions.pricingAddress().call(), abis['pricing'])
    broker = _bind(w3, pox.functions.reservationBrokerAddress().call(), abis['broker'])
    token = _bind(w3, broker.functions.token().call(), abis['erc20']) if broker is not None else None
    return {'reader': reader, 'pricing': pricing, 'broker': broker, 'token': token}

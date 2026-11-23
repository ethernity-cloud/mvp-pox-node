class Request:
    def __init__(self, req):
        self.cpu = req[1]
        self.memory = req[2]
        self.storage = req[3]
        self.bandwidth = req[4]
        self.duration = req[5]
        self.price = req[6]
        self.status = req[7]


class DORequest(Request):
    def __init__(self, req):
        self.downer = req[0]
        super().__init__(req)


class DPRequest(Request):
    def __init__(self, req):
        self.dproc = req[0]
        super().__init__(req)


class DORequestV2:
    """A 2.0 reader's _getDORequestV2 answer: the resources as the legacy
    getter's, then the recorded terms (zero on a legacy request)."""
    def __init__(self, req):
        self.downer = req[0]
        self.cpu = req[1]
        self.memory = req[2]
        self.storage = req[3]
        self.bandwidth = req[4]
        self.duration = req[5]
        self.instances = req[6]
        self.max_price = int(req[7])
        self.reserved_price = int(req[8])
        self.max_gflops = int(req[9])
        self.relays_per_hour = req[10]
        self.gas_level = int(req[11])
        self.chain_fee = int(req[12])
        self.escrow = int(req[13])
        self.status = req[14]


class RequestStatus:
    AVAILABLE = 0
    BOOKED = 1
    CANCELED = 2

    @staticmethod
    def _status_as_string(status) -> str:
        result = [x for x in dir(RequestStatus) if not x.startswith('_')]
        return result[status] if len(result) - 1 > status else status


class Order:
    def __init__(self, req):
        self.downer = req[0]
        self.dproc = req[1]
        self.do_req = req[2]
        self.dp_req = req[3]
        self.status = req[4]


class OrderStatus:
    OPEN = 0
    PROCESSING = 1
    CLOSED = 2
    CANCELLED = 3

class ResultStatus:
    """The task codes of the trustedzones' signed results (MATCHING-DESIGN.md
    3.19; SECURELOCK-HARDENING-SPEC.md 7): field 1 of the result string."""
    SUCCESS = 0
    SYSTEM_ERROR = 1
    KEY_ERROR = 2
    SYNTAX_WARNING = 3
    BASE_EXCEPTION = 4
    PAYLOAD_NOT_DEFINED = 5
    PAYLOAD_CHECKSUM_ERROR = 6
    INPUT_CHECKSUM_ERROR = 7
    EXECVE = 8
    SIGNATURE_ERROR = 20
    CONFIG_ERROR = 32
    EXECUTION_TIMEOUT = 33
    SECURITY_VIOLATION = 35
    ESR_RELAY_TIMEOUT = 37
    SESSION_RELAY_FAULT = 39
    CAS_ATTESTATION_FAULT = 40
    TERMINATED = 43
    TRUSTEDZONE_FAILURE = 44
    PAYLOAD_DELIVERY_FAULT = 45
    INPUT_DELIVERY_FAULT = 46
    CHAIN_UNAVAILABLE = 47
    NETWORK_UNAVAILABLE = 59
    PAYLOAD_CRASHED = 60
    CONTENT_TOO_LARGE = 61
    SECURELOCK_SILENT = 62
    PAYLOAD_LOST = 63

    @staticmethod
    def name_of(code):
        """The name of a task code, 'CODE_<n>' for one the table does not hold."""
        for name, value in vars(ResultStatus).items():
            if name.isupper() and value == int(code):
                return name
        return f"CODE_{int(code)}"

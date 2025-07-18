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
    SUCCESS = 0
    SYSTEM_ERROR = 1
    KEY_ERROR = 2
    SYNTAX_WARNING = 3
    BASE_EXCEPTION = 4
    PAYLOAD_NOT_DEFINED = 5
    PAYLOAD_CHECKSUM_ERROR = 6
    INPUT_CHECKSUM_ERROR = 7
    EXECVE = 8

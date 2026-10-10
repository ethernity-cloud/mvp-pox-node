"""utils.Storage.pin_add_watched against a streamed pin/add response: the pin
is ended with PinAborted when its progress count stops growing for `stall`
seconds, when the watch gives a reason, and completes when Kubo reports the
pin; the response is closed in every case."""
import json
import logging
import threading
import time

import pytest

from utils import PinAborted, Storage

CID = "QmaZvhAZQhcQRi8bqHC4VbL9xnyoZ67RANte12VUogjwe5"


class FakeResponse:
    """Kubo's pin/add body: one JSON line per `messages` entry every `every`
    seconds, then the end of the body, or with `hang` silence until closed;
    reading a closed response raises, as requests does."""

    def __init__(self, messages, every=0.02, hang=False):
        self.messages = messages
        self.every = every
        self.hang = hang
        self.closed = threading.Event()

    def iter_lines(self):
        for message in self.messages:
            if self.closed.wait(self.every):
                raise ConnectionError("response closed")
            yield json.dumps(message).encode()
        if self.hang and self.closed.wait(60):
            raise ConnectionError("response closed")

    def close(self):
        self.closed.set()


def storage_with(response):
    storage = Storage.__new__(Storage)
    storage.logger = logging.getLogger("test")
    storage._api_call = lambda *args, **kwargs: response
    return storage


def test_a_pin_whose_progress_stops_is_ended_as_stalled():
    response = FakeResponse([{"Progress": 1}, {"Progress": 2}] + [{"Progress": 2}] * 200, hang=True)
    started = time.time()
    with pytest.raises(PinAborted, match="no block fetched for 0.3 s"):
        storage_with(response).pin_add_watched(CID, 60, lambda: None, interval=0.05, stall=0.3)
    assert 0.3 <= time.time() - started < 5
    assert response.closed.is_set()


def test_a_pin_that_keeps_fetching_completes():
    response = FakeResponse([{"Progress": n} for n in range(1, 30)] + [{"Pins": [CID]}])
    storage_with(response).pin_add_watched(CID, 60, lambda: None, interval=0.05, stall=0.3)
    assert response.closed.is_set()


def test_the_watch_reason_ends_the_pin():
    response = FakeResponse([{"Progress": n} for n in range(1, 400)], hang=True)
    with pytest.raises(PinAborted, match="the disk fell under 10GB free"):
        storage_with(response).pin_add_watched(CID, 60, lambda: "the disk fell under 10GB free", interval=0.05)
    assert response.closed.is_set()


def test_a_body_that_ends_without_the_pin_raises():
    response = FakeResponse([{"Progress": 1}])
    response.iter_lines = lambda: iter([json.dumps({"Progress": 1}).encode()])
    with pytest.raises(Exception, match="ended without pinning it"):
        storage_with(response).pin_add_watched(CID, 60, lambda: None, interval=0.05, stall=5)

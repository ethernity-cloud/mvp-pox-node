"""utils.holds_within: a condition polled every interval within a window,
with one more check when the window ends (the order-approval wait)."""
from utils import holds_within


class Clock:
    def __init__(self):
        self.t = 0.0
        self.checks = []

    def now(self):
        return self.t

    def sleep(self, s):
        self.t += s


def run(clock, true_from, window=60, interval=30):
    def check():
        clock.checks.append(clock.t)
        return clock.t >= true_from
    return holds_within(check, window, interval, sleep=clock.sleep, now=clock.now)


def test_a_condition_true_at_once_is_checked_once():
    c = Clock()
    assert run(c, true_from=0)
    assert c.checks == [0.0]


def test_a_condition_that_turns_true_between_polls_is_seen_at_the_end_of_the_window():
    # The case of Sepolia order 8: approved 22 s after placement, polled every
    # 30 s for 60 s; the earlier wait checked at 0 and 30 only.
    c = Clock()
    assert run(c, true_from=52)
    assert c.checks == [0.0, 30.0, 60.0]


def test_a_condition_never_true_is_false_after_the_window():
    c = Clock()
    assert not run(c, true_from=1000)
    assert c.checks[-1] == 60.0


def test_the_last_sleep_is_cut_to_the_window():
    c = Clock()
    assert not run(c, true_from=1000, window=60, interval=25)
    assert c.checks == [0.0, 25.0, 50.0, 60.0]


def test_an_exception_counts_as_false():
    c = Clock()
    calls = []

    def check():
        calls.append(c.t)
        if len(calls) == 1:
            raise ConnectionError("rpc")
        return True
    assert holds_within(check, 60, 12, sleep=c.sleep, now=c.now)
    assert calls == [0.0, 12.0]


class Chain:
    """A block height that advances one block every `block_seconds` of the clock."""
    def __init__(self, clock, block_seconds):
        self.clock, self.block_seconds = clock, block_seconds

    def height(self):
        return int(self.clock.t // self.block_seconds)


def run_blocks(clock, chain, true_from, min_blocks=6, max_seconds=900):
    def check():
        clock.checks.append(clock.t)
        return clock.t >= true_from
    return holds_within(check, 60, 5, sleep=clock.sleep, now=clock.now,
                        min_blocks=min_blocks, block=chain.height, max_seconds=max_seconds)


def test_on_a_slow_chain_the_window_waits_for_the_blocks():
    # The case of bloxberg order 1997: blocks every ~40 s, the approval mined
    # about 90 s after placement; a 60 s window alone gave up first.
    c = Clock()
    assert run_blocks(c, Chain(c, 40), true_from=90)


def test_on_a_fast_chain_the_window_is_the_seconds():
    c = Clock()
    assert not run_blocks(c, Chain(c, 5), true_from=1000)
    assert c.checks[-1] == 60.0


def test_a_stalled_chain_ends_at_the_cap():
    c = Clock()
    assert not run_blocks(c, Chain(c, 10_000), true_from=10_000, max_seconds=900)
    assert c.checks[-1] == 900.0


def test_an_unreadable_block_height_falls_back_to_the_seconds():
    c = Clock()

    def broken():
        raise ConnectionError("rpc")
    assert not holds_within(lambda: False, 60, 5, sleep=c.sleep, now=c.now,
                            min_blocks=6, block=broken, max_seconds=300)

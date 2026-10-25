"""utils: an order this node placed that was not approved within the approval
window is recorded on disk beside its placement record, so the node takes it
up once the approval lands instead of leaving it to its client's deadline
(bloxberg testnet orders 2039 and 2053 of 2026-10-24: approved after the
node's window, their clients waited 45 and 85 minutes)."""
from utils import (forget_pending_approval, pending_approval_path, pending_approvals,
                   record_pending_approval)


def test_a_recorded_order_is_read_back_with_its_requests(tmp_path):
    record_pending_approval(str(tmp_path), 2053, 195208, 4290)
    assert pending_approvals(str(tmp_path)) == {2053: {'dp_req': 195208, 'do_req': 4290}}
    assert pending_approval_path(str(tmp_path), 2053) == str(tmp_path / "2053" / "awaiting_approval")


def test_records_come_oldest_order_first(tmp_path):
    record_pending_approval(str(tmp_path), 2060, 7, 70)
    record_pending_approval(str(tmp_path), 2053, 5, 50)
    assert list(pending_approvals(str(tmp_path))) == [2053, 2060]


def test_a_forgotten_order_is_gone_and_forgetting_twice_is_fine(tmp_path):
    record_pending_approval(str(tmp_path), 2053, 5, 50)
    forget_pending_approval(str(tmp_path), 2053)
    forget_pending_approval(str(tmp_path), 2053)
    assert pending_approvals(str(tmp_path)) == {}


def test_other_order_folders_and_broken_records_are_left_out(tmp_path):
    (tmp_path / "2019").mkdir()
    (tmp_path / "2019" / "placed_at").write_text("1792750000")
    (tmp_path / "2040").mkdir()
    (tmp_path / "2040" / "awaiting_approval").write_text("not json")
    (tmp_path / "notes").mkdir()
    record_pending_approval(str(tmp_path), 2053, 5, 50)
    assert pending_approvals(str(tmp_path)) == {2053: {'dp_req': 5, 'do_req': 50}}


def test_a_missing_orders_directory_has_no_records(tmp_path):
    assert pending_approvals(str(tmp_path / "none")) == {}

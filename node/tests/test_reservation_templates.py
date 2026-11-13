"""broker.template_cids and template_pins_due (PHASE-E-PLAN.md F.8, E36): the
mirror pins the artefacts a reservation's template names, every CID token of
its metadata1 to metadata3, for the reservation's term; a reservation whose
recorded end has passed is read again (the end moves on by the term's lost
seconds) and dropped from the record when the fresh end has passed too."""
from broker import TEMPLATE_METADATA, template_cids, template_pins_due

IMAGE = "Qm" + "a" * 44
COMPOSE = "Qm" + "b" * 44
PAYLOAD = "bafkreib" + "c" * 51
CHALLENGE = "bafkreib" + "d" * 51


def template(metadata1, metadata2, metadata3):
    fields = [b"\x00" * 32, 2, 4, 8, 10, 24, 21600, 0, 3, 3, 600, 600, "0x" + "00" * 20, 60, 10**18, False]
    fields += [metadata1, metadata2, metadata3]
    return tuple(fields)


def test_the_metadata_positions():
    assert TEMPLATE_METADATA == (("metadata1", 16), ("metadata2", 17), ("metadata3", 18))


def test_the_cids_of_the_three_metadata_strings_in_order_each_once():
    raw = template(f"etny-pynithy-testnet:{IMAGE}:{COMPOSE}:v3",
                   f"{PAYLOAD}:deadbeef:{CHALLENGE}:0x" + "11" * 20,
                   f"v3s:256:{PAYLOAD}")
    assert template_cids(raw) == [IMAGE, COMPOSE, PAYLOAD, CHALLENGE]


def test_a_mapping_and_tokens_that_are_no_cid():
    raw = {"metadata1": f"name:{IMAGE}", "metadata2": "0xabc:not-a-cid:Qmshort", "metadata3": ""}
    assert template_cids(raw) == [IMAGE]
    assert template_cids(template("", "", "")) == []


def test_the_live_reservations_are_pinned_and_the_ended_dropped():
    now = 1_800_000_000
    ends = {3: now + 1, 1: now, 2: now + 86400, 0: now - 1}
    reads = []

    def read_end(rid):
        reads.append(rid)
        return ends[rid]

    assert template_pins_due(ends, now, read_end) == ([2, 3], [0, 1])
    assert reads == [0, 1]
    assert template_pins_due({}, now, read_end) == ([], [])


def test_a_reservation_whose_end_moved_on_stays_live():
    now = 1_800_000_000
    ends = {0: now - 1, 1: now + 10}
    reads = []

    def read_end(rid):
        reads.append(rid)
        return now + 3600

    assert template_pins_due(ends, now, read_end) == ([0, 1], [])
    assert reads == [0]
    assert ends == {0: now + 3600, 1: now + 10}

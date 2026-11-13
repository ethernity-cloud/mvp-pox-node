"""utils.ResourceRefusals and resource_refusal_holds: a DO request refused for
its resources is held with the four it needs and judged again once this
node's offer serves it (node/<network>/doreq_resources.json)."""
import json

from utils import ResourceRefusals, resource_refusal_holds


def test_a_refusal_holds_while_a_need_exceeds_the_offer():
    assert resource_refusal_holds((1, 1, 10, 1), (1, 1, 8, 1))
    assert resource_refusal_holds((2, 1, 1, 1), (1, 4, 20, 4))
    assert not resource_refusal_holds((1, 1, 10, 1), (1, 1, 10, 1))
    assert not resource_refusal_holds((1, 1, 10, 1), (2, 2, 14, 2))


def test_held_keeps_the_refused_and_drops_the_served(tmp_path):
    path = tmp_path / "doreq_resources.json"
    cache = ResourceRefusals(10, path)
    cache.add(113, (1, 1, 10, 1))
    cache.add(114, (1, 1, 10, 1))
    cache.add(120, (4, 1, 1, 1))
    assert sorted(cache.held((1, 1, 8, 1))) == [113, 114, 120]
    assert sorted(cache.held((1, 1, 14, 1))) == [120]
    assert json.loads(path.read_text()) == {"120": [4, 1, 1, 1]}


def test_the_refusals_are_kept_across_instances(tmp_path):
    path = tmp_path / "doreq_resources.json"
    ResourceRefusals(10, path).add(113, (1, 1, 10, 1))
    assert ResourceRefusals(10, path).held((1, 1, 8, 1)) == [113]


def test_a_file_that_is_not_a_map_starts_empty(tmp_path):
    path = tmp_path / "doreq_resources.json"
    path.write_text("[113]")
    assert ResourceRefusals(10, path).held((1, 1, 8, 1)) == []
    assert json.loads(path.read_text()) == {}

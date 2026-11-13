"""cache_config.CacheConfig.contract_caches: the request and order caches
built against a network's protocol contract, which the agent removes when the
contract changes (etny-node.py __reset_caches_of_another_contract), name the
resource refusals file beside the five caches of 3.8.0."""
from cache_config import CacheConfig


def test_the_contract_caches_name_every_request_and_order_file(tmp_path):
    config = CacheConfig(str(tmp_path))
    assert sorted(path.name for path in config.contract_caches) == sorted([
        "orders_cache.txt", "dpreq_cache.txt", "doreq_cache.txt", "doreq_resources.json",
        "merged_orders_cache.json", "process_order_data.json"])
    assert config.doreq_resources_filepath in config.contract_caches

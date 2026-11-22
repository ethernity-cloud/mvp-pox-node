"""The generation words of --network (config.GENERATION_WORDS;
pox-smart-contract-litvm MAINNET-UPGRADE.md, decision O18): a word that ends
in the generation of its network's PoX, _pox10 or _pox20, selects the network
its name selects. bloxberg_pox10 and polygon_pox10 are the mainnets,
bloxberg_testnet_pox20, polygon_amoy_pox20 and the two *_unsafe_pox20 the
PoX 2.0 testnets; the testnet copies of PoX 1.0 are named by their generation
words; iotex_testnet, ethereum_sepolia, litvm_liteforge and the two -unsafe
twins of the latter have none. A network keeps one name whatever word selects
it: the NetworkConfig, the prefix of its variables and arguments and the
directory of the --handover command are the name's. A word adds no network
the selection names otherwise, by its name or by an earlier word; a name is
selected at each place it is given. Every selection of 3.8.19 selects what it
selected (test_pox10_testnets.py SELECTIONS). networks.ini ends the name of
each network that has a generation word with that word, and the installer
writes the shortnames, the networks' names, as the NETWORK value."""
import configparser
import importlib.util
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

import pytest

import config
from test_pox10_testnets import NODE, REPOSITORY, SERVED_3818, SERVED_ALL, load, load_all, selected

WORDS = {
    "polygon_pox10": "polygon_mainnet",
    "polygon_amoy_pox20": "polygon_amoy",
    "polygon_amoy_unsafe_pox20": "polygon_amoy_unsafe",
    "bloxberg_pox10": "bloxberg_mainnet",
    "bloxberg_testnet_pox20": "bloxberg_testnet",
    "bloxberg_testnet_unsafe_pox20": "bloxberg_testnet_unsafe",
}
# The networks without a generation word.
WORDLESS = ["iotex_testnet", "ethereum_sepolia", "ethereum_sepolia_unsafe", "litvm_liteforge",
            "litvm_liteforge_unsafe"]
# (--network words, the networks selected).
SELECTIONS = [([word], [name]) for word, name in WORDS.items()] + [
    ([word.upper()], [name]) for word, name in WORDS.items()] + [
    (["bloxberg_pox10", "bloxberg_testnet_pox20", "bloxberg_testnet_pox10"],
     ["bloxberg_mainnet", "bloxberg_testnet", "bloxberg_testnet_pox10"]),
    (["polygon_amoy_pox20", "polygon_pox10", "polygon_amoy_pox10"],
     ["polygon_amoy", "polygon_mainnet", "polygon_amoy_pox10"]),
    (["bloxberg_testnet_pox20", "polygon_amoy"], ["bloxberg_testnet", "polygon_amoy"]),
    # A word adds nothing when the selection also gives its network's name,
    # before or after it, or an earlier word added that network.
    (["bloxberg_testnet", "bloxberg_testnet_pox20"], ["bloxberg_testnet"]),
    (["bloxberg_pox10", "bloxberg_mainnet"], ["bloxberg_mainnet"]),
    (["bloxberg_testnet_pox20", "polygon_amoy", "bloxberg_testnet"], ["polygon_amoy", "bloxberg_testnet"]),
    (["polygon_pox10", "polygon_pox10"], ["polygon_mainnet"]),
    (["POLYGON_POX10", "polygon_amoy_pox20", "polygon_pox10"], ["polygon_mainnet", "polygon_amoy"]),
    # A name is selected at each place it is given, as 3.8.19 selects it.
    (["bloxberg_mainnet", "bloxberg_mainnet"], ["bloxberg_mainnet", "bloxberg_mainnet"]),
    (["bloxberg_mainnet", "bloxberg_pox10", "BLOXBERG_MAINNET"], ["bloxberg_mainnet", "bloxberg_mainnet"]),
    (["polygon_amoy_pox10", "POLYGON_AMOY_POX10"], ["polygon_amoy_pox10", "polygon_amoy_pox10"]),
    # An -unsafe network selected by its word joins the network it is named
    # after, selected by either word; alone it runs under its own name.
    (["bloxberg_testnet_pox20", "bloxberg_testnet_unsafe_pox20"], ["bloxberg_testnet"]),
    (["bloxberg_testnet", "bloxberg_testnet_unsafe_pox20"], ["bloxberg_testnet"]),
    (["polygon_amoy_unsafe_pox20", "polygon_amoy_pox20"], ["polygon_amoy"]),
    (["polygon_amoy_unsafe_pox20", "polygon_amoy_pox10"], ["polygon_amoy_unsafe", "polygon_amoy_pox10"]),
    # "all", "auto" and "openbeta" beside a generation word select what they
    # select alone.
    (["all", "bloxberg_testnet_unsafe_pox20"], SERVED_ALL),
    (["auto", "bloxberg_pox10"], SERVED_3818),
    (["openbeta", "polygon_amoy_pox20"], ["polygon_mainnet", "bloxberg_mainnet"]),
]
# Words that name no network, and a legacy name beside a generation word.
REFUSED = [["bloxberg_pox20"], ["polygon_pox20"], ["bloxberg_mainnet_pox10"], ["polygon_mainnet_pox10"],
           ["bloxberg_testnet_pox10_pox10"], ["bloxberg_testnet_pox20_unsafe"], ["iotex_testnet_pox10"],
           ["ethereum_sepolia_pox20"], ["ethereum_sepolia_unsafe_pox20"], ["litvm_liteforge_pox20"],
           ["litvm_liteforge_unsafe_pox20"], ["pox20"], ["bloxberg", "bloxberg_pox10"],
           ["testnet", "bloxberg_testnet_pox20"]]


@pytest.fixture(scope="module")
def agent():
    """etny-node.py as a module, loaded from its path (its name is no
    identifier); its entry point runs under __main__ alone."""
    spec = importlib.util.spec_from_file_location("etny_node", NODE / "etny-node.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_generation_words_and_the_networks_they_select():
    assert config.GENERATION_WORDS == WORDS
    names = config.available_networks()
    for word, name in WORDS.items():
        assert word.endswith(("_pox10", "_pox20")), word
        assert name in names, word
        assert word not in names, word
    assert len(set(WORDS.values())) == len(WORDS)
    # An -unsafe network's word is that of the network it is named after,
    # with _unsafe before the generation.
    for word, name in WORDS.items():
        if config.is_unsafe_network(name):
            generation = word[-len("_pox20"):]
            assert WORDS[word.replace(f"_unsafe{generation}", generation)] == name[:-len("_unsafe")]


def test_every_network_has_a_word_that_ends_in_its_generation_but_the_wordless():
    worded = set(WORDS.values()) | set(config.POX10_TESTNETS)
    assert all(name.endswith("_pox10") for name in config.POX10_TESTNETS)
    assert sorted(set(config.available_networks()) - worded) == sorted(WORDLESS)
    assert not worded & set(WORDLESS)


def test_a_word_gives_the_name_of_its_network():
    for word, name in WORDS.items():
        for given in (word, word.upper(), word.title()):
            assert config.network_name(given) == name
    # A name, a selection word and a word that names no network are given
    # back in lower case.
    for word in config.available_networks() + ["all", "auto", "openbeta", "bloxberg", "testnet", "polygon",
                                               "bloxberg_pox20", "x"]:
        assert config.network_name(word) == word
        assert config.network_name(word.upper()) == word


@pytest.mark.parametrize("words, networks", SELECTIONS, ids=" ".join)
def test_a_selection_selects(monkeypatch, words, networks):
    assert selected(monkeypatch, words) == networks


@pytest.mark.parametrize("words", REFUSED, ids=" ".join)
def test_a_selection_naming_no_network_is_refused(monkeypatch, words):
    with pytest.raises(SystemExit):
        selected(monkeypatch, words)


@pytest.mark.parametrize("word, name", WORDS.items())
def test_a_network_selected_by_its_word_is_the_network_of_its_name(monkeypatch, word, name):
    by_word = load_all(monkeypatch, [word])
    assert list(by_word) == [name]
    assert asdict(by_word[name]) == asdict(load(monkeypatch, name))


def test_the_variables_and_arguments_of_a_network_are_those_of_its_name(monkeypatch):
    """Selected by its word, a network reads <NAME>_<FIELD> and
    --<name>_<field>; the word is no prefix: a variable under it sets
    nothing and the parser holds no argument under it."""
    word, name = "bloxberg_testnet_pox20", "bloxberg_testnet"
    plain = load_all(monkeypatch, [word])[name]
    assert load_all(monkeypatch, [word], {"BLOXBERG_TESTNET_RPC_DELAY": "70"})[name].rpc_delay == 70
    assert load_all(monkeypatch, [word], arguments=("--bloxberg_testnet_rpc_delay", "50"))[name].rpc_delay == 50
    assert load_all(monkeypatch, [word], {"BLOXBERG_TESTNET_POX20_RPC_DELAY": "70"})[name] == plain
    with pytest.raises(SystemExit):
        load_all(monkeypatch, [word], arguments=("--bloxberg_testnet_pox20_rpc_delay", "50"))
    options = [option for action in config.parse_arguments(list(config.NETWORKS))._actions
               for option in action.option_strings]
    assert "--bloxberg_testnet_rpc_delay" in options
    assert not [option for option in options if "pox20" in option]
    assert sorted({option.split("_pox10_")[0] for option in options if "pox10" in option}) == [
        "--bloxberg_testnet", "--polygon_amoy"]


def test_a_refusal_lists_the_names_and_the_generation_words(monkeypatch, capsys):
    with pytest.raises(SystemExit):
        selected(monkeypatch, ["bloxberg_pox20"])
    message = capsys.readouterr().err
    assert "Invalid network(s) specified: bloxberg_pox20." in message
    assert f"Available networks are: {', '.join(config.available_networks())}." in message
    assert ("Generation words: polygon_pox10 (polygon_mainnet), polygon_amoy_pox20 (polygon_amoy), "
            "polygon_amoy_unsafe_pox20 (polygon_amoy_unsafe), bloxberg_pox10 (bloxberg_mainnet), "
            "bloxberg_testnet_pox20 (bloxberg_testnet), bloxberg_testnet_unsafe_pox20 (bloxberg_testnet_unsafe).") \
        in message


def test_the_refusal_of_a_family_without_a_generation_word_lists_none(monkeypatch, capsys):
    assert config.generation_words_text(["iotex_testnet"]) == "none"
    assert config.generation_words_text(["bloxberg_testnet", "x"]) == "bloxberg_testnet_pox20 (bloxberg_testnet)"
    with pytest.raises(SystemExit):
        load_all(monkeypatch, ["iotex_mainnet"])
    assert "Available networks are: iotex_testnet. Generation words: none." in capsys.readouterr().err


def test_the_help_of_network_names_each_generation_word_with_its_network():
    (action,) = [action for action in config.parse_arguments(list(config.NETWORKS))._actions
                 if action.dest == "network"]
    for word, name in WORDS.items():
        assert f"{word} ({name})" in action.help
    assert action.default == ["all"]


def handover(agent, monkeypatch, tmp_path, network, answer):
    """(the exit code of `--handover --network <network>`, the directories
    CacheConfig was asked for): the agent's answer is `answer`."""
    asked = []

    def cache_config(directory):
        asked.append(directory)
        return SimpleNamespace(base_path=tmp_path)

    monkeypatch.setattr(agent, "CacheConfig", cache_config)
    monkeypatch.setattr(agent.broker, "await_handover_answer", lambda path: answer)
    return agent.handover_command(["--handover", "--network", network]), asked


@pytest.mark.parametrize("network", ["bloxberg_testnet_pox20", "BLOXBERG_TESTNET_POX20", "bloxberg_testnet",
                                     "BLOXBERG_TESTNET"])
def test_the_handover_command_writes_into_the_directory_of_the_networks_name(agent, monkeypatch, tmp_path, capsys,
                                                                             network):
    answer = {"announced": True, "fromBlock": 10, "toBlock": 20, "slot": 3, "tx": "0xab"}
    assert handover(agent, monkeypatch, tmp_path, network, answer) == (0, ["bloxberg_testnet"])
    assert Path(agent.broker.handover_request_path(tmp_path)).exists()
    assert "handover requested for the order running on bloxberg_testnet;" in capsys.readouterr().out


@pytest.mark.parametrize("network", ["bloxberg_pox20", "ethereum_sepolia_pox20", "testnet"])
def test_the_handover_command_refuses_a_word_that_names_no_network(agent, monkeypatch, tmp_path, capsys, network):
    assert handover(agent, monkeypatch, tmp_path, network, None) == (2, [])
    message = capsys.readouterr().out
    assert f"unknown network {network!r}; one of: {', '.join(sorted(config.available_networks()))}; " in message
    assert "generation words: polygon_pox10 (polygon_mainnet), " in message
    assert "bloxberg_testnet_unsafe_pox20 (bloxberg_testnet_unsafe)" in message


def test_the_installers_list_ends_a_networks_name_with_its_generation_word():
    listed = configparser.ConfigParser(interpolation=None)
    listed.read(REPOSITORY / "networks.ini", encoding="utf-8")
    word_of = {name: word for word, name in WORDS.items()}
    word_of.update({name: name for name in config.POX10_TESTNETS})
    generations = {"_pox10": "PoX 1.0", "_pox20": "PoX 2.0"}
    for section in listed.sections():
        entry = listed[section]
        # The shortname is the network's name: the prefix of the lines the
        # installer writes into the node's config, and its NETWORK word.
        name = entry["shortname"].lower()
        assert name in config.available_networks(), section
        if name in WORDLESS:
            assert "pox" not in entry["name"].lower(), section
            continue
        word = word_of[name]
        assert entry["name"].endswith(f", {word}"), section
        assert generations[word[-len("_pox10"):]] in entry["name"], section
        # The installer cuts a line at ';' or '#' and passes it through
        # xargs, which refuses an apostrophe.
        assert not set(entry["name"]) & set(";#'\""), section


def test_the_installer_shows_the_names_and_writes_the_shortnames():
    installer = (REPOSITORY / "etny-node-installer.sh").read_text(encoding="utf-8")
    assert 'echo "$key. ${networks[$key]}"' in installer
    assert 'networks["$current_section"]="$value"' in installer
    assert 'selected_shortnames+=("${shortnames[$choice]}")' in installer
    assert 'export NETWORK="${selected_shortnames[*]}"' in installer

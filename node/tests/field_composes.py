"""The composes a node runs, for the tests of the store credential's mount
and users. store_composes/ holds byte copies (MANIFEST.sha256) of
etny-nodenithy v3/run/docker-compose-final.yml (main 89d7dd4), etny-pynithy
v3/run/docker-compose-final.yml (master 2852199) and ethernity-cloud-sdk-js
nodenithy/ and pynithy/run/docker-compose-final.yml.tmpl (main 83ce0e6);
trustedzone_published and sdk_js_published untag a copy as its publication
does. sdk_py_data is the dictionary ethernity-cloud-sdk-py
commands/pynithy/publish.py (main 8dc1e2a) dumps with yaml.safe_dump."""
import re
from pathlib import Path

COMPOSES = Path(__file__).parent / "store_composes"
NODENITHY_TRUSTEDZONE = "etny-nodenithy.docker-compose-final.yml"
PYNITHY_TRUSTEDZONE = "etny-pynithy.docker-compose-final.yml"
SDK_JS_NODENITHY = "sdk-js-nodenithy.docker-compose-final.yml.tmpl"
SDK_JS_PYNITHY = "sdk-js-pynithy.docker-compose-final.yml.tmpl"
COPIES = [NODENITHY_TRUSTEDZONE, PYNITHY_TRUSTEDZONE, SDK_JS_NODENITHY, SDK_JS_PYNITHY]


def copy_of(name):
    return (COMPOSES / name).read_bytes().decode("utf-8")


def trustedzone_published(text, cas):
    """A trustedzone repository's compose as .gitlab/run.sh publishes it: a
    network without a CAS deletes the lines tagged __CAS_ONLY__ (sed
    '/# __CAS_ONLY__/d'), any other drops the tag (sed
    's/[[:space:]]*# __CAS_ONLY__//')."""
    lines = text.split("\n")
    if not cas:
        return "\n".join(line for line in lines if "# __CAS_ONLY__" not in line)
    return "\n".join(re.sub(r"\s*# __CAS_ONLY__", "", line, count=1) for line in lines)


def sdk_js_published(text, cas, unsafe):
    """The JS SDK's compose as cas/config.js renderCompose publishes it for
    a network a CAS provisions (`cas`) or not, with its LAS or, on an
    -unsafe network (`unsafe`), without."""
    lines = [line for line in text.split("\n")
             if not (re.search(r"# __CAS_ONLY__$", line) and not cas)
             and not (re.search(r"# __NO_CAS_ONLY__$", line) and cas)
             and not (re.search(r"# __SAFE_ONLY__$", line) and unsafe)]
    return "\n".join(re.sub(r"\s*# __(?:CAS|NO_CAS|SAFE)_ONLY__$", "", line) for line in lines)


def sdk_py_data(cas, unsafe):
    """The dictionary generate_compose_data(is_final=True) of the Python
    SDK's publish.py builds for a network a CAS provisions (`cas`) or a
    testnet whose enclaves self-sign, with its LAS or, on an -unsafe network
    (`unsafe`), without."""
    data = {
        "version": "3.2",
        "services": {
            "las": {
                "container_name": "las",
                "privileged": True,
                "image": "localhost:5000/etny-las",
                "restart": "unless-stopped",
                "ports": [{"target": 18766, "published": 18766, "protocol": "tcp", "mode": "host"}],
                "healthcheck": {
                    "test": ["CMD", "bash", "-c", "echo > /dev/tcp/127.0.0.1/18766"],
                    "interval": "5s",
                    "timeout": "3s",
                    "retries": 5,
                },
            },
            "etny-securelock": {
                "container_name": "etny-securelock",
                "privileged": True,
                "image": "localhost:5000/etny-securelock",
                "entrypoint": "",
                "command": ["/usr/local/bin/python", "/etny-securelock/securelock.py"],
                "restart": "on-failure",
                "depends_on": ["las"],
            },
            "etny-trustedzone": {
                "container_name": "etny-trustedzone",
                "privileged": True,
                "image": "localhost:5000/etny-trustedzone",
                "entrypoint": "",
                "command": ["/usr/local/bin/python", "/etny-trustedzone/trustedzone.py"],
                "restart": "on-failure",
                "depends_on": ["las"],
            },
        },
    }
    data["services"]["las"]["entrypoint"] = "/las_entrypoint.sh"
    data["services"]["las"]["command"] = ("bash -c '/las_entrypoint.sh && /usr/local/bin/las -a 0.0.0.0 "
                                          "| tee /var/log/las.log'")
    data["networks"] = {"ethernity": {"external": True}}
    for service in data["services"].values():
        service["networks"] = ["ethernity"]
    if cas:
        securelock_env = {"SCONE_CAS_ADDR": "scone-cas.cf", "SCONE_LAS_ADDR": "las",
                          "SCONE_CONFIG_ID": "securelock-session/application", "SCONE_HEAP": "1G",
                          "SCONE_QUEUES": "${ETNY_SCONE_QUEUES:-4}", "SCONE_LOG": "FATAL", "SCONE_STACK": "4M",
                          "SCONE_ALLOW_DLOPEN": "1", "SCONE_EXTENSIONS_PATH": "/lib/libbinary-fs.so"}
        trustedzone_env = {"SCONE_CAS_ADDR": "scone-cas.cf", "SCONE_LAS_ADDR": "las",
                           "SCONE_CONFIG_ID": "trustedzone-session/application", "SCONE_HEAP": "256M",
                           "SCONE_QUEUES": "${ETNY_SCONE_QUEUES:-4}", "SCONE_LOG": "FATAL",
                           "SCONE_ALLOW_DLOPEN": "1", "SCONE_EXTENSIONS_PATH": "/lib/libbinary-fs.so"}
    else:
        securelock_env = {"SCONE_HEAP": "1G", "SCONE_QUEUES": "${ETNY_SCONE_QUEUES:-4}", "SCONE_ALLOW_DLOPEN": "1",
                          "SCONE_EXTENSIONS_PATH": "/lib/libbinary-fs.so", "SCONE_ALPINE": "1", "SCONE_DEBUG": "0",
                          "SCONE_LOG": "FATAL"}
        trustedzone_env = {"SCONE_HEAP": "256M", "SCONE_QUEUES": "${ETNY_SCONE_QUEUES:-4}",
                           "SCONE_ALLOW_DLOPEN": "1", "SCONE_EXTENSIONS_PATH": "/lib/libbinary-fs.so",
                           "SCONE_ALPINE": "1", "SCONE_DEBUG": "0", "SCONE_LOG": "FATAL"}
    data["services"]["etny-securelock"]["environment"] = [f"{k}={v}" for k, v in securelock_env.items()]
    data["services"]["etny-trustedzone"]["environment"] = [f"{k}={v}" for k, v in trustedzone_env.items()]
    if unsafe:
        del data["services"]["las"]
        for service in ("etny-securelock", "etny-trustedzone"):
            data["services"][service].pop("depends_on", None)
    return data

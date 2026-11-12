import os
import sys
import types

# config runs dependency_manager.check_dependencies() when imported, which
# pip-installs the agent's pinned packages; a test takes the environment as
# it is.
sys.modules.setdefault("dependency_manager", types.SimpleNamespace(check_dependencies=lambda: None))

# The agent's modules are imported by their names from node/, as etny-node.py imports them.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

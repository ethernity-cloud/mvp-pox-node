import os
import sys

# The agent's modules are imported by their names from node/, as etny-node.py imports them.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

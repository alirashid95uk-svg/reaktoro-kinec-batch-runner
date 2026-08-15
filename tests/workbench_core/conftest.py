"""Keep GUI/workbench dependency tests out of the verified solver environment."""

from importlib.util import find_spec


collect_ignore_glob = ["test_*.py"] if find_spec("ruamel") is None else []

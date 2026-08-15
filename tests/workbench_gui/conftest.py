import os
from importlib.util import find_spec

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# The authoritative solver suite remains runnable without adding pytest-qt to it.
collect_ignore_glob = ["test_*.py"] if find_spec("pytestqt") is None else []

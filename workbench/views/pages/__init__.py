"""Stable public API for the seven permanent Workbench pages."""

from .cases import CasesPage
from .compare import ComparePage
from .environment import EnvironmentPage
from .explore import ExplorePage
from .queue import QueuePage
from .runs import RunsPage
from .studies import StudiesPage

__all__ = [
    "CasesPage",
    "ComparePage",
    "EnvironmentPage",
    "ExplorePage",
    "QueuePage",
    "RunsPage",
    "StudiesPage",
]

"""Standalone Design of Experiments support for the Reaktoro batch runner.

This package owns DoE specification, sampling, candidate materialisation,
identity, packaging, and launch handoff. It deliberately has no dependency on
Workbench or ``workbench_core``.
"""

from .launch import launch_all, launch_sample
from .models import ExistingCasesDesignSpec, GeneratedDesignSpec, load_design_spec
from .package import generate_design, load_manifest, read_ledger

__all__ = [
    "ExistingCasesDesignSpec",
    "GeneratedDesignSpec",
    "generate_design",
    "launch_all",
    "launch_sample",
    "load_design_spec",
    "load_manifest",
    "read_ledger",
]

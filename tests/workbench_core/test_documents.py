from pathlib import Path

import pytest

from workbench_core.documents import (
    CaseDocument,
    CaseDocumentError,
    ExternalModificationError,
    MergeConflictError,
    NamedListItem,
    TemplateSentinelError,
)


CASE_YAML = """\
case:
  name: "demo"  # retained comment
paths:
  output_dir: outputs/demo
minerals:
  - name: Calcite
    role: kinetic
"""


def test_round_trip_patch_undo_redo_and_conflict_safe_save(tmp_path: Path) -> None:
    document = CaseDocument.from_text(CASE_YAML)
    document.patch(("minerals", NamedListItem("Calcite"), "role"), "equilibrium")
    assert 'name: "demo"  # retained comment' in document.to_text()
    assert "role: equilibrium" in document.to_text()
    assert document.undo()
    assert "role: kinetic" in document.to_text()
    assert document.redo()
    assert "role: equilibrium" in document.to_text()

    path = tmp_path / "case.yaml"
    revision = document.save(path)
    assert revision.sha256 == __import__("hashlib").sha256(path.read_bytes()).hexdigest()
    loaded = CaseDocument.load(path)
    loaded.patch(("case", "name"), "changed")
    path.write_text(CASE_YAML, encoding="utf-8")
    with pytest.raises(ExternalModificationError):
        loaded.save()


def test_document_rejects_duplicate_keys_conflicts_and_runnable_sentinels() -> None:
    with pytest.raises(CaseDocumentError, match="duplicate key"):
        CaseDocument.from_text("case: 1\ncase: 2\n")
    with pytest.raises(MergeConflictError):
        CaseDocument.from_text("case: x\n<<<<<<< HEAD\n")

    template = CaseDocument.from_text("case:\n  name: REQUIRED\nphysical:\n  pressure: TBD_SOURCE_REQUIRED\n")
    assert template.sentinel_paths() == (("case", "name"), ("physical", "pressure"))
    with pytest.raises(TemplateSentinelError):
        template.assert_runnable()

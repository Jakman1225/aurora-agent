from __future__ import annotations

import tomllib
from pathlib import Path


PACKAGE_ROOT = Path(__file__).resolve().parents[1]


def test_apache_2_license_metadata_and_files_are_present() -> None:
    pyproject = tomllib.loads((PACKAGE_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    project = pyproject["project"]

    assert project["license"] == "Apache-2.0"
    assert project["license-files"] == ["LICENSE", "NOTICE"]

    license_text = (PACKAGE_ROOT / "LICENSE").read_text(encoding="utf-8")
    notice_text = (PACKAGE_ROOT / "NOTICE").read_text(encoding="utf-8")

    assert "Apache License" in license_text
    assert "Version 2.0, January 2004" in license_text
    assert "aurora-agent" in notice_text
    assert "Copyright 2026 AURORA" in notice_text


def test_public_distribution_approval_record_matches_release() -> None:
    approval = (PACKAGE_ROOT / "PYPI_DISTRIBUTION_APPROVAL_REQUIRED.txt").read_text(
        encoding="utf-8"
    )

    assert "blocker recorded by this file has been satisfied" in approval
    assert "Approved version: 0.3.0" in approval
    assert "SPDX: Apache-2.0" in approval
    assert "AURORA backend" in approval
    assert "JAKROW repository content outside aurora-agent/" in approval
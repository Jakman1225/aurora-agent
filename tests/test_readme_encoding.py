from pathlib import Path


PACKAGE_ROOT = Path(__file__).resolve().parents[1]


def test_readme_is_utf8_and_preserves_em_dashes() -> None:
    readme = (PACKAGE_ROOT / "README.md").read_bytes().decode("utf-8")

    broken_markers = ("??", "\ufffd", "\u00e2\u20ac\u201d", "\u00e2\u20ac\u201c")
    assert not any(marker in readme for marker in broken_markers)

    dash = "\u2014"
    expected_phrases = (
        f"**Local action evidence** {dash} proposal",
        f"**AURORA ingestion** {dash} incremental runtime events",
        f"**AI Output evidence** {dash} first-class AuroraSeal v3 records",
        f"**AI Decision evidence** {dash} first-class decision records",
        f"**Human Approval evidence** {dash} policy-bound approval requirements",
        f"**Amendment lifecycle evidence** {dash} immutable corrections",
        f"[CH-01 {dash} Independently verify the timestamp token]",
        f"[CH-02 {dash} Change one bit and reproduce verification failure]",
    )
    assert all(phrase in readme for phrase in expected_phrases)
from datetime import datetime
from pathlib import Path

from scribe import sessions


def test_slugify_keeps_accents_and_drops_separators() -> None:
    assert sessions.slugify("2024-11-24 Estar en casa a través de los demás ") == (
        "2024-11-24 Estar en casa a través de los demás"
    )
    assert sessions.slugify("a/b:c\nd") == "a-b-c-d"
    assert sessions.slugify("   ") == "session"
    assert len(sessions.slugify("x" * 200)) == 60


def test_slugify_normalizes_decomposed_unicode() -> None:
    decomposed = "demás"  # macOS filenames are often NFD
    assert sessions.slugify(decomposed) == "demás"


def test_create_names_are_timestamped_and_unique(tmp_path: Path) -> None:
    now = datetime(2026, 11, 12, 9, 30, 12)
    a = sessions.create(tmp_path, "dictation", kind="dictation", now=now)
    b = sessions.create(tmp_path, "dictation", kind="dictation", now=now)
    assert a.name == "2026-11-12_09-30-12_dictation"
    assert b.name == "2026-11-12_09-30-12_dictation-2"
    meta = a.meta()
    assert meta["kind"] == "dictation"
    assert meta["created"] == "2026-11-12T09:30:12"
    assert meta["runs"] == []


def test_find_accepts_folder_or_file_inside(tmp_path: Path) -> None:
    session = sessions.create(tmp_path, "x", kind="text")
    session.transcript.write_text("hi")
    assert sessions.find(session.path) == session
    assert sessions.find(session.transcript) == session
    assert sessions.find(tmp_path) is None


def test_refined_paths_never_collide_across_runs(tmp_path: Path) -> None:
    session = sessions.create(tmp_path, "x", kind="text")
    first = session.refined_path("claude", datetime(2026, 1, 1, 10, 0, 0))
    second = session.refined_path("claude", datetime(2026, 1, 1, 10, 0, 1))
    assert first.name == "refined.claude.2026-01-01_10-00-00.txt"
    assert first != second


def test_create_is_safe_when_processes_race(tmp_path: Path) -> None:
    """Regression: two `scribe dictate` started in the same millisecond both picked the same
    name (exists() then mkdir()) and the loser crashed with FileExistsError."""
    import threading

    now = datetime(2026, 11, 12, 12, 20, 16)
    barrier = threading.Barrier(8)
    created: list[sessions.Session] = []
    errors: list[BaseException] = []

    def worker() -> None:
        barrier.wait()
        try:
            created.append(sessions.create(tmp_path, "dictation", kind="dictation", now=now))
        except BaseException as e:
            errors.append(e)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert len({s.path for s in created}) == 8

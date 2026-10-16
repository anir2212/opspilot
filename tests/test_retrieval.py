from pathlib import Path
from types import SimpleNamespace

from opspilot.retrieval import chunk_markdown, reciprocal_rank_fusion


def test_long_section_has_overlap_and_keeps_heading(tmp_path: Path) -> None:
    path = tmp_path / "RB-99.md"
    path.write_text("# Root\n## Diagnosis\n" + " ".join(f"word{i}" for i in range(30)))
    chunks = chunk_markdown(path, max_words=20, overlap_words=5)
    assert len(chunks) == 2
    assert chunks[0].heading == chunks[1].heading == "Diagnosis"
    assert chunks[0].body.split()[-5:] == chunks[1].body.split()[:5]
    assert all(chunk.source_id == "RB-99" for chunk in chunks)


def test_rrf_rewards_documents_found_by_both_searches() -> None:
    a, b, c = (SimpleNamespace(id=i) for i in (1, 2, 3))
    ranked = reciprocal_rank_fusion([a, b], [c, b])
    assert [row.id for row in ranked] == [2, 1, 3]

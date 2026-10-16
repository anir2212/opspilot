"""Run the repeatable knowledge-ingestion step against local Postgres."""

from pathlib import Path

from opspilot.retrieval import ingest
from opspilot.storage import init_database, make_engine, session_factory

ROOT = Path(__file__).resolve().parents[1]

if __name__ == "__main__":
    engine = make_engine()
    init_database(engine)
    with session_factory(engine)() as session:
        count = ingest(session, ROOT / "knowledge" / "runbooks")
    print(f"Indexed {count} knowledge chunks using ONNX BAAI/bge-small-en-v1.5")

import sys
from pathlib import Path
from urllib.parse import urlparse

import chromadb

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "scripts"))
import ingest_dataset  # noqa: E402

from app.config import get_settings


async def test_ingest_dataset_populates_both_collections_and_is_idempotent():
    await ingest_dataset.main()

    settings = get_settings()
    parsed = urlparse(settings.chroma_url)
    client = chromadb.HttpClient(host=parsed.hostname, port=parsed.port)

    employees_count_1 = client.get_collection("employees").count()
    helpdesk_count_1 = client.get_collection("helpdesk").count()
    assert employees_count_1 > 0
    assert helpdesk_count_1 > 0

    # Re-run: idempotent, no duplication -- document counts must not change.
    await ingest_dataset.main()
    employees_count_2 = client.get_collection("employees").count()
    helpdesk_count_2 = client.get_collection("helpdesk").count()

    assert employees_count_2 == employees_count_1
    assert helpdesk_count_2 == helpdesk_count_1

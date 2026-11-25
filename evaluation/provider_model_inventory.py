"""Read model IDs from the configured provider; never serialize credentials."""

import asyncio
import json
from pathlib import Path

from backend.config import Settings
from backend.services.groq import GroqService


async def main():
    service = GroqService(Settings())
    slot, key = service._credentials_or_raise()[0]
    client = service._client_for_credential(slot, key)
    models = await client.models.list()
    result = [{"id": model.id} for model in models.data]
    Path("docs/reports/artifacts/provider_model_inventory_20261101.json").write_text(
        json.dumps(result, indent=2), encoding="utf-8"
    )
    print(json.dumps(result))


if __name__ == "__main__":
    asyncio.run(main())

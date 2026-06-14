import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import get_settings  # noqa: E402
from app.services.coze_client import CozeClient  # noqa: E402


CASES = [
    ("sales_talk_qa", "\u5230\u5e97\u4f1a\u4e71\u6536\u8d39\u5417"),
    ("sales_talk_qa", "\u4f60\u4eec\u795b\u6591\u7528\u4ec0\u4e48\u65b9\u6cd5"),
    ("case_studies", "\u5ba2\u6237\u505a\u5b8c\u4e4b\u540e\u7684\u6548\u679c\u6211\u60f3\u770b\u4e00\u4e0b"),
    ("project_qa", "\u6591\u70b9 \u80a4\u8272\u4e0d\u5747 \u6539\u5584\u65b9\u5411"),
]


async def main() -> None:
    client = CozeClient(get_settings())
    output = []
    for kb_name, query in CASES:
        try:
            result = await client.search_kb(kb_name, query)
            output.append(
                {
                    "kb_name": kb_name,
                    "query": query,
                    "count": len(result.items),
                    "first_document_id": result.items[0].document_id if result.items else "",
                    "first_content_len": len(result.items[0].content) if result.items else 0,
                }
            )
        except Exception as exc:
            output.append({"kb_name": kb_name, "query": query, "error": f"{type(exc).__name__}: {exc}"})
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())

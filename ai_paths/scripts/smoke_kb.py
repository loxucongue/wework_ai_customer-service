import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import get_settings  # noqa: E402
from app.services.coze_client import CozeClient  # noqa: E402


CASES = [
    ("project_qa", "\u76ae\u79d2 \u70b9\u72b6\u6591 \u6de1\u6591"),
    ("after_sales_qa", "\u76ae\u79d2\u540e\u7ed3\u75c2 \u4e0d\u80fd\u62a0\u75c2"),
    ("competitor_qa", "\u522b\u5bb6\u6c34\u5149199"),
    ("trust_assets", "\u6b63\u89c4 \u8d44\u8d28"),
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

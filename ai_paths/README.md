# AI Paths Backend

Local FastAPI + LangGraph service for the enterprise WeChat medical-aesthetic customer service workflow.

Coze is kept as a tool layer for the first release:

- unified knowledge-base search
- pricing database CRUD
- pricing knowledge-base sync
- later business APIs such as appointment and store lookup

## Local Development

Create `.env` in `ai_paths/` or export environment variables:

```env
ALIYUN_DASHSCOPE_API_KEY=
VOLCENGINE_ARK_API_KEY=
COZE_API_BASE=https://api.coze.cn
COZE_OAUTH_CLIENT_ID=
COZE_OAUTH_PUBLIC_KEY_ID=
COZE_OAUTH_PRIVATE_KEY_FILE=<path-to-private-key.pem>
COZE_OAUTH_TOKEN_TTL=7200
```

Install dependencies:

```bash
pip install -r requirements.txt
```

Run:

```bash
uvicorn app.main:app --reload --app-dir ai_paths
```

Health check:

```bash
curl http://127.0.0.1:8000/health
```

Chat:

```bash
curl -X POST http://127.0.0.1:8000/chat \
  -H "Content-Type: application/json" \
  -d "{\"content\":\"project price test\",\"customer_id\":\"test\",\"corp_id\":\"test\"}"
```

## Windows Encoding Note

PowerShell may corrupt inline Chinese literals into `???` when piping scripts or JSON.
For local smoke tests, prefer the checked-in scripts:

```bash
python ai_paths/scripts/smoke_chat.py
python ai_paths/scripts/smoke_kb.py
```

If an inline Python script is necessary, use Unicode escapes such as `"\u76ae\u79d2"` instead of raw Chinese text.

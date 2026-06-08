from pathlib import Path

from cozepy import JWTAuth

from app.config import Settings


class CozeOAuthTokenProvider:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._auth: JWTAuth | None = None

    def get_access_token(self) -> str:
        auth = self._get_auth()
        return auth.token

    def _get_auth(self) -> JWTAuth:
        if self._auth is not None:
            return self._auth

        client_id = self.settings.coze_oauth_client_id
        public_key_id = self.settings.coze_oauth_public_key_id
        private_key_file = self.settings.coze_oauth_private_key_file

        if not client_id:
            raise RuntimeError("Missing COZE_OAUTH_CLIENT_ID")
        if not public_key_id:
            raise RuntimeError("Missing COZE_OAUTH_PUBLIC_KEY_ID")
        if private_key_file is None:
            raise RuntimeError("Missing COZE_OAUTH_PRIVATE_KEY_FILE")

        path = Path(private_key_file)
        if not path.is_absolute() and not path.exists():
            repo_root = Path(__file__).resolve().parents[3]
            candidate = repo_root / path
            if candidate.exists():
                path = candidate
        if not path.exists():
            raise RuntimeError("COZE_OAUTH_PRIVATE_KEY_FILE does not exist")

        private_key = path.read_text(encoding="utf-8")
        self._auth = JWTAuth(
            client_id=client_id,
            private_key=private_key,
            public_key_id=public_key_id,
            ttl=self.settings.coze_oauth_token_ttl,
            base_url=self.settings.coze_api_base.rstrip("/"),
        )
        return self._auth

"""SuperGrok / X Premium+ device login, then an API key if inference returns 403.

Public Grok CLI client. No client secret. Login is auth.x.ai. Inference is api.x.ai/v1.
The device grant is RFC 8628 on that public client. The grok CLI browser login is PKCE on the same client.
"""

from __future__ import annotations

from collections.abc import Callable

from ghost_desk.client import ClientError, OpenAIChatClient
from ghost_desk.config import Config
from ghost_desk.oauth import OAuthSpec, device_login, save_tokens
from ghost_desk.providers.base import Provider, TierBlocked, chosen_base

# Published with the public Grok CLI client. Not a secret.
SPEC = OAuthSpec(
    provider="xai",
    client_id="b1a00492-073a-47ea-816f-4c329264a828",
    scope="openid profile email offline_access grok-cli:access api:access",
    device_url="https://auth.x.ai/oauth2/device/code",
    token_url="https://auth.x.ai/oauth2/token",
)
INFERENCE = "https://api.x.ai/v1"
DEFAULT_MODEL = "grok-4.6"
NOTE = "If login works but inference returns 403, this tier is blocked. Use an xAI API key."


class XAI(Provider):
    def __init__(self, config, http=None, browser=None, sdk=None, sleep=None):
        super().__init__(
            id="xai",
            auth_mode=config.auth_mode or "oauth",
            model=config.model or DEFAULT_MODEL,
            base_url=INFERENCE,
        )
        self._config = config
        self._http = http
        self._browser = browser
        self._sdk = sdk
        self._sleep = sleep

    def authenticate(self, output: Callable[[str], None]) -> None:
        tokens = device_login(
            SPEC,
            output=output,
            http=self._http,
            browser=self._browser,
            sleep=self._sleep,
        )
        save_tokens(self._config.data_path() / "auth.json", tokens, provider=self._config.provider or "xai-oauth")
        output("Grok subscription saved on this machine.")

    def _runtime(self) -> Config:
        key = self.access_token or self._config.api_key
        base = INFERENCE if self.auth_mode == "oauth" else chosen_base(self._config, INFERENCE)
        return Config(
            api_key=key,
            base_url=base,
            model=self.model,
            working_directory=self._config.working_directory,
            data_dir=self._config.data_dir,
            provider="xai",
            auth_mode=self.auth_mode,
        )

    def chat(self, messages, tools=None, *, model=None, stream=False, on_text=None):
        try:
            client = OpenAIChatClient(self._runtime(), sdk=self._sdk, attempts=2)
            return client.complete(messages, tools, model=model or self.model, stream=stream, on_text=on_text)
        except ClientError as exc:
            if self.auth_mode == "oauth" and "403" in str(exc):
                raise TierBlocked(
                    "This subscription tier is blocked from inference. Run ghost setup and choose API key."
                ) from exc
            raise

    def list_models(self) -> list[str]:
        return [self.model] if self.model else []

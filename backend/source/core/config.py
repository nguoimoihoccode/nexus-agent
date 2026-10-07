"""Application settings and model factory."""

from typing import Literal, Self
from urllib.parse import quote, urlsplit

from langchain.chat_models import init_chat_model
from langchain_core.callbacks import BaseCallbackHandler
from pydantic import Field, PositiveInt, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from source.core.observability import configure_logging


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    nexus_env: Literal["development", "test", "production"] = "development"
    model: str = "deepseek:deepseek-v4-pro"
    deepseek_api_key: SecretStr
    tavily_api_key: SecretStr | None = None
    ai_trader_api_base_url: str | None = None
    ai_trader_token: SecretStr | None = None
    ai_trader_timeout_seconds: float = 20.0
    nexus_oidc_issuer: str | None = None
    nexus_oidc_audience: str | None = None
    nexus_oidc_jwks_uri: str | None = None
    nexus_oidc_roles_claim: str = "roles"
    nexus_oidc_allowed_algorithms: list[str] = Field(default_factory=lambda: ["RS256"])
    nexus_oidc_client_id: str | None = None
    nexus_oidc_client_secret: SecretStr | None = None
    nexus_oidc_scope: str = "openid profile"
    nexus_app_origin: str | None = None
    nexus_browser_session_auth: Literal["disabled", "dual", "required"] = "disabled"
    nexus_browser_session_key: SecretStr | None = None
    nexus_browser_session_idle_seconds: PositiveInt = 3600
    nexus_browser_session_absolute_seconds: PositiveInt = 28_800
    nexus_tenant_hmac_key: SecretStr | None = None
    nexus_quant_worker_token: SecretStr | None = None
    nexus_quant_data_worker_token: SecretStr | None = None
    langgraph_aes_key: SecretStr | None = None
    nexus_prompt_max_bytes: PositiveInt = 16 * 1024
    nexus_replay_retention_seconds: PositiveInt = 86_400
    nexus_replay_poll_milliseconds: PositiveInt = 500
    nexus_approval_ttl_seconds: PositiveInt = 900
    nexus_artifact_download_max_bytes: PositiveInt = 256 * 1024 * 1024
    quant_worker_url: str = "http://quant-worker:8000"
    quant_data_worker_url: str = "http://quant-data-worker:8000"
    quant_worker_timeout_seconds: float = 20.0
    quant_worker_poll_seconds: float = 2.0
    quant_worker_wait_seconds: float = 30.0
    postgres_host: str = "localhost"
    postgres_port: int = 5433
    postgres_user: str = "nexus"
    postgres_password: SecretStr = SecretStr("nexus123")
    postgres_db_name: str = "nexus_agent"
    postgres_pool_min_size: int = 2
    postgres_pool_max_size: int = 10
    supervisor_task_call_limit: PositiveInt = 4
    supervisor_model_call_limit: PositiveInt = 12
    researcher_web_search_tool_call_limit: PositiveInt = 4
    researcher_model_call_limit: PositiveInt = 6
    quant_researcher_experiment_tool_call_limit: PositiveInt = 1
    quant_researcher_interpretation_tool_call_limit: PositiveInt = 1
    quant_researcher_tool_call_limit: PositiveInt = 12
    quant_researcher_model_call_limit: PositiveInt = 16
    quant_data_agent_tool_call_limit: PositiveInt = 2
    quant_data_agent_model_call_limit: PositiveInt = 5
    ai_trader_agent_tool_call_limit: PositiveInt = 6
    ai_trader_agent_model_call_limit: PositiveInt = 8

    @model_validator(mode="after")
    def validate_production_settings(self) -> Self:
        """Fail closed on known-unsafe production defaults."""
        if self.nexus_env != "production":
            return self

        postgres_password = self.postgres_password.get_secret_value().strip()
        default_password = type(self).model_fields[
            "postgres_password"
        ].default.get_secret_value()
        if not postgres_password or postgres_password == default_password:
            msg = (
                "POSTGRES_PASSWORD must be set to a non-default value when "
                "NEXUS_ENV=production."
            )
            raise ValueError(msg)
        required = {
            "NEXUS_OIDC_ISSUER": self.nexus_oidc_issuer,
            "NEXUS_OIDC_AUDIENCE": self.nexus_oidc_audience,
            "NEXUS_TENANT_HMAC_KEY": (
                self.nexus_tenant_hmac_key.get_secret_value()
                if self.nexus_tenant_hmac_key
                else None
            ),
            "NEXUS_QUANT_WORKER_TOKEN": (
                self.nexus_quant_worker_token.get_secret_value()
                if self.nexus_quant_worker_token
                else None
            ),
            "NEXUS_QUANT_DATA_WORKER_TOKEN": (
                self.nexus_quant_data_worker_token.get_secret_value()
                if self.nexus_quant_data_worker_token
                else None
            ),
            "LANGGRAPH_AES_KEY": (
                self.langgraph_aes_key.get_secret_value()
                if self.langgraph_aes_key
                else None
            ),
        }
        if self.nexus_browser_session_auth != "disabled":
            required.update(
                {
                    "NEXUS_OIDC_CLIENT_ID": self.nexus_oidc_client_id,
                    "NEXUS_OIDC_CLIENT_SECRET": (
                        self.nexus_oidc_client_secret.get_secret_value()
                        if self.nexus_oidc_client_secret
                        else None
                    ),
                    "NEXUS_APP_ORIGIN": self.nexus_app_origin,
                    "NEXUS_BROWSER_SESSION_KEY": (
                        self.nexus_browser_session_key.get_secret_value()
                        if self.nexus_browser_session_key
                        else None
                    ),
                }
            )
        missing = [name for name, value in required.items() if not str(value or "").strip()]
        if missing:
            raise ValueError(
                f"Production security settings are required: {', '.join(missing)}."
            )
        if not str(self.nexus_oidc_issuer).startswith("https://"):
            raise ValueError("NEXUS_OIDC_ISSUER must use HTTPS in production.")
        if self.nexus_browser_session_auth != "disabled":
            raw_app_origin = str(self.nexus_app_origin)
            app_origin = urlsplit(raw_app_origin)
            try:
                app_origin.port
            except ValueError as exc:
                raise ValueError("NEXUS_APP_ORIGIN contains an invalid port.") from exc
            if (
                app_origin.scheme != "https"
                or not app_origin.hostname
                or app_origin.username is not None
                or app_origin.password is not None
                or app_origin.path
                or app_origin.query
                or app_origin.fragment
                or any(character.isspace() or ord(character) < 32 for character in raw_app_origin)
            ):
                raise ValueError("NEXUS_APP_ORIGIN must be one HTTPS origin without credentials or a path.")
            if len(self.nexus_oidc_client_secret.get_secret_value()) < 32:
                raise ValueError("NEXUS_OIDC_CLIENT_SECRET must be at least 32 characters.")
            if len(self.nexus_browser_session_key.get_secret_value().encode()) != 32:
                raise ValueError("NEXUS_BROWSER_SESSION_KEY must be exactly 32 bytes.")
            if "openid" not in self.nexus_oidc_scope.split():
                raise ValueError("NEXUS_OIDC_SCOPE must include openid for browser sessions.")
            if not 300 <= self.nexus_browser_session_idle_seconds <= 7200:
                raise ValueError("Browser session idle TTL must be between 5 minutes and 2 hours.")
            if not 3600 <= self.nexus_browser_session_absolute_seconds <= 28_800:
                raise ValueError("Browser session absolute TTL must be between 1 and 8 hours.")
        if self.nexus_oidc_jwks_uri and not self.nexus_oidc_jwks_uri.startswith(
            "https://"
        ):
            raise ValueError("NEXUS_OIDC_JWKS_URI must use HTTPS in production.")
        asymmetric_algorithms = {
            "RS256",
            "RS384",
            "RS512",
            "PS256",
            "PS384",
            "PS512",
            "ES256",
            "ES384",
            "ES512",
        }
        if not self.nexus_oidc_allowed_algorithms or not set(
            self.nexus_oidc_allowed_algorithms
        ).issubset(asymmetric_algorithms):
            raise ValueError(
                "NEXUS_OIDC_ALLOWED_ALGORITHMS must contain only asymmetric "
                "RS, PS, or ES algorithms."
            )
        if len(self.nexus_tenant_hmac_key.get_secret_value()) < 32:
            raise ValueError("NEXUS_TENANT_HMAC_KEY must be at least 32 characters.")
        worker_tokens = {
            "NEXUS_QUANT_WORKER_TOKEN": self.nexus_quant_worker_token,
            "NEXUS_QUANT_DATA_WORKER_TOKEN": self.nexus_quant_data_worker_token,
        }
        for name, token in worker_tokens.items():
            if token is None or len(token.get_secret_value()) < 32:
                raise ValueError(f"{name} must be at least 32 characters.")
        production_worker_urls = {
            "QUANT_WORKER_URL": (
                self.quant_worker_url,
                "http://quant-worker:8000",
            ),
            "QUANT_DATA_WORKER_URL": (
                self.quant_data_worker_url,
                "http://quant-data-worker:8000",
            ),
        }
        for name, (configured, expected) in production_worker_urls.items():
            if configured.rstrip("/") != expected:
                raise ValueError(f"{name} must target the canonical private service.")
        if self.ai_trader_api_base_url and (
            self.ai_trader_api_base_url.rstrip("/")
            != "http://ai-trader-worker:8000/api"
        ):
            raise ValueError(
                "AI_TRADER_API_BASE_URL must target the canonical private service."
            )
        aes_key = self.langgraph_aes_key.get_secret_value().encode()
        if len(aes_key) not in {16, 24, 32}:
            raise ValueError("LANGGRAPH_AES_KEY must be exactly 16, 24, or 32 bytes.")
        return self

    @property
    def postgres_url(self) -> str:
        """PostgreSQL connection URL for checkpoint persistence."""
        user = quote(self.postgres_user, safe="")
        password = quote(self.postgres_password.get_secret_value(), safe="")
        database = quote(self.postgres_db_name, safe="")
        return (
            f"postgresql://{user}:{password}"
            f"@{self.postgres_host}:{self.postgres_port}/{database}"
        )


settings = Settings()
configure_logging(
    service="backend",
    environment=settings.nexus_env,
)


def create_model(*, callbacks: list[BaseCallbackHandler] | None = None):
    """Create the LLM model."""
    return init_chat_model(
        model=settings.model,
        api_key=settings.deepseek_api_key.get_secret_value(),
        callbacks=callbacks,
        # DeepSeek may reuse a tool-call ID when emitting parallel calls. The
        # duplicate only becomes visible on the following model request, when
        # the provider rejects the persisted tool transcript with HTTP 400.
        model_kwargs={"parallel_tool_calls": False},
    )

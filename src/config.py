from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Anthropic
    anthropic_api_key: str = ""

    # Codat
    codat_api_key: str = ""
    codat_webhook_secret: str = ""

    # Instantly.ai (kept for account management)
    instantly_api_key: str = ""

    # Resend (transactional email sending)
    resend_api_key: str = ""

    # Vapi
    vapi_api_key: str = ""

    # ElevenLabs
    elevenlabs_api_key: str = ""

    # Stripe
    stripe_secret_key: str = ""
    stripe_webhook_secret: str = ""

    # Xero
    xero_client_id: str = ""
    xero_client_secret: str = ""

    # QuickBooks
    quickbooks_client_id: str = ""
    quickbooks_client_secret: str = ""
    quickbooks_sandbox: bool = True

    # OAuth
    oauth_redirect_base_url: str = ""
    token_encryption_key: str = ""

    # Supabase
    supabase_url: str = ""
    supabase_anon_key: str = ""
    supabase_service_role_key: str = ""

    # Slack
    slack_webhook_url: str = ""

    # Dashboard
    dashboard_password: str = ""
    cors_allowed_origins: list[str] = []

    # Internal (Cloudflare Worker -> Container calls, e.g. cron-triggered daily sync)
    internal_sync_secret: str = ""

    # Application
    agent_default_name: str = "Alex"
    agent_default_email: str = ""
    default_currency: str = "GBP"
    max_discount_percent: float = 3.0
    fee_flat_amount: float = 500.0
    fee_percentage: float = 10.0
    fee_percentage_threshold: float = 5000.0

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()

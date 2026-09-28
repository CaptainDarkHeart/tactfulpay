from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # OpenRouter (LLM provider for the Strategist brain)
    openrouter_api_key: str = ""

    # Message generation: needs tone/nuance for tactical empathy, so leads
    # with a stronger model. Fallback order also spreads across provider
    # lineages (Alibaba, DeepSeek, Z.ai) for resilience.
    openrouter_message_models: list[str] = [
        "qwen/qwen3-235b-a22b-2507",
        "deepseek/deepseek-v3.2",
        "z-ai/glm-4.6",
    ]

    # Reply classification: fixed-category output, lower stakes if wrong
    # (falls back to STALL). GLM 4.7 Flash was tried first for cost but
    # intermittently leaked reasoning text into the response even with
    # reasoning disabled, breaking the strict "CATEGORY - reason" parse.
    # Qwen3/DeepSeek held the format cleanly in every test, so they lead.
    openrouter_classifier_models: list[str] = [
        "qwen/qwen3-235b-a22b-2507",
        "deepseek/deepseek-v3.2",
        "z-ai/glm-4.7-flash",
    ]

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
    vat_registered: bool = False
    vat_rate: float = 20.0
    # Bank of England base rate, percent. Statutory interest under the Late
    # Payment of Commercial Debts (Interest) Act 1998 is this plus 8%. This
    # tracks a real published rate and must be updated by hand when it
    # changes, there is no live feed here. Last checked 2026-09-28: 3.75%,
    # held at the 17 September 2026 MPC meeting (bankofengland.co.uk).
    boe_base_rate_percent: float = 3.75

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()

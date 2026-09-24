import { Container, getContainer } from "@cloudflare/containers";

export interface Env {
  OAAS_CONTAINER: DurableObjectNamespace<OaasContainer>;
  ANTHROPIC_API_KEY: string;
  SUPABASE_URL: string;
  SUPABASE_ANON_KEY: string;
  SUPABASE_SERVICE_ROLE_KEY: string;
  RESEND_API_KEY: string;
  STRIPE_SECRET_KEY: string;
  STRIPE_WEBHOOK_SECRET: string;
  VAPI_API_KEY: string;
  ELEVENLABS_API_KEY: string;
  CODAT_API_KEY: string;
  CODAT_WEBHOOK_SECRET: string;
  XERO_CLIENT_ID: string;
  XERO_CLIENT_SECRET: string;
  QUICKBOOKS_CLIENT_ID: string;
  QUICKBOOKS_CLIENT_SECRET: string;
  TOKEN_ENCRYPTION_KEY: string;
  DASHBOARD_PASSWORD: string;
  INTERNAL_SYNC_SECRET: string;
}

// Container instance running the existing FastAPI app (uvicorn on :8000).
export class OaasContainer extends Container<Env> {
  defaultPort = 8000;
  // Sleep the container after 10 min idle; the Worker restarts it on the
  // next request. Fine for a dashboard + webhook receiver, not for
  // anything latency-sensitive.
  sleepAfter = "10m";

  envVars = {
    ANTHROPIC_API_KEY: this.env.ANTHROPIC_API_KEY,
    SUPABASE_URL: this.env.SUPABASE_URL,
    SUPABASE_ANON_KEY: this.env.SUPABASE_ANON_KEY,
    SUPABASE_SERVICE_ROLE_KEY: this.env.SUPABASE_SERVICE_ROLE_KEY,
    RESEND_API_KEY: this.env.RESEND_API_KEY,
    STRIPE_SECRET_KEY: this.env.STRIPE_SECRET_KEY,
    STRIPE_WEBHOOK_SECRET: this.env.STRIPE_WEBHOOK_SECRET,
    VAPI_API_KEY: this.env.VAPI_API_KEY,
    ELEVENLABS_API_KEY: this.env.ELEVENLABS_API_KEY,
    CODAT_API_KEY: this.env.CODAT_API_KEY,
    CODAT_WEBHOOK_SECRET: this.env.CODAT_WEBHOOK_SECRET,
    XERO_CLIENT_ID: this.env.XERO_CLIENT_ID,
    XERO_CLIENT_SECRET: this.env.XERO_CLIENT_SECRET,
    QUICKBOOKS_CLIENT_ID: this.env.QUICKBOOKS_CLIENT_ID,
    QUICKBOOKS_CLIENT_SECRET: this.env.QUICKBOOKS_CLIENT_SECRET,
    TOKEN_ENCRYPTION_KEY: this.env.TOKEN_ENCRYPTION_KEY,
    DASHBOARD_PASSWORD: this.env.DASHBOARD_PASSWORD,
    INTERNAL_SYNC_SECRET: this.env.INTERNAL_SYNC_SECRET,
  };
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const container = getContainer(env.OAAS_CONTAINER);
    return container.fetch(request);
  },

  async scheduled(_event: ScheduledEvent, env: Env): Promise<void> {
    const container = getContainer(env.OAAS_CONTAINER);
    await container.fetch(
      new Request("http://container/internal/run-daily-sync", {
        method: "POST",
        headers: { "x-internal-secret": env.INTERNAL_SYNC_SECRET },
      }),
    );
  },
};

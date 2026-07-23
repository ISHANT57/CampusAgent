import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { ArrowLeft, Check, ExternalLink, X } from "lucide-react";
import { api } from "@/api/client";
import type { ProviderInfo, TestResult } from "@/api/types";
import { Button } from "@/components/ui/Button";
import { useProvider } from "@/hooks/useProvider";

export function Settings() {
  const { config, setConfig } = useProvider();
  const [providers, setProviders] = useState<ProviderInfo[]>([]);
  const [selected, setSelected] = useState<string | null>(config?.provider ?? null);
  const [apiKey, setApiKey] = useState(config?.api_key ?? "");
  const [model, setModel] = useState(config?.model ?? "");
  const [baseUrl, setBaseUrl] = useState(config?.base_url ?? "");
  const [result, setResult] = useState<TestResult | null>(null);
  const [testing, setTesting] = useState(false);

  useEffect(() => {
    api.providers().then(setProviders).catch(() => {});
  }, []);

  const provider = providers.find((p) => p.id === selected) ?? null;

  async function test() {
    if (!provider) return;
    setTesting(true);
    setResult(null);
    try {
      const outcome = await api.testProvider({
        provider: provider.id,
        api_key: apiKey,
        model: model || provider.default_model,
        base_url: baseUrl || null,
      });
      setResult(outcome);
      // Save only on a full pass. tool_calling === false means the credential
      // works but the agent will not — saving it would guarantee a confusing
      // failure at step 4 of the first real run.
      if (outcome.ok && outcome.tool_calling) {
        setConfig({
          provider: provider.id,
          api_key: apiKey,
          model: model || provider.default_model,
          base_url: baseUrl || null,
        });
      }
    } finally {
      setTesting(false);
    }
  }

  return (
    <div className="mx-auto max-w-2xl px-4 py-6">
      <Link
        to="/"
        className="mb-6 flex items-center gap-1.5 text-sm text-[var(--color-muted)] hover:text-[var(--color-text)]"
      >
        <ArrowLeft size={14} /> Back
      </Link>

      <h1 className="mb-1 text-xl font-semibold">AI provider</h1>
      <p className="mb-6 text-sm text-[var(--color-muted)]">
        You bring your own key, so there are no usage limits beyond your provider's.
      </p>

      <div className="space-y-2">
        {providers.map((p) => (
          <button
            key={p.id}
            onClick={() => {
              setSelected(p.id);
              setModel("");
              setResult(null);
            }}
            className={`flex w-full items-center gap-3 rounded-lg border px-4 py-3 text-left transition-colors ${
              selected === p.id
                ? "border-[var(--color-accent)] bg-[var(--color-surface)]"
                : "border-[var(--color-border)] hover:border-[var(--color-border-strong)]"
            }`}
          >
            <div className="flex-1">
              <div className="font-medium">{p.label}</div>
              {/* Honest blurbs: someone who picks OpenRouter expecting
                  unlimited use will hit its cap in a few runs and blame us. */}
              <div className="text-xs text-[var(--color-muted)]">{p.blurb}</div>
            </div>
            {!p.requires_key && (
              <span className="rounded bg-[var(--color-ok)]/15 px-2 py-0.5 text-[10px] text-[var(--color-ok)]">
                no key
              </span>
            )}
          </button>
        ))}
      </div>

      {provider && (
        <div className="mt-6 space-y-4 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-4">
          {provider.requires_key && (
            <Field label="API key" hint={provider.keys_url}>
              <input
                type="password"
                value={apiKey}
                onChange={(e) => setApiKey(e.target.value)}
                placeholder="paste your key"
                className="w-full rounded border border-[var(--color-border)] bg-[var(--color-bg)] px-3 py-2 text-sm outline-none focus:border-[var(--color-accent)] mono"
              />
            </Field>
          )}

          {provider.models.length > 0 && (
            <Field label="model">
              <select
                value={model || provider.default_model || ""}
                onChange={(e) => setModel(e.target.value)}
                className="w-full rounded border border-[var(--color-border)] bg-[var(--color-bg)] px-3 py-2 text-sm outline-none"
              >
                {provider.models.map((m) => (
                  <option key={m.id} value={m.id}>
                    {m.label}
                    {m.notes ? ` — ${m.notes}` : ""}
                  </option>
                ))}
              </select>
            </Field>
          )}

          {provider.allows_custom_base_url && (
            <Field label="base URL">
              <input
                value={baseUrl}
                onChange={(e) => setBaseUrl(e.target.value)}
                placeholder={provider.id === "ollama" ? "http://localhost:11434/v1" : "https://…/v1"}
                className="w-full rounded border border-[var(--color-border)] bg-[var(--color-bg)] px-3 py-2 text-sm outline-none mono"
              />
            </Field>
          )}

          <Button onClick={test} disabled={testing || (provider.requires_key && !apiKey)}>
            {testing ? "testing…" : "Test connection"}
          </Button>

          {result && <TestOutcome result={result} />}
        </div>
      )}

      {config && (
        <p className="mt-6 text-xs text-[var(--color-faint)]">
          Your key is kept in this browser tab and sent to the backend with each run. It is not
          stored on the server, and closing the tab clears it.
        </p>
      )}
    </div>
  );
}

function TestOutcome({ result }: { result: TestResult }) {
  // Three separate checks, because a green tick on "connected" alone hides the
  // failure that actually matters. A model that accepts a `tools` parameter
  // and ignores it produces an agent that appears to refuse every task, with
  // no error anywhere — this is where that gets caught.
  return (
    <div className="space-y-1.5 rounded border border-[var(--color-border)] p-3 text-sm">
      <Line ok={result.ok} label="Connected" detail={result.latency_ms ? `${result.latency_ms}ms` : ""} />
      {result.ok && (
        <Line
          ok={result.tool_calling === true}
          label="Tool calling supported"
          detail={result.tool_calling ? "" : "the agent will not work with this model"}
        />
      )}
      {result.ok && <Line ok label="Model" detail={result.model ?? ""} />}
      {result.error && <p className="pt-1 text-xs text-[var(--color-bad)]">{result.error}</p>}
    </div>
  );
}

function Line({ ok, label, detail }: { ok: boolean; label: string; detail?: string }) {
  return (
    <div className="flex items-center gap-2">
      {ok ? (
        <Check size={14} className="text-[var(--color-ok)]" />
      ) : (
        <X size={14} className="text-[var(--color-bad)]" />
      )}
      <span>{label}</span>
      {detail && <span className="text-xs text-[var(--color-faint)] mono">{detail}</span>}
    </div>
  );
}

function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string | null;
  children: React.ReactNode;
}) {
  return (
    <div>
      <div className="mb-1.5 flex items-center justify-between">
        <label className="text-xs uppercase tracking-wider text-[var(--color-faint)]">{label}</label>
        {hint && (
          <a
            href={hint}
            target="_blank"
            rel="noreferrer"
            className="flex items-center gap-1 text-xs text-[var(--color-accent)] hover:underline"
          >
            get a key <ExternalLink size={10} />
          </a>
        )}
      </div>
      {children}
    </div>
  );
}

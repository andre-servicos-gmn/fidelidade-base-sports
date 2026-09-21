import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { ApiError, login } from "../lib/api";
import { useAuth } from "../lib/auth";
import { Banner, Button, Field, TextInput } from "../components/ui";

export function LoginPage() {
  const { token, signIn } = useAuth();
  const navigate = useNavigate();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  // Já logado → vai direto pro painel.
  if (token) {
    navigate("/rules", { replace: true });
  }

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      const { access_token } = await login(username.trim(), password);
      signIn(access_token);
      navigate("/rules", { replace: true });
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err.message
          : "Não foi possível conectar. Verifique a conexão."
      );
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="login-shell">
      <section className="login-hero" aria-hidden>
        <svg className="login-speed" viewBox="0 0 400 120" preserveAspectRatio="none">
          <path d="M0 30 H300" />
          <path d="M40 58 H340" />
          <path d="M100 86 H320" />
        </svg>
        <img className="login-logo" src="/logo-dark.png" alt="" />
        <p className="login-tagline">
          Cada compra vira ponto.
          <br />
          <span>Cada ponto vira desconto.</span>
        </p>
      </section>

      <section className="login-panel">
        <div className="login-card">
          <div className="login-head">
            <span className="eyebrow">Fidelidade Base Sports</span>
            <h1>Painel administrativo</h1>
            <p className="muted">Entre com seu usuário e senha.</p>
          </div>

          <form onSubmit={onSubmit}>
            <Field label="Usuário">
              <TextInput
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                autoFocus
                autoComplete="username"
                required
              />
            </Field>
            <Field label="Senha">
              <TextInput
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete="current-password"
                required
              />
            </Field>

            {error && (
              <div className="mb-4">
                <Banner tone="warn">{error}</Banner>
              </div>
            )}

            <Button type="submit" block loading={loading}>
              Entrar
            </Button>
          </form>
        </div>
      </section>
    </div>
  );
}

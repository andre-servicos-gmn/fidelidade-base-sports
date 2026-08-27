import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { ApiError, login } from "../lib/api";
import { useAuth } from "../lib/auth";
import { Button, Field, TextInput } from "../components/ui";

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
      <div className="login-card card card-pad">
        <div className="login-head">
          <div className="brand">
            <div className="brand-mark">BS</div>
            <div>
              <div className="brand-name">Base Sports</div>
              <div className="brand-sub">Fidelidade</div>
            </div>
          </div>
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
            <div className="banner banner-warn" style={{ marginBottom: 16 }}>
              <span className="banner-icon">!</span>
              <div>{error}</div>
            </div>
          )}

          <Button type="submit" block loading={loading}>
            Entrar
          </Button>
        </form>
      </div>
    </div>
  );
}

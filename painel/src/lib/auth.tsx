import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { clearToken, getToken, setToken } from "./api";

interface AuthState {
  token: string | null;
  username: string | null;
  signIn: (token: string) => void;
  signOut: () => void;
}

const AuthContext = createContext<AuthState | undefined>(undefined);

/** Lê o `username` do payload do JWT (sem validar — só para exibir quem entrou). */
function readUsername(token: string | null): string | null {
  if (!token) return null;
  try {
    const payload = JSON.parse(atob(token.split(".")[1]));
    return payload.username ?? null;
  } catch {
    return null;
  }
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [token, setTokenState] = useState<string | null>(() => getToken());

  const signIn = (newToken: string) => {
    setToken(newToken);
    setTokenState(newToken);
  };
  const signOut = () => {
    clearToken();
    setTokenState(null);
  };

  // A API dispara este evento ao receber 401 (token expirado) -> desloga.
  useEffect(() => {
    const onUnauthorized = () => setTokenState(null);
    window.addEventListener("auth:unauthorized", onUnauthorized);
    return () => window.removeEventListener("auth:unauthorized", onUnauthorized);
  }, []);

  const value = useMemo<AuthState>(
    () => ({ token, username: readUsername(token), signIn, signOut }),
    [token]
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth deve ser usado dentro de <AuthProvider>");
  return ctx;
}

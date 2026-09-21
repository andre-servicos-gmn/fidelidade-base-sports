import { NavLink, Outlet, useNavigate } from "react-router-dom";
import { useAuth } from "../lib/auth";
import {
  IconLogout,
  IconMegaphone,
  IconRacket,
  IconTicket,
  IconUsers,
} from "./icons";

const NAV = [
  { to: "/rules", label: "Regras de pontuação", Icon: IconRacket },
  { to: "/coupons", label: "Cupons", Icon: IconTicket },
  { to: "/customers", label: "Clientes", Icon: IconUsers },
  { to: "/affiliates", label: "Afiliados", Icon: IconMegaphone },
];

export function Layout() {
  const { username, signOut } = useAuth();
  const navigate = useNavigate();
  const name = username ?? "admin";

  const logout = () => {
    signOut();
    navigate("/login", { replace: true });
  };

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <img
            className="brand-logo"
            src="/logo-light.png"
            alt="Base Sports"
            width={120}
            height={80}
          />
          <span className="brand-tag">Fidelidade</span>
        </div>

        <nav className="nav" aria-label="Seções do painel">
          {NAV.map(({ to, label, Icon }) => (
            <NavLink
              key={to}
              to={to}
              className={({ isActive }) =>
                `nav-link ${isActive ? "active" : ""}`
              }
            >
              <Icon className="nav-icon" />
              {label}
            </NavLink>
          ))}
        </nav>

        <div className="sidebar-footer">
          <div className="user-chip">
            <span className="avatar" aria-hidden>
              {name.charAt(0).toUpperCase()}
            </span>
            <span className="user-chip-text">
              <span className="subtle">Conectado como</span>
              <strong>{name}</strong>
            </span>
          </div>
          <button
            type="button"
            className="icon-btn"
            onClick={logout}
            aria-label="Sair"
            title="Sair"
          >
            <IconLogout size={18} />
          </button>
        </div>
      </aside>

      <main className="content">
        <Outlet />
      </main>
    </div>
  );
}

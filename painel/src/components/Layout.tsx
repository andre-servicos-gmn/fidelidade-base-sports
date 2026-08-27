import { NavLink, Outlet, useNavigate } from "react-router-dom";
import { useAuth } from "../lib/auth";
import { Button } from "./ui";

const NAV = [
  { to: "/rules", label: "Regras de pontuação", icon: "◎" },
  { to: "/coupons", label: "Cupons", icon: "▦" },
  { to: "/customers", label: "Clientes", icon: "◍" },
  { to: "/affiliates", label: "Afiliados", icon: "◉" },
];

export function Layout() {
  const { username, signOut } = useAuth();
  const navigate = useNavigate();

  const logout = () => {
    signOut();
    navigate("/login", { replace: true });
  };

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-mark">BS</div>
          <div>
            <div className="brand-name">Base Sports</div>
            <div className="brand-sub">Fidelidade</div>
          </div>
        </div>

        <nav className="nav">
          {NAV.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              className={({ isActive }) =>
                `nav-link ${isActive ? "active" : ""}`
              }
            >
              <span className="nav-icon">{item.icon}</span>
              {item.label}
            </NavLink>
          ))}
        </nav>

        <div className="sidebar-footer">
          <div className="user-chip">
            Conectado como
            <strong>{username ?? "admin"}</strong>
          </div>
          <Button variant="ghost" size="sm" onClick={logout}>
            Sair
          </Button>
        </div>
      </aside>

      <main className="content">
        <Outlet />
      </main>
    </div>
  );
}

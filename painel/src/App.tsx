import { Navigate, Route, Routes } from "react-router-dom";
import { ProtectedRoute } from "./components/ProtectedRoute";
import { Layout } from "./components/Layout";
import { LoginPage } from "./pages/Login";
import { RulesPage } from "./pages/Rules";
import { CouponsPage } from "./pages/Coupons";
import { CustomersPage } from "./pages/Customers";
import { AffiliatesPage } from "./pages/Affiliates";

export function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route element={<ProtectedRoute />}>
        <Route element={<Layout />}>
          <Route path="/rules" element={<RulesPage />} />
          <Route path="/coupons" element={<CouponsPage />} />
          <Route path="/customers" element={<CustomersPage />} />
          <Route path="/affiliates" element={<AffiliatesPage />} />
        </Route>
      </Route>
      <Route path="*" element={<Navigate to="/rules" replace />} />
    </Routes>
  );
}

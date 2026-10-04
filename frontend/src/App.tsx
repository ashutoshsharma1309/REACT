import { useCallback, useMemo, useState } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import { RefreshContext } from "./lib/useApi";
import { Layout } from "./components/common/Layout";
import { CasePage } from "./pages/CasePage";
import { CommandCenterPage } from "./pages/CommandCenterPage";
import { DecisionsPage } from "./pages/DecisionsPage";
import { FindingsPage, InvestigationsPage, OpportunitiesPage } from "./pages/FindingsPages";
import { InventoryPage, ProductsPage, SetupPage, SuppliersPage } from "./pages/MerchantPages";
import { ActivityPage, DataPage, NotFoundPage } from "./pages/OtherPages";

export function AppRoutes() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<CommandCenterPage />} />
        <Route path="findings" element={<FindingsPage />} />
        <Route path="risks" element={<Navigate to="/findings" replace />} />
        <Route path="products" element={<ProductsPage />} />
        <Route path="inventory" element={<InventoryPage />} />
        <Route path="suppliers" element={<SuppliersPage />} />
        <Route path="setup" element={<SetupPage />} />
        <Route path="opportunities" element={<OpportunitiesPage />} />
        <Route path="investigations" element={<InvestigationsPage />} />
        <Route path="decisions" element={<DecisionsPage />} />
        <Route path="activity" element={<ActivityPage />} />
        <Route path="data" element={<DataPage />} />
        <Route path="cases/:kind/:id" element={<CasePage />} />
        <Route path="*" element={<NotFoundPage />} />
      </Route>
    </Routes>
  );
}

export function App() {
  const [version, setVersion] = useState(0);
  const refresh = useCallback(() => setVersion((v) => v + 1), []);
  const value = useMemo(() => ({ version, refresh }), [version, refresh]);
  return (
    <RefreshContext.Provider value={value}>
      <AppRoutes />
    </RefreshContext.Provider>
  );
}

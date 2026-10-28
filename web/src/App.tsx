import { HashRouter, Navigate, Route, Routes } from "react-router-dom";

import { Shell } from "./components/Shell";
import { ConfigsPage } from "./pages/ConfigsPage";
import { MarketPage } from "./pages/MarketPage";
import { OrdersPage } from "./pages/OrdersPage";
import { OverviewPage } from "./pages/OverviewPage";
import { PositionsPage } from "./pages/PositionsPage";

export function App() {
  return (
    <HashRouter>
      <Shell>
        <Routes>
          <Route path="/" element={<OverviewPage />} />
          <Route path="/positions" element={<PositionsPage />} />
          <Route path="/orders" element={<OrdersPage />} />
          <Route path="/market" element={<MarketPage />} />
          <Route path="/configs" element={<ConfigsPage />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </Shell>
    </HashRouter>
  );
}

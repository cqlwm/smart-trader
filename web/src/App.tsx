import { HashRouter, Navigate, Route, Routes } from "react-router-dom";

import { Shell } from "./components/Shell";
import { MarketPage } from "./pages/MarketPage";
import { OverviewPage } from "./pages/OverviewPage";
import { StrategiesPage } from "./pages/StrategiesPage";
import { StrategyDetailPage } from "./pages/StrategyDetailPage";

export function App() {
  return (
    <HashRouter>
      <Shell>
        <Routes>
          <Route path="/" element={<OverviewPage />} />
          <Route path="/strategies" element={<StrategiesPage />} />
          <Route path="/strategies/:strategyKey" element={<StrategyDetailPage />} />
          <Route path="/market" element={<MarketPage />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </Shell>
    </HashRouter>
  );
}

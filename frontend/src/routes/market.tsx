import { createFileRoute } from "@tanstack/react-router";
import { z } from "zod";

import { MARKET_RANGES } from "../api/marketQuotes.js";
import { MarketPage } from "../features/market/MarketPage.js";

// Selection lives in the URL so the view survives a reload and can be linked.
// `.catch()` makes a hand-edited ?symbol= degrade to "nothing selected" instead
// of failing the route.
const marketSearch = z.object({
  symbol: z.string().regex(/^[A-Z0-9]{3,10}$/).catch(""),
  range: z.enum(MARKET_RANGES).catch("1mo"),
});

export const Route = createFileRoute("/market")({
  validateSearch: marketSearch,
  component: MarketRoute,
});

function MarketRoute() {
  const { symbol, range } = Route.useSearch();
  const navigate = Route.useNavigate();
  return (
    <MarketPage
      symbol={symbol}
      range={range}
      onSelect={(next) => {
        void navigate({ search: (previous) => ({ ...previous, symbol: next }), replace: true });
      }}
      onRangeChange={(next) => {
        void navigate({ search: (previous) => ({ ...previous, range: next }), replace: true });
      }}
    />
  );
}

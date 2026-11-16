import unittest
from unittest.mock import MagicMock, patch

from app import fx
from app.platforms import meta
from app.platforms.meta import Meta


def resp(data, status=200):
    r = MagicMock(); r.status_code = status; r.json.return_value = data; r.is_error = status >= 400
    r.raise_for_status.return_value = None
    return r


class FxTests(unittest.TestCase):
    def setUp(self):
        fx._cache.clear()

    def test_same_currency_needs_no_rate_and_pins_win(self):
        self.assertEqual(fx.rate("COP", "cop"), 1.0)
        with patch.dict("os.environ", {"FX_RATE_COP_USD": "0.00025"}):
            self.assertAlmostEqual(fx.convert(20000, "COP", "USD"), 5.0)

    def test_rate_is_fetched_cached_and_stale_used_when_source_down(self):
        import httpx
        with patch.object(fx.httpx, "get", return_value=resp({"result": "success", "rates": {"USD": 0.00026}})) as g:
            self.assertEqual(fx.rate("COP", "USD"), 0.00026)
            fx.rate("COP", "USD")
            self.assertEqual(g.call_count, 1)
        fx._cache["COP"] = (fx._cache["COP"][0] - fx.TTL - 5, fx._cache["COP"][1])      # expired but not ancient
        with patch.object(fx.httpx, "get", side_effect=httpx.ConnectError("down")):
            self.assertEqual(fx.rate("COP", "USD"), 0.00026)

    def test_no_rate_means_refuse_not_guess(self):
        import httpx
        with patch.object(fx.httpx, "get", side_effect=httpx.ConnectError("down")):
            with self.assertRaises(fx.FxError):
                fx.rate("COP", "USD")


class MetaBudgetTests(unittest.TestCase):
    def setUp(self):
        fx._cache.clear()
        self.m = Meta({"access_token": "t", "ad_account_id": "123"})

    def run_budget(self, account_currency, value, app="COP", rate=None):
        env = {"FX_RATE_COP_USD": str(rate)} if rate else {}
        with patch.object(meta, "DEFAULT_CURRENCY", app), patch.dict("os.environ", env), \
                patch.object(meta.httpx, "get", return_value=resp({"currency": account_currency})), \
                patch.object(meta.httpx, "request", return_value=resp({"success": True})) as req:
            self.m.set_budget("999", value)
        return req.call_args.kwargs["data"]["daily_budget"]

    def test_cop_budget_to_a_usd_account_is_converted_and_sent_in_cents(self):
        self.assertEqual(self.run_budget("USD", 50000, rate=0.00025), 1250)        # 50,000 COP = 12.50 USD = 1250 cents

    def test_cop_account_gets_whole_pesos_not_centavos(self):
        self.assertEqual(self.run_budget("COP", 50000), 50000)

    def test_usd_app_to_usd_account_keeps_cents(self):
        self.assertEqual(self.run_budget("USD", 25, app="USD"), 2500)

    def test_budget_below_the_minimum_is_refused(self):
        with self.assertRaises(RuntimeError):
            self.run_budget("USD", 1, rate=0.00025)

    def test_imported_budgets_come_back_in_the_app_currency(self):
        data = {"data": [{"id": "1", "name": "A", "status": "ACTIVE", "daily_budget": "1250"}]}
        with patch.object(meta, "DEFAULT_CURRENCY", "COP"), patch.dict("os.environ", {"FX_RATE_USD_COP": "4000"}), \
                patch.object(meta.httpx, "get", return_value=resp({"currency": "USD"})), \
                patch.object(meta.httpx, "request", return_value=resp(data)):
            self.assertEqual(self.m.list_campaigns()[0]["daily_budget"], 50000.0)


if __name__ == "__main__":
    unittest.main()

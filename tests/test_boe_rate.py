"""Tests for the live Bank of England base rate fetch and its fallback."""

from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
import requests

from src.billing import boe_rate


@pytest.fixture(autouse=True)
def _clear_cache():
    boe_rate._cache.clear()
    yield
    boe_rate._cache.clear()


class TestFetchBoeBaseRate:
    @patch("src.billing.boe_rate.resilient_session")
    def test_parses_last_row_of_csv(self, mock_session_factory):
        mock_response = MagicMock()
        mock_response.text = "DATE,IUDBEDR\n02 Jan 2025,4.75\n25 Sep 2026,3.75\n"
        mock_response.raise_for_status = MagicMock()
        mock_session = MagicMock()
        mock_session.get.return_value = mock_response
        mock_session_factory.return_value = mock_session

        rate = boe_rate._fetch_boe_base_rate()
        assert rate == Decimal("3.75")

    @patch("src.billing.boe_rate.resilient_session")
    def test_malformed_response_raises(self, mock_session_factory):
        mock_response = MagicMock()
        mock_response.text = "<html>not a csv</html>"
        mock_response.raise_for_status = MagicMock()
        mock_session = MagicMock()
        mock_session.get.return_value = mock_response
        mock_session_factory.return_value = mock_session

        with pytest.raises(ValueError):
            boe_rate._fetch_boe_base_rate()


class TestGetBoeBaseRatePercent:
    @patch("src.billing.boe_rate._fetch_boe_base_rate")
    def test_returns_live_value_on_success(self, mock_fetch):
        mock_fetch.return_value = Decimal("3.75")
        assert boe_rate.get_boe_base_rate_percent() == Decimal("3.75")

    @patch("src.billing.boe_rate._fetch_boe_base_rate")
    def test_caches_between_calls(self, mock_fetch):
        mock_fetch.return_value = Decimal("3.75")
        boe_rate.get_boe_base_rate_percent()
        boe_rate.get_boe_base_rate_percent()
        assert mock_fetch.call_count == 1

    @patch("src.billing.boe_rate.settings")
    @patch("src.billing.boe_rate._fetch_boe_base_rate")
    def test_falls_back_to_settings_on_network_error(self, mock_fetch, mock_settings):
        mock_fetch.side_effect = requests.exceptions.ConnectionError("boom")
        mock_settings.boe_base_rate_percent = 4.0
        assert boe_rate.get_boe_base_rate_percent() == Decimal("4.0")

    @patch("src.billing.boe_rate.settings")
    @patch("src.billing.boe_rate._fetch_boe_base_rate")
    def test_falls_back_to_settings_on_parse_error(self, mock_fetch, mock_settings):
        mock_fetch.side_effect = ValueError("unparseable")
        mock_settings.boe_base_rate_percent = 4.0
        assert boe_rate.get_boe_base_rate_percent() == Decimal("4.0")

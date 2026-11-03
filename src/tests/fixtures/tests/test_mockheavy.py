"""Mock-heavy / mock-only tests (§5.7): ≥80% of exercised calls are mocks."""

from unittest.mock import Mock, patch

from prod import pricing


def test_mockonly_pricer():
    with patch("prod.pricing.price_order") as mock_price:
        mock_price.return_value = 90.0
        result = pricing.price_order([100])
        assert result == 90.0
        mock_price.assert_called_once_with([100])


def test_mock_heavy_collab():
    notifier = Mock()
    logger = Mock()
    audit = Mock()
    notifier.notify("x")
    logger.info("y")
    audit.record("z")
    assert notifier.notify.assert_called_once()

"""Unit tests for supplier_updates.py utility functions."""

from supplier_updates import _normalize_ean, has_cost_changed, has_ean_changed


class TestEanNormalization:
    """Tests for EAN handling functions."""

    def test_normalize_ean_strips_whitespace(self) -> None:
        assert _normalize_ean("  6000000000001  ") == "6000000000001"

    def test_normalize_ean_handles_empty_string(self) -> None:
        assert _normalize_ean("") is None

    def test_normalize_ean_handles_whitespace_only(self) -> None:
        assert _normalize_ean("   ") is None

    def test_normalize_ean_handles_none(self) -> None:
        assert _normalize_ean(None) is None

    def test_has_ean_changed_detects_change(self) -> None:
        assert has_ean_changed("6000000000001", "6000000000002") is True

    def test_has_ean_changed_detects_no_change(self) -> None:
        assert has_ean_changed("6000000000001", "6000000000001") is False

    def test_has_ean_changed_normalizes_whitespace(self) -> None:
        assert has_ean_changed("  6000000000001  ", "6000000000001") is False

    def test_has_ean_changed_handles_empty_vs_none(self) -> None:
        assert has_ean_changed("", None) is False

    def test_has_ean_changed_detects_empty_to_value(self) -> None:
        assert has_ean_changed("", "6000000000001") is True


class TestCostChanged:
    """Tests for has_cost_changed."""

    def test_has_cost_changed_detects_change(self) -> None:
        assert has_cost_changed(7500, 76.0) is True

    def test_has_cost_changed_detects_no_change(self) -> None:
        assert has_cost_changed(7500, 75.0) is False

    def test_has_cost_changed_returns_true_for_none_current(self) -> None:
        assert has_cost_changed(None, 75.0) is True

    def test_has_cost_changed_handles_rounding(self) -> None:
        # 7500 pence = 75.00 pounds
        assert has_cost_changed(7500, 75.00) is False
        assert has_cost_changed(7500, 76.00) is True
        # 7501 pence = 75.01 pounds
        assert has_cost_changed(7501, 75.01) is False

import pytest

from scripts.extract_hmrc_alcohol_bulletin import _build_catalog, classify_column


def test_spirits_production_is_quarterly():
    native = {
        "HMRC_ALCOHOL_SPIRITS_PRODUCTION_LAL": {
            "product": "SPIRITS",
            "measure": "PRODUCTION_LAL",
            "published_unit": "litres of alcohol",
            "label": "UK potable spirits production (litres of alcohol)",
            "sheet": "Spirits",
        }
    }
    row = _build_catalog(native, None)["HMRC_ALCOHOL_SPIRITS_PRODUCTION_LAL"]
    assert row["frequency"] == "quarterly"
    assert "Quarterly" in row["description"]


@pytest.mark.parametrize(
    "label",
    [
        "Total beer clearances (litres of alcohol)",
        "Total Alcohol Duty receipts from beer (pounds million)",
    ],
)
def test_a_product_label_cannot_be_reassigned_to_another_sheet(label):
    with pytest.raises(ValueError, match="product"):
        classify_column(label, "SPIRITS")

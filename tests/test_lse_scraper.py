"""Tests for tools/lse_scraper.clean_company_name."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "tools"))
from lse_scraper import clean_company_name


class TestCleanCompanyName:
    def test_unknown_passthrough(self):
        assert clean_company_name("Unknown") == "Unknown"

    def test_nan_returns_unknown(self):
        import pandas as pd
        assert clean_company_name(pd.NA) == "Unknown"

    def test_strips_ord_suffix(self):
        assert clean_company_name("BARCLAYS ORD 25P") == "Barclays"

    def test_strips_npv_suffix(self):
        assert clean_company_name("VODAFONE NPV") == "Vodafone"

    def test_strips_ord_case_insensitive(self):
        assert clean_company_name("ABRDN ord 0.1p") == "Abrdn"

    def test_all_caps_title_cased(self):
        result = clean_company_name("BARCLAYS")
        assert result == "Barclays"

    def test_plc_lowercased_after_title_case(self):
        result = clean_company_name("VODAFONE PLC")
        assert result == "Vodafone plc"

    def test_llc_uppercased_after_title_case(self):
        result = clean_company_name("SOME COMPANY LLC")
        assert result == "Some Company LLC"

    def test_uk_uppercased_after_title_case(self):
        result = clean_company_name("ABRDN UK SMALLER COMPANIES")
        assert result == "Abrdn UK Smaller Companies"

    def test_mixed_case_preserved(self):
        # Mixed-case input should not be title-cased
        result = clean_company_name("BT Group plc")
        assert result == "BT Group plc"

    def test_internal_dot_in_name_not_affected(self):
        # Dots in names should not be stripped (mnemonic dots are handled separately)
        result = clean_company_name("BT.A GROUP")
        assert result == "Bt.A Group"

    def test_trailing_punctuation_stripped(self):
        result = clean_company_name("BARCLAYS,")
        assert result == "Barclays"

    def test_empty_string_returns_empty(self):
        result = clean_company_name("")
        assert result == ""

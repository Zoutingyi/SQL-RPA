"""Regression tests for context-aware sensitive-data masking."""

from utils.masking import mask_rows, mask_value


def test_sensitive_column_is_fully_masked():
    assert mask_value("supersecret", "password") == "***"
    assert mask_value("abc", "api_key") == "***"


def test_email_column_is_masked_by_context():
    assert mask_value("alice@example.com", "email") == "a***e@example.com"


def test_phone_column_is_masked_by_context():
    assert mask_value("13812345678", "phone") == "138****5678"


def test_id_card_column_is_masked_by_context():
    assert mask_value("110101199001011234", "id_card") == "***"
    assert mask_value("110101199001011234", "id_no") == "110101********1234"


def test_ordinary_numeric_columns_are_not_masked():
    assert mask_value("13812345678", "id") == "13812345678"
    assert mask_value("6222021234567890123", "amount") == "6222021234567890123"
    assert mask_value("13812345678", "quantity") == "13812345678"
    assert mask_value("13812345678", "stock") == "13812345678"
    assert mask_value("13812345678", "count") == "13812345678"


def test_mask_rows_respects_column_context():
    columns = ["id", "email", "password", "amount"]
    rows = [[
        "13812345678",
        "bob@example.com",
        "plain-secret",
        "6222021234567890123",
    ]]
    masked = mask_rows(columns, rows)
    assert masked[0] == [
        "13812345678",
        "b***b@example.com",
        "***",
        "6222021234567890123",
    ]

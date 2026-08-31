from storops.core.format import format_size


def test_format_size_bytes():
    assert format_size(512) == "512 B"


def test_format_size_gb_two_decimals():
    assert format_size(93627028848) == "87.20 GB"


def test_format_size_zero():
    assert format_size(0) == "0 B"

import controller_lab


def test_version_is_a_string() -> None:
    assert isinstance(controller_lab.__version__, str)

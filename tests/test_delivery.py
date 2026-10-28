import pytest

from app.delivery import DeliveryConfigurationError, validate_target


def test_target_requires_allowlisted_https_host():
    validate_target(
        "https://hooks.example.test/events",
        frozenset({"hooks.example.test"}),
    )


@pytest.mark.parametrize(
    "url, allowed_hosts",
    [
        ("http://hooks.example.test/events", frozenset({"hooks.example.test"})),
        ("https://other.example.test/events", frozenset({"hooks.example.test"})),
        ("https://user:pass@hooks.example.test/events", frozenset({"hooks.example.test"})),
        ("https://hooks.example.test/events#fragment", frozenset({"hooks.example.test"})),
        ("https://hooks.example.test:8443/events", frozenset({"hooks.example.test"})),
    ],
)
def test_rejects_unsafe_or_unapproved_targets(url, allowed_hosts):
    with pytest.raises(DeliveryConfigurationError):
        validate_target(url, allowed_hosts)

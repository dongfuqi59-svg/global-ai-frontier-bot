import pytest

from src.utils.url import UnsafeUrlError, ensure_public_url, normalize_url


def test_url_normalization_removes_tracking_and_fragment() -> None:
    normalized = normalize_url(
        "HTTPS://Example.COM:443/news/?utm_source=x&b=2&a=1&fbclid=y#part"
    )
    assert normalized == "https://example.com/news/?a=1&b=2"


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "http://localhost/a",
        "http://127.0.0.1/a",
        "http://169.254.169.254/latest/meta-data/",
        "http://10.0.0.1/a",
        "http://[::1]/a",
        "https://user:password@example.com/a",
    ],
)
def test_malicious_or_private_urls_are_rejected(url: str) -> None:
    with pytest.raises(UnsafeUrlError):
        normalize_url(url)


@pytest.mark.asyncio
async def test_hostname_resolving_to_private_ip_is_rejected() -> None:
    async def resolver(_host: str, _port: int):
        return [(None, None, None, None, ("192.168.1.10", 443))]

    with pytest.raises(UnsafeUrlError):
        await ensure_public_url("https://example.com/feed", resolver)


@pytest.mark.asyncio
async def test_public_dns_result_is_allowed() -> None:
    async def resolver(_host: str, _port: int):
        return [(None, None, None, None, ("8.8.8.8", 443))]

    assert (
        await ensure_public_url("https://example.com/feed", resolver)
        == "https://example.com/feed"
    )

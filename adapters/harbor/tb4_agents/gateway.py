"""Canonical bases for explicitly authorized gateways and local mock audits."""
from ipaddress import ip_address
from urllib.parse import urlsplit


DEFAULT_GATEWAY = "https://yuzhiapi.xyz"
AUTHORIZED_GATEWAY_HOSTS = frozenset({"api.deepseek.com", "rehdasu.cn", "yuzhiapi.xyz"})


def _parts(value):
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError("Gateway base must be a nonempty URL without surrounding whitespace")
    parsed = urlsplit(value)
    if (not parsed.hostname or parsed.username is not None or parsed.password is not None
            or parsed.query or parsed.fragment or parsed.path not in {"", "/", "/v1", "/v1/"}):
        raise ValueError("Use a gateway root or /v1 base without credentials, query, or fragment")
    try:
        parsed.port
    except ValueError:
        raise ValueError("Invalid gateway port") from None
    return parsed


def _canonical(parsed, protocol):
    if protocol not in {"responses", "chat", "anthropic"}:
        raise ValueError("Unsupported gateway protocol")
    host = parsed.hostname
    if ":" in host:
        host = f"[{host}]"
    default_port = 443 if parsed.scheme == "https" else 80
    port = f":{parsed.port}" if parsed.port not in {None, default_port} else ""
    suffix = "/v1" if protocol in {"responses", "chat"} else ""
    return f"{parsed.scheme}://{host}{port}{suffix}"


def gateway_base(value=DEFAULT_GATEWAY, *, protocol):
    parsed = _parts(value)
    if (parsed.scheme != "https" or parsed.hostname not in AUTHORIZED_GATEWAY_HOSTS
            or parsed.port not in {None, 443}):
        raise ValueError("Gateway must use HTTPS on an explicitly authorized host")
    return _canonical(parsed, protocol)


def audit_base(value, *, protocol):
    """Only an explicit audit option may route to an uncredentialed local mock."""
    parsed = _parts(value)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("Audit base must use HTTP or HTTPS")
    if parsed.hostname != "localhost":
        try:
            address = ip_address(parsed.hostname)
        except ValueError:
            raise ValueError("Audit base must be localhost or a private literal IP") from None
        if not (address.is_private or address.is_loopback) or address.is_unspecified or address.is_multicast:
            raise ValueError("Audit base must be localhost or a private literal IP")
    return _canonical(parsed, protocol)

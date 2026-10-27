"""Use the existing credential-free loopback OS proxy for native CLI traffic.

This only decorates the CLI process environment. It does not edit Windows,
Git, Codex settings, authentication, model/provider, or historical results.
"""
from urllib.parse import urlsplit
from urllib.request import getproxies


def validate(value):
    parsed = urlsplit(value)
    if (parsed.scheme not in {'http', 'https'} or parsed.hostname not in {'127.0.0.1', 'localhost', '::1'}
            or parsed.username or parsed.password or parsed.path not in {'', '/'}
            or parsed.query or parsed.fragment or parsed.port is None):
        raise ValueError('Expected the existing credential-free loopback OS proxy')
    return parsed


def install(runner):
    proxy = getproxies().get('https')
    if not proxy:
        raise ValueError('No existing HTTPS proxy; do not silently fall back to the failed direct route')
    parsed = validate(proxy)
    original = runner.environment

    def environment(workspace):
        result = original(workspace)
        result.update(HTTP_PROXY=proxy, HTTPS_PROXY=proxy, http_proxy=proxy, https_proxy=proxy)
        # Keep local control connections local rather than routing them back
        # through the HTTP proxy. Preserve any existing bypass preferences.
        result.setdefault('NO_PROXY', 'localhost,127.0.0.1,::1')
        result.setdefault('no_proxy', result['NO_PROXY'])
        return result

    runner.environment = environment
    return {'source': 'existing_OS_proxy', 'scheme': parsed.scheme, 'host': parsed.hostname,
            'port': parsed.port, 'credentials_present': False, 'CLI_environment_only': True,
            'global_settings_changed': False, 'model_provider_or_auth_changed': False}

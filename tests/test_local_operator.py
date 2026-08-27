from __future__ import annotations

from smartwatch_clank.local_operator import request_is_local_operator_mutation


def _call(**overrides):
    defaults = dict(client_host="127.0.0.1", host_header="127.0.0.1:8300",
                     method="POST", path="/api/local-collection/run-all")
    defaults.update(overrides)
    return request_is_local_operator_mutation(**defaults)


def test_allows_loopback_post_on_allowlisted_route():
    assert _call() is True


def test_allows_localhost_host_header():
    assert _call(host_header="localhost:8300") is True


def test_rejects_non_post_method():
    assert _call(method="GET") is False


def test_rejects_non_loopback_client():
    assert _call(client_host="192.168.1.50") is False


def test_rejects_non_loopback_host_header():
    assert _call(host_header="example.com") is False


def test_rejects_forwarded_headers_are_never_consulted():
    # even a spoofed-looking loopback client address behind a real forwarded
    # header is irrelevant -- this function only ever receives client_host/
    # host_header, never X-Forwarded-* values, by construction.
    assert _call(client_host=None) is False


def test_rejects_path_outside_explicit_allowlist():
    assert _call(path="/api/local-collection/status") is False
    assert _call(path="/") is False
    assert _call(path="/api/qc/decide/abc") is False  # non-numeric id, must not match


def test_allows_qc_decide_and_run_one_routes():
    assert _call(path="/api/qc/decide/42") is True
    assert _call(path="/api/local-collection/run/samsung_product_catalogue") is True


def test_rejects_prefix_matching_beyond_allowlist():
    # "starts with" would be a security regression -- must not match
    assert _call(path="/api/local-collection/run-all/extra") is False
    assert _call(path="/api/qc/decide/42/extra") is False

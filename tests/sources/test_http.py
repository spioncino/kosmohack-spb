import httpx
import pytest
import respx

from evarisk.sources.http import FetchError, HttpFetcher

URL = "https://example.invalid/data.json"


def fetcher(**kw: object) -> HttpFetcher:
    return HttpFetcher(httpx.Client(), backoff_s=0.0, sleep=lambda s: None, **kw)  # type: ignore[arg-type]


@respx.mock
def test_get_returns_content_and_metadata() -> None:
    respx.get(URL).mock(return_value=httpx.Response(200, json={"a": 1}))
    r = fetcher().get(URL)
    assert r.status == 200 and r.content == b'{"a":1}' and r.content_type.startswith("application/json")
    assert r.fetched_at.tzinfo is not None


@respx.mock
def test_retries_on_5xx_then_succeeds() -> None:
    route = respx.get(URL).mock(side_effect=[httpx.Response(503), httpx.Response(200, text="ok")])
    assert fetcher(retries=3).get(URL).content == b"ok"
    assert route.call_count == 2


@respx.mock
def test_4xx_fails_without_retry() -> None:
    route = respx.get(URL).mock(return_value=httpx.Response(404))
    with pytest.raises(FetchError) as e:
        fetcher(retries=3).get(URL)
    assert e.value.status == 404 and route.call_count == 1


@respx.mock
def test_post_sends_form_and_returns_content() -> None:
    route = respx.post(URL).mock(return_value=httpx.Response(200, json=[{"a": 1}]))
    r = fetcher().post(URL, data={"identity": "u", "password": "p"})
    assert r.content == b'[{"a":1}]'
    assert b"identity=u" in route.calls[0].request.content


@respx.mock
def test_transport_error_exhausts_retries() -> None:
    route = respx.get(URL).mock(side_effect=httpx.ConnectError("down"))
    with pytest.raises(FetchError) as e:
        fetcher(retries=2).get(URL)
    assert e.value.status is None and route.call_count == 2

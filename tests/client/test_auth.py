import hashlib
import os
import typing

import pytest

from httpx import (
    URL,
    AsyncClient,
    Auth,
    DigestAuth,
    Headers,
    ProtocolError,
    Request,
    RequestBodyUnavailable,
    Response,
)
from httpx._config import TimeoutTypes
from httpx._content_streams import ContentStream, JSONStream
from httpx._dispatch.base import AsyncDispatcher


class MockDispatch(AsyncDispatcher):
    def __init__(self, auth_header: str = "", status_code: int = 200) -> None:
        self.auth_header = auth_header
        self.status_code = status_code

    async def send(
        self,
        method: bytes,
        url: URL,
        headers: Headers,
        stream: ContentStream,
        timeout: TimeoutTypes = None,
    ) -> typing.Tuple[int, str, Headers, ContentStream]:
        response_headers = Headers(
            [("www-authenticate", self.auth_header)] if self.auth_header else []
        )
        response_stream = JSONStream({"auth": headers.get("Authorization")})
        return (self.status_code, "HTTP/1.1", response_headers, response_stream)


class MockDigestAuthDispatch(AsyncDispatcher):
    def __init__(
        self,
        algorithm: str = "SHA-256",
        send_response_after_attempt: int = 1,
        qop: str = "auth",
        regenerate_nonce: bool = True,
    ) -> None:
        self.algorithm = algorithm
        self.send_response_after_attempt = send_response_after_attempt
        self.qop = qop
        self._regenerate_nonce = regenerate_nonce
        self._response_count = 0

    async def send(
        self,
        method: bytes,
        url: URL,
        headers: Headers,
        stream: ContentStream,
        timeout: TimeoutTypes = None,
    ) -> typing.Tuple[int, bytes, Headers, ContentStream]:
        if self._response_count < self.send_response_after_attempt:
            return self.challenge_send(method, url, headers, stream)

        body = JSONStream({"auth": headers.get("Authorization")})
        return 200, b"HTTP/1.1", [], body

    def challenge_send(
        self, method: bytes, url: URL, headers: Headers, stream: ContentStream,
    ) -> typing.Tuple[int, bytes, Headers, ContentStream]:
        self._response_count += 1
        nonce = (
            hashlib.sha256(os.urandom(8)).hexdigest()
            if self._regenerate_nonce
            else "ee96edced2a0b43e4869e96ebe27563f369c1205a049d06419bb51d8aeddf3d3"
        )
        challenge_data = {
            "nonce": nonce,
            "qop": self.qop,
            "opaque": (
                "ee6378f3ee14ebfd2fff54b70a91a7c9390518047f242ab2271380db0e14bda1"
            ),
            "algorithm": self.algorithm,
            "stale": "FALSE",
        }
        challenge_str = ", ".join(
            '{}="{}"'.format(key, value)
            for key, value in challenge_data.items()
            if value
        )

        headers = [
            ("www-authenticate", 'Digest realm="httpx@example.org", ' + challenge_str)
        ]
        return 401, b"HTTP/1.1", headers, ContentStream()


@pytest.mark.asyncio
async def test_basic_auth() -> None:
    url = "https://example.org/"
    auth = ("tomchristie", "password123")

    client = AsyncClient(dispatch=MockDispatch())
    response = await client.get(url, auth=auth)

    assert response.status_code == 200
    assert response.json() == {"auth": "Basic dG9tY2hyaXN0aWU6cGFzc3dvcmQxMjM="}


@pytest.mark.asyncio
async def test_basic_auth_in_url() -> None:
    url = "https://tomchristie:password123@example.org/"

    client = AsyncClient(dispatch=MockDispatch())
    response = await client.get(url)

    assert response.status_code == 200
    assert response.json() == {"auth": "Basic dG9tY2hyaXN0aWU6cGFzc3dvcmQxMjM="}


@pytest.mark.asyncio
async def test_basic_auth_on_session() -> None:
    url = "https://example.org/"
    auth = ("tomchristie", "password123")

    client = AsyncClient(dispatch=MockDispatch(), auth=auth)
    response = await client.get(url)

    assert response.status_code == 200
    assert response.json() == {"auth": "Basic dG9tY2hyaXN0aWU6cGFzc3dvcmQxMjM="}


@pytest.mark.asyncio
async def test_custom_auth() -> None:
    url = "https://example.org/"

    def auth(request: Request) -> Request:
        request.headers["Authorization"] = "Token 123"
        return request

    client = AsyncClient(dispatch=MockDispatch())
    response = await client.get(url, auth=auth)

    assert response.status_code == 200
    assert response.json() == {"auth": "Token 123"}


@pytest.mark.asyncio
async def test_netrc_auth() -> None:
    os.environ["NETRC"] = "tests/.netrc"
    url = "http://netrcexample.org"

    client = AsyncClient(dispatch=MockDispatch())
    response = await client.get(url)

    assert response.status_code == 200
    assert response.json() == {
        "auth": "Basic ZXhhbXBsZS11c2VybmFtZTpleGFtcGxlLXBhc3N3b3Jk"
    }


@pytest.mark.asyncio
async def test_auth_header_has_priority_over_netrc() -> None:
    os.environ["NETRC"] = "tests/.netrc"
    url = "http://netrcexample.org"

    client = AsyncClient(dispatch=MockDispatch())
    response = await client.get(url, headers={"Authorization": "Override"})

    assert response.status_code == 200
    assert response.json() == {"auth": "Override"}


@pytest.mark.asyncio
async def test_trust_env_auth() -> None:
    os.environ["NETRC"] = "tests/.netrc"
    url = "http://netrcexample.org"

    client = AsyncClient(dispatch=MockDispatch(), trust_env=False)
    response = await client.get(url)

    assert response.status_code == 200
    assert response.json() == {"auth": None}

    client = AsyncClient(dispatch=MockDispatch(), trust_env=True)
    response = await client.get(url)

    assert response.status_code == 200
    assert response.json() == {
        "auth": "Basic ZXhhbXBsZS11c2VybmFtZTpleGFtcGxlLXBhc3N3b3Jk"
    }


def test_auth_hidden_url() -> None:
    url = "http://example-username:example-password@example.org/"
    expected = "URL('http://example-username:[secure]@example.org/')"
    assert url == URL(url)
    assert expected == repr(URL(url))


@pytest.mark.asyncio
async def test_auth_hidden_header() -> None:
    url = "https://example.org/"
    auth = ("example-username", "example-password")

    client = AsyncClient(dispatch=MockDispatch())
    response = await client.get(url, auth=auth)

    assert "'authorization': '[secure]'" in str(response.request.headers)


@pytest.mark.asyncio
async def test_auth_invalid_type() -> None:
    url = "https://example.org/"
    client = AsyncClient(
        dispatch=MockDispatch(), auth="not a tuple, not a callable",  # type: ignore
    )
    with pytest.raises(TypeError):
        await client.get(url)


@pytest.mark.asyncio
async def test_digest_auth_returns_no_auth_if_no_digest_header_in_response() -> None:
    url = "https://example.org/"
    auth = DigestAuth(username="tomchristie", password="password123")

    client = AsyncClient(dispatch=MockDispatch())
    response = await client.get(url, auth=auth)

    assert response.status_code == 200
    assert response.json() == {"auth": None}
    assert len(response.history) == 0


@pytest.mark.asyncio
async def test_digest_auth_200_response_including_digest_auth_header() -> None:
    url = "https://example.org/"
    auth = DigestAuth(username="tomchristie", password="password123")
    auth_header = 'Digest realm="realm@host.com",qop="auth",nonce="abc",opaque="xyz"'

    client = AsyncClient(
        dispatch=MockDispatch(auth_header=auth_header, status_code=200)
    )
    response = await client.get(url, auth=auth)

    assert response.status_code == 200
    assert response.json() == {"auth": None}
    assert len(response.history) == 0


@pytest.mark.asyncio
async def test_digest_auth_401_response_without_digest_auth_header() -> None:
    url = "https://example.org/"
    auth = DigestAuth(username="tomchristie", password="password123")

    client = AsyncClient(dispatch=MockDispatch(auth_header="", status_code=401))
    response = await client.get(url, auth=auth)

    assert response.status_code == 401
    assert response.json() == {"auth": None}
    assert len(response.history) == 0


@pytest.mark.parametrize(
    "algorithm,expected_hash_length,expected_response_length",
    [
        ("MD5", 64, 32),
        ("MD5-SESS", 64, 32),
        ("SHA", 64, 40),
        ("SHA-SESS", 64, 40),
        ("SHA-256", 64, 64),
        ("SHA-256-SESS", 64, 64),
        ("SHA-512", 64, 128),
        ("SHA-512-SESS", 64, 128),
    ],
)
@pytest.mark.asyncio
async def test_digest_auth(
    algorithm: str, expected_hash_length: int, expected_response_length: int
) -> None:
    url = "https://example.org/"
    auth = DigestAuth(username="tomchristie", password="password123")

    client = AsyncClient(dispatch=MockDigestAuthDispatch(algorithm=algorithm))
    response = await client.get(url, auth=auth)

    assert response.status_code == 200
    assert len(response.history) == 1

    authorization = typing.cast(dict, response.json())["auth"]
    scheme, _, fields = authorization.partition(" ")
    assert scheme == "Digest"

    response_fields = [field.strip() for field in fields.split(",")]
    digest_data = dict(field.split("=") for field in response_fields)

    assert digest_data["username"] == '"tomchristie"'
    assert digest_data["realm"] == '"httpx@example.org"'
    assert "nonce" in digest_data
    assert digest_data["uri"] == '"/"'
    assert len(digest_data["response"]) == expected_response_length + 2  # extra quotes
    assert len(digest_data["opaque"]) == expected_hash_length + 2
    assert digest_data["algorithm"] == algorithm
    assert digest_data["qop"] == "auth"
    assert digest_data["nc"] == "00000001"
    assert len(digest_data["cnonce"]) == 16 + 2


@pytest.mark.asyncio
async def test_digest_auth_no_specified_qop() -> None:
    url = "https://example.org/"
    auth = DigestAuth(username="tomchristie", password="password123")

    client = AsyncClient(dispatch=MockDigestAuthDispatch(qop=""))
    response = await client.get(url, auth=auth)

    assert response.status_code == 200
    assert len(response.history) == 1

    authorization = typing.cast(dict, response.json())["auth"]
    scheme, _, fields = authorization.partition(" ")
    assert scheme == "Digest"

    response_fields = [field.strip() for field in fields.split(",")]
    digest_data = dict(field.split("=") for field in response_fields)

    assert "qop" not in digest_data
    assert "nc" not in digest_data
    assert "cnonce" not in digest_data
    assert digest_data["username"] == '"tomchristie"'
    assert digest_data["realm"] == '"httpx@example.org"'
    assert len(digest_data["nonce"]) == 64 + 2  # extra quotes
    assert digest_data["uri"] == '"/"'
    assert len(digest_data["response"]) == 64 + 2
    assert len(digest_data["opaque"]) == 64 + 2
    assert digest_data["algorithm"] == "SHA-256"


@pytest.mark.parametrize("qop", ("auth, auth-int", "auth,auth-int", "unknown,auth"))
@pytest.mark.asyncio
async def test_digest_auth_qop_including_spaces_and_auth_returns_auth(qop: str) -> None:
    url = "https://example.org/"
    auth = DigestAuth(username="tomchristie", password="password123")

    client = AsyncClient(dispatch=MockDigestAuthDispatch(qop=qop))
    response = await client.get(url, auth=auth)

    assert response.status_code == 200
    assert len(response.history) == 1


@pytest.mark.asyncio
async def test_digest_auth_qop_auth_int_not_implemented() -> None:
    url = "https://example.org/"
    auth = DigestAuth(username="tomchristie", password="password123")
    client = AsyncClient(dispatch=MockDigestAuthDispatch(qop="auth-int"))

    with pytest.raises(NotImplementedError):
        await client.get(url, auth=auth)


@pytest.mark.asyncio
async def test_digest_auth_qop_must_be_auth_or_auth_int() -> None:
    url = "https://example.org/"
    auth = DigestAuth(username="tomchristie", password="password123")
    client = AsyncClient(dispatch=MockDigestAuthDispatch(qop="not-auth"))

    with pytest.raises(ProtocolError):
        await client.get(url, auth=auth)


@pytest.mark.asyncio
async def test_digest_auth_incorrect_credentials() -> None:
    url = "https://example.org/"
    auth = DigestAuth(username="tomchristie", password="password123")

    client = AsyncClient(dispatch=MockDigestAuthDispatch(send_response_after_attempt=2))
    response = await client.get(url, auth=auth)

    assert response.status_code == 401
    assert len(response.history) == 1


@pytest.mark.parametrize(
    "auth_header",
    [
        'Digest realm="httpx@example.org", qop="auth"',  # missing fields
        'realm="httpx@example.org", qop="auth"',  # not starting with Digest
        'DigestZ realm="httpx@example.org", qop="auth"'
        'qop="auth,auth-int",nonce="abc",opaque="xyz"',
        'Digest realm="httpx@example.org", qop="auth,au',  # malformed fields list
    ],
)
@pytest.mark.asyncio
async def test_digest_auth_raises_protocol_error_on_malformed_header(
    auth_header: str,
) -> None:
    url = "https://example.org/"
    auth = DigestAuth(username="tomchristie", password="password123")
    client = AsyncClient(
        dispatch=MockDispatch(auth_header=auth_header, status_code=401)
    )

    with pytest.raises(ProtocolError):
        await client.get(url, auth=auth)


@pytest.mark.asyncio
async def test_auth_history() -> None:
    """
    Test that intermediate requests sent as part of an authentication flow
    are recorded in the response history.
    """

    class RepeatAuth(Auth):
        """
        A mock authentication scheme that requires clients to send
        the request a fixed number of times, and then send a last request containing
        an aggregation of nonces that the server sent in 'WWW-Authenticate' headers
        of intermediate responses.
        """

        requires_request_body = True

        def __init__(self, repeat: int):
            self.repeat = repeat

        def auth_flow(
            self, request: Request
        ) -> typing.Generator[Request, Response, None]:
            nonces = []

            for index in range(self.repeat):
                request.headers["Authorization"] = f"Repeat {index}"
                response = yield request
                nonces.append(response.headers["www-authenticate"])

            key = ".".join(nonces)
            request.headers["Authorization"] = f"Repeat {key}"
            yield request

    url = "https://example.org/"
    auth = RepeatAuth(repeat=2)
    client = AsyncClient(dispatch=MockDispatch(auth_header="abc"))

    response = await client.get(url, auth=auth)
    assert response.status_code == 200
    assert response.json() == {"auth": "Repeat abc.abc"}

    assert len(response.history) == 2
    resp1, resp2 = response.history
    assert resp1.json() == {"auth": "Repeat 0"}
    assert resp2.json() == {"auth": "Repeat 1"}

    assert len(resp2.history) == 1
    assert resp2.history == [resp1]

    assert len(resp1.history) == 0


@pytest.mark.asyncio
async def test_digest_auth_unavailable_streaming_body():
    url = "https://example.org/"
    auth = DigestAuth(username="tomchristie", password="password123")
    client = AsyncClient(dispatch=MockDispatch())

    async def streaming_body():
        yield b"Example request body"

    with pytest.raises(RequestBodyUnavailable):
        await client.post(url, data=streaming_body(), auth=auth)

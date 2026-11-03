import socket
import threading
import time

import pytest
import uvicorn

from mock_lokutor import create_mock
from vapi_lokutor_adapter.app import create_app
from vapi_lokutor_adapter.config import Settings


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _Server:
    def __init__(self, app):
        self.port = _free_port()
        self.server = uvicorn.Server(
            uvicorn.Config(app, host="127.0.0.1", port=self.port, log_level="warning", ws="websockets")
        )
        self.thread = threading.Thread(target=self.server.run, daemon=True)
        self.thread.start()
        for _ in range(100):
            if self.server.started:
                break
            time.sleep(0.05)

    def stop(self):
        self.server.should_exit = True
        self.thread.join(timeout=5)


@pytest.fixture()
def mock():
    app = create_mock()
    srv = _Server(app)
    yield app, srv
    srv.stop()


@pytest.fixture()
def adapter(mock):
    mock_app, mock_srv = mock
    settings = Settings(
        lokutor_api_key="test-key",
        lokutor_base_url=f"http://127.0.0.1:{mock_srv.port}",
        adapter_secret="s3cret",
    )
    srv = _Server(create_app(settings))
    yield srv, mock_app
    srv.stop()

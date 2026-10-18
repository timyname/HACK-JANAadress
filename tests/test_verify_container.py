from http.client import RemoteDisconnected
from unittest.mock import patch

from tools.verify_container import read_health


def test_cold_start_remote_disconnect_is_retryable():
    with patch(
        "tools.verify_container.urlopen",
        side_effect=RemoteDisconnected("container is still starting"),
    ):
        assert read_health() is None

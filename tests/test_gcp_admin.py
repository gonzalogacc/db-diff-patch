from unittest.mock import MagicMock, patch

import httplib2
import pytest
from google.auth.exceptions import DefaultCredentialsError
from googleapiclient.errors import HttpError

from src.utils.gcp_admin import GCPAdminError, InstanceInfo, get_instance, list_instances


@pytest.fixture
def mock_client():
    """Patch google.auth.default and the discovery client, returning the
    mocked sqladmin client for tests to configure."""
    with patch("google.auth.default", return_value=(MagicMock(), "test-project")):
        with patch("src.utils.gcp_admin.build") as mock_build:
            client = MagicMock()
            mock_build.return_value = client
            yield client


def _instance_resource(name, region="us-central1", private_ip="10.0.0.1", public_ip="34.0.0.1", labels=None):
    ip_addresses = []
    if private_ip:
        ip_addresses.append({"type": "PRIVATE", "ipAddress": private_ip})
    if public_ip:
        ip_addresses.append({"type": "PRIMARY", "ipAddress": public_ip})
    return {
        "name": name,
        "region": region,
        "databaseVersion": "POSTGRES_15",
        "ipAddresses": ip_addresses,
        "settings": {"userLabels": labels or {}},
    }


def test_build_client_raises_clear_error_without_credentials():
    with patch("google.auth.default", side_effect=DefaultCredentialsError("no creds")):
        with pytest.raises(GCPAdminError, match="application-default login"):
            list_instances("test-project")


def test_list_instances_parses_ips_and_labels(mock_client):
    mock_client.instances.return_value.list.return_value.execute.return_value = {
        "items": [_instance_resource("db-1", labels={"env": "prod"})]
    }
    mock_client.instances.return_value.list_next.return_value = None

    instances = list_instances("test-project")

    assert instances == [
        InstanceInfo(
            name="db-1",
            region="us-central1",
            database_version="POSTGRES_15",
            private_ip="10.0.0.1",
            public_ip="34.0.0.1",
            labels={"env": "prod"},
        )
    ]


def test_list_instances_follows_pagination(mock_client):
    page_1_response = {"items": [_instance_resource("db-1")]}
    page_2_response = {"items": [_instance_resource("db-2")]}

    page_2_request = MagicMock()
    page_2_request.execute.return_value = page_2_response

    mock_client.instances.return_value.list.return_value.execute.return_value = page_1_response
    mock_client.instances.return_value.list_next.side_effect = [page_2_request, None]

    instances = list_instances("test-project")

    assert [i.name for i in instances] == ["db-1", "db-2"]


def test_get_instance_wraps_http_error(mock_client):
    response = httplib2.Response({"status": 404})
    mock_client.instances.return_value.get.return_value.execute.side_effect = HttpError(
        response, b'{"error": "not found"}'
    )

    with pytest.raises(GCPAdminError, match="Could not find Cloud SQL instance"):
        get_instance("test-project", "missing-instance")

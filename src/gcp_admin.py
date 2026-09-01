"""Thin wrapper over the Cloud SQL Admin API.

Knows nothing about branches or the Auth Proxy - just instance discovery,
for `list-databases` and for resolving where the Auth Proxy should tunnel.
"""

from dataclasses import dataclass, field

import google.auth
from google.auth.exceptions import DefaultCredentialsError
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

_SCOPES = ["https://www.googleapis.com/auth/sqlservice.admin"]


class GCPAdminError(Exception):
    """Raised for credential problems or a failed Admin API call."""


@dataclass
class InstanceInfo:
    name: str
    region: str
    database_version: str
    private_ip: str | None
    public_ip: str | None
    labels: dict[str, str] = field(default_factory=dict)


def _build_client():
    try:
        credentials, _ = google.auth.default(scopes=_SCOPES)
    except DefaultCredentialsError as exc:
        raise GCPAdminError(
            "No Google Application Default Credentials found. "
            "Run `gcloud auth application-default login` first."
        ) from exc
    return build("sqladmin", "v1beta4", credentials=credentials)


def _instance_info_from_resource(resource: dict) -> InstanceInfo:
    private_ip = None
    public_ip = None
    for ip in resource.get("ipAddresses", []):
        if ip.get("type") == "PRIVATE":
            private_ip = ip.get("ipAddress")
        elif ip.get("type") == "PRIMARY":
            public_ip = ip.get("ipAddress")

    return InstanceInfo(
        name=resource["name"],
        region=resource.get("region", ""),
        database_version=resource.get("databaseVersion", ""),
        private_ip=private_ip,
        public_ip=public_ip,
        labels=resource.get("settings", {}).get("userLabels", {}) or {},
    )


def list_instances(project: str) -> list[InstanceInfo]:
    client = _build_client()
    instances = []
    request = client.instances().list(project=project)
    while request is not None:
        response = request.execute()
        for resource in response.get("items", []):
            instances.append(_instance_info_from_resource(resource))
        request = client.instances().list_next(previous_request=request, previous_response=response)
    return instances


def get_instance(project: str, name: str) -> InstanceInfo:
    client = _build_client()
    try:
        resource = client.instances().get(project=project, instance=name).execute()
    except HttpError as exc:
        raise GCPAdminError(f"Could not find Cloud SQL instance '{name}' in project '{project}': {exc}") from exc
    return _instance_info_from_resource(resource)

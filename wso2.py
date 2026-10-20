import asyncio
import os
import uuid
from pathlib import Path

import httpx

NAMESPACE = uuid.UUID("6ba7b811-9dad-11d1-80b4-00c04fd430c8")
ROOT = Path(__file__).resolve().parent
SCOPES = (
    "apim:api_create apim:api_publish apim:api_view apim:api_manage "
    "apim:subscribe apim:app_manage apim:sub_manage apim:api_import_export "
    "apim:mediation_policy_create apim:mediation_policy_view"
)
SHORT_SCOPES = "apim:api_create apim:api_publish apim:api_view apim:subscribe apim:app_manage"


def user_uuid(username):
    return str(uuid.uuid5(NAMESPACE, username))


def endpoint_config(ingest_url):
    return {
        "endpoint_type": "http",
        "production_endpoints": {"url": ingest_url},
        "sandbox_endpoints": {"url": ingest_url},
    }


def additional_properties(ingest_url, context):
    return {
        "name": "Desk",
        "version": "v1",
        "context": context,
        "endpointConfig": endpoint_config(ingest_url),
    }


def _target(operation):
    return operation.get("target") or operation.get("uriTemplate")


def _verb(operation):
    return (operation.get("verb") or operation.get("httpVerb") or "").upper()


def with_user_policy(api_body):
    for operation in api_body.get("operations", []):
        if _verb(operation) == "POST" and _target(operation) in {"/events", "/incidents"}:
            operation["authType"] = "Application & Application User"
            policies = operation.setdefault(
                "operationPolicies",
                {"request": [], "response": [], "fault": []},
            )
            request_flow = policies.setdefault("request", [])
            if not any(item.get("policyName") == "userUuid" for item in request_flow):
                request_flow.append(
                    {
                        "policyName": "userUuid",
                        "policyVersion": "v1",
                        "policyType": "common",
                        "parameters": {},
                    }
                )
    return api_body


async def provision():
    host = os.environ.get("WSO2_HOST", "https://localhost:9443").rstrip("/")
    admin = os.environ.get("WSO2_ADMIN_USER", "admin")
    password = os.environ.get("WSO2_ADMIN_PASSWORD", "admin")
    ingest_url = os.environ.get("INGEST_URL", "http://ingest:5000").rstrip("/")
    context = os.environ.get("API_CONTEXT", "/desk/v1")
    alice_password = os.environ["WSO2_ALICE_PASSWORD"]
    bob_password = os.environ["WSO2_BOB_PASSWORD"]
    async with httpx.AsyncClient(verify=False, timeout=60) as client:
        await _wait(client, host)
        await _user(client, host, admin, password, "alice", alice_password)
        await _user(client, host, admin, password, "bob", bob_password)
        token = await _token(client, host, admin, password)
        await _policy(client, host, token)
        api_id = await _api(client, host, token, ingest_url, context)
        await _publish(client, host, token, api_id)
        await _subscribe(client, host, token, api_id)
    print(f"alice {user_uuid('alice')}")
    print(f"bob {user_uuid('bob')}")
    print(f"gateway https://localhost:8243{context}")


async def _wait(client, host):
    last = None
    for _ in range(90):
        try:
            response = await client.get(f"{host}/carbon/admin/login.jsp")
            if response.status_code < 500:
                return
        except httpx.HTTPError as exc:
            last = exc
        await asyncio.sleep(5)
    raise RuntimeError(f"WSO2 did not start: {last}")


async def _user(client, host, admin, password, username, user_password):
    response = await client.post(
        f"{host}/scim2/Users",
        auth=(admin, password),
        json={
            "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
            "userName": username,
            "password": user_password,
            "name": {"givenName": username.title(), "familyName": "Desk"},
            "emails": [{"value": f"{username}@desk.local", "primary": True}],
        },
    )
    if response.status_code in {200, 201, 409}:
        return
    if response.status_code == 400 and "exist" in response.text.lower():
        return
    response.raise_for_status()


async def _token(client, host, admin, password):
    registered = await client.post(
        f"{host}/client-registration/v0.17/register",
        auth=(admin, password),
        json={
            "callbackUrl": "https://localhost/callback",
            "clientName": "desk-provisioner",
            "owner": admin,
            "grantType": "client_credentials password",
            "saasApp": True,
        },
    )
    registered.raise_for_status()
    body = registered.json()
    client_id = body["clientId"]
    client_secret = body["clientSecret"]
    last = None
    for scopes in (SCOPES, SHORT_SCOPES):
        response = await client.post(
            f"{host}/oauth2/token",
            auth=(client_id, client_secret),
            data={"grant_type": "client_credentials", "scope": scopes},
        )
        if response.status_code == 200:
            return response.json()["access_token"]
        last = response.text
    raise RuntimeError(last)


async def _policy(client, host, token):
    spec = {
        "category": "Mediation",
        "name": "userUuid",
        "version": "v1",
        "displayName": "User UUID",
        "description": "Sets X-User-Uuid from the authenticated username",
        "applicableFlows": ["request"],
        "supportedGateways": ["Synapse"],
        "supportedApiTypes": ["HTTP"],
        "policyAttributes": [],
    }
    sequence = (ROOT / "mediation.xml").read_text()
    response = await client.post(
        f"{host}/api/am/publisher/v4/operation-policies",
        headers={"Authorization": f"Bearer {token}"},
        files={
            "policySpecFile": ("policy-definition.json", __import__("json").dumps(spec), "application/json"),
            "synapsePolicyDefinitionFile": ("userUuid.xml", sequence, "text/xml"),
        },
    )
    if response.status_code in {200, 201, 409}:
        return
    if response.status_code == 400 and "already" in response.text.lower():
        return
    response.raise_for_status()


async def _api(client, host, token, ingest_url, context):
    headers = {"Authorization": f"Bearer {token}"}
    found = await client.get(
        f"{host}/api/am/publisher/v4/apis",
        headers=headers,
        params={"query": "name:Desk"},
    )
    found.raise_for_status()
    listed = found.json().get("list") or found.json().get("list", [])
    if isinstance(found.json(), dict):
        listed = found.json().get("list") or []
    if listed:
        return listed[0]["id"]
    extra = additional_properties(ingest_url, context)
    response = await client.post(
        f"{host}/api/am/publisher/v4/apis/import-openapi",
        headers=headers,
        files={"file": ("openapi.yaml", (ROOT / "openapi.yaml").read_bytes(), "application/yaml")},
        data={"additionalProperties": __import__("json").dumps(extra)},
    )
    response.raise_for_status()
    api_id = response.json()["id"]
    current = await client.get(f"{host}/api/am/publisher/v4/apis/{api_id}", headers=headers)
    current.raise_for_status()
    updated = with_user_policy(current.json())
    put = await client.put(
        f"{host}/api/am/publisher/v4/apis/{api_id}",
        headers={**headers, "Content-Type": "application/json"},
        json=updated,
    )
    put.raise_for_status()
    return api_id


async def _publish(client, host, token, api_id):
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    revision = await client.post(
        f"{host}/api/am/publisher/v4/apis/{api_id}/revisions",
        headers=headers,
        json={"description": "desk"},
    )
    revision.raise_for_status()
    revision_id = revision.json().get("id")
    deploy = await client.post(
        f"{host}/api/am/publisher/v4/apis/{api_id}/deploy-revision",
        headers=headers,
        params={"revisionId": revision_id},
        json=[{"name": "Default", "vhost": "localhost", "displayOnDevportal": True}],
    )
    deploy.raise_for_status()
    lifecycle = await client.post(
        f"{host}/api/am/publisher/v4/apis/change-lifecycle",
        headers={"Authorization": f"Bearer {token}"},
        params={"action": "Publish", "apiId": api_id},
    )
    lifecycle.raise_for_status()


async def _subscribe(client, host, token, api_id):
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    created = await client.post(
        f"{host}/api/am/devportal/v3/applications",
        headers=headers,
        json={"name": "desk", "throttlingPolicy": "Unlimited", "description": "desk"},
    )
    if created.status_code == 409:
        return
    created.raise_for_status()
    app_id = created.json()["applicationId"]
    keys = await client.post(
        f"{host}/api/am/devportal/v3/applications/{app_id}/generate-keys",
        headers=headers,
        json={
            "keyType": "PRODUCTION",
            "grantTypesToBeSupported": ["password", "client_credentials", "refresh_token"],
            "callbackUrl": "https://localhost/callback",
        },
    )
    keys.raise_for_status()
    subscription = await client.post(
        f"{host}/api/am/devportal/v3/subscriptions",
        headers=headers,
        json={"applicationId": app_id, "apiId": api_id, "throttlingPolicy": "Unlimited"},
    )
    subscription.raise_for_status()
    body = keys.json()
    print(f"client_id {body.get('consumerKey')}")
    print(f"client_secret {body.get('consumerSecret')}")


if __name__ == "__main__":
    asyncio.run(provision())

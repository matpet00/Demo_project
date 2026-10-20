import asyncio
import json
import os
import socket
import uuid
from pathlib import Path

import httpx

NAMESPACE = uuid.UUID("6ba7b811-9dad-11d1-80b4-00c04fd430c8")
ROOT = Path(__file__).resolve().parent
SCOPES = (
    "apim:api_create apim:api_publish apim:api_view apim:api_manage "
    "apim:subscribe apim:app_manage apim:sub_manage apim:api_import_export "
    "apim:common_operation_policy_manage apim:common_operation_policy_view "
    "apim:api_operation_policy_manage apim:api_operation_policy_view"
)
SHORT_SCOPES = (
    "apim:api_create apim:api_publish apim:api_view apim:subscribe apim:app_manage "
    "apim:common_operation_policy_manage apim:api_operation_policy_manage"
)


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
        if _verb(operation) == "POST" and _target(operation) == "/events":
            operation["authType"] = "Application & Application User"
            policies = operation.setdefault(
                "operationPolicies",
                {"request": [], "response": [], "fault": []},
            )
            request_flow = [
                item
                for item in policies.setdefault("request", [])
                if item.get("policyName") != "userUuid"
            ]
            request_flow.append(
                {
                        "policyName": "userUuid",
                        "policyVersion": "v4",
                    "policyType": "common",
                    "parameters": {},
                }
            )
            policies["request"] = request_flow
    return api_body


async def provision():
    host = os.environ.get("WSO2_HOST", "https://localhost:9443").rstrip("/")
    admin = os.environ.get("WSO2_ADMIN_USER", "admin")
    password = os.environ.get("WSO2_ADMIN_PASSWORD", "admin")
    ingest_url = os.environ.get("INGEST_URL", "http://ingest:5000").rstrip("/")
    context = os.environ.get("API_CONTEXT", "/desk")
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
        await _subscribe(client, host, admin, password, "alice", alice_password, api_id)
    print(f"alice {user_uuid('alice')}")
    print(f"bob {user_uuid('bob')}")
    print(f"gateway https://localhost:8243{context}/v1")


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


def _step(name, response):
    print(f"{name} {response.status_code}", flush=True)
    if response.status_code >= 400:
        print(f"  {response.text.replace(chr(10), ' ')[:220]}", flush=True)


def _policy_template(sequence_xml):
    start = sequence_xml.find("<sequence")
    return sequence_xml[start:] if start >= 0 else sequence_xml


def _soap(inner):
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" '
        'xmlns:ser="http://service.ws.um.carbon.wso2.org">'
        f"<soapenv:Header/><soapenv:Body>{inner}</soapenv:Body></soapenv:Envelope>"
    )


async def _soap_call(client, host, admin, password, action, inner):
    response = await client.post(
        f"{host}/services/RemoteUserStoreManagerService",
        auth=(admin, password),
        content=_soap(inner).encode(),
        headers={"Content-Type": "text/xml; charset=utf-8", "SOAPAction": f"urn:{action}"},
    )
    if response.status_code >= 400 or "Fault" in response.text:
        raise RuntimeError(response.text[:500])
    return response.text


async def _user(client, host, admin, password, username, user_password):
    from xml.sax.saxutils import escape

    safe_name = escape(username)
    safe_password = escape(user_password)
    existing = await _soap_call(
        client,
        host,
        admin,
        password,
        "isExistingUser",
        f"<ser:isExistingUser><ser:userName>{safe_name}</ser:userName></ser:isExistingUser>",
    )
    if ">true<" in existing:
        return
    await _soap_call(
        client,
        host,
        admin,
        password,
        "addUser",
        (
            "<ser:addUser>"
            f"<ser:userName>{safe_name}</ser:userName>"
            f"<ser:credential>{safe_password}</ser:credential>"
            "<ser:roleList>Internal/subscriber</ser:roleList>"
            "<ser:roleList>Internal/everyone</ser:roleList>"
            "<ser:profileName>default</ser:profileName>"
            "<ser:requirePasswordChange>false</ser:requirePasswordChange>"
            "</ser:addUser>"
        ),
    )


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
        granted = response.json().get("scope", "") if response.status_code == 200 else ""
        if "apim:api_publish" in granted and "apim:common_operation_policy_manage" in granted:
            return response.json()["access_token"]
        last = response.text
    raise RuntimeError(last)


async def _policy(client, host, token):
    spec = {
        "category": "Mediation",
        "name": "userUuid",
        "version": "v4",
        "displayName": "User UUID",
        "description": "Sets X-User-Uuid from the authenticated username",
        "applicableFlows": ["request"],
        "supportedGateways": ["Synapse"],
        "supportedApiTypes": ["HTTP"],
        "policyAttributes": [],
    }
    sequence = _policy_template((ROOT / "mediation.xml").read_text())
    existing = await client.get(
        f"{host}/api/am/publisher/v4/operation-policies",
        headers={"Authorization": f"Bearer {token}"},
    )
    if existing.status_code == 200 and any(
        item.get("name") == "userUuid" and item.get("version") == "v4"
        for item in existing.json().get("list", [])
    ):
        return
    response = await client.post(
        f"{host}/api/am/publisher/v4/operation-policies",
        headers={"Authorization": f"Bearer {token}"},
        files={
            "policySpecFile": ("policy-definition.json", json.dumps(spec), "application/json"),
            "synapsePolicyDefinitionFile": ("userUuid.xml", sequence, "text/xml"),
        },
    )
    if response.status_code in {200, 201, 409}:
        return
    if response.status_code == 400 and any(word in response.text.lower() for word in ("already", "exist", "duplicate")):
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
        api_id = listed[0]["id"]
    else:
        extra = additional_properties(ingest_url, context)
        response = await client.post(
            f"{host}/api/am/publisher/v4/apis/import-openapi",
            headers=headers,
            files={"file": ("openapi.yaml", (ROOT / "openapi.yaml").read_bytes(), "application/yaml")},
            data={"additionalProperties": json.dumps(extra)},
        )
        response.raise_for_status()
        api_id = response.json()["id"]
    current = await client.get(f"{host}/api/am/publisher/v4/apis/{api_id}", headers=headers)
    current.raise_for_status()
    updated = with_user_policy(current.json())
    updated["policies"] = ["Gold", "Unlimited", "Bronze", "Silver"]
    put = await client.put(
        f"{host}/api/am/publisher/v4/apis/{api_id}",
        headers={**headers, "Content-Type": "application/json"},
        json=updated,
    )
    put.raise_for_status()
    return api_id


async def _publish(client, host, token, api_id):
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    current = await client.get(f"{host}/api/am/publisher/v4/apis/{api_id}", headers=headers)
    current.raise_for_status()
    already_published = current.json().get("lifeCycleStatus") == "PUBLISHED"
    revision = await client.post(
        f"{host}/api/am/publisher/v4/apis/{api_id}/revisions",
        headers=headers,
        json={"description": "desk"},
    )
    if revision.status_code == 400 and "Maximum number of revisions" in revision.text:
        listed = await client.get(f"{host}/api/am/publisher/v4/apis/{api_id}/revisions", headers=headers)
        listed.raise_for_status()
        idle = [item for item in listed.json().get("list", []) if not item.get("deploymentInfo")]
        if idle:
            removed = await client.delete(
                f"{host}/api/am/publisher/v4/apis/{api_id}/revisions/{idle[0]['id']}",
                headers=headers,
            )
            _step("wso2 revision delete", removed)
            revision = await client.post(
                f"{host}/api/am/publisher/v4/apis/{api_id}/revisions",
                headers=headers,
                json={"description": "desk"},
            )
    _step("wso2 revision", revision)
    revision.raise_for_status()
    revision_id = revision.json().get("id")
    deploy = await client.post(
        f"{host}/api/am/publisher/v4/apis/{api_id}/deploy-revision",
        headers=headers,
        params={"revisionId": revision_id},
        json=[{"name": "Default", "vhost": "localhost", "displayOnDevportal": True}],
    )
    deploy.raise_for_status()
    if already_published:
        return
    lifecycle = await client.post(
        f"{host}/api/am/publisher/v4/apis/change-lifecycle",
        headers={"Authorization": f"Bearer {token}"},
        params={"action": "Publish", "apiId": api_id},
    )
    lifecycle.raise_for_status()


async def _subscriber_token(client, host, admin, password, username, user_password):
    registered = await client.post(
        f"{host}/client-registration/v0.17/register",
        auth=(admin, password),
        json={
            "callbackUrl": "https://localhost/callback",
            "clientName": f"desk-subscriber-{username}",
            "owner": admin,
            "grantType": "password client_credentials",
            "saasApp": True,
        },
    )
    _step("wso2 subscriber client", registered)
    registered.raise_for_status()
    body = registered.json()
    response = await client.post(
        f"{host}/oauth2/token",
        auth=(body["clientId"], body["clientSecret"]),
        data={
            "grant_type": "password",
            "username": username,
            "password": user_password,
            "scope": "apim:subscribe apim:app_manage apim:sub_manage",
        },
    )
    _step("wso2 subscriber token", response)
    response.raise_for_status()
    return response.json()["access_token"]


async def _subscribe(client, host, admin, password, username, user_password, api_id):
    token = await _subscriber_token(client, host, admin, password, username, user_password)
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    created = await client.post(
        f"{host}/api/am/devportal/v3/applications",
        headers=headers,
        json={"name": "desk", "throttlingPolicy": "Unlimited", "description": "desk", "tokenType": "JWT"},
    )
    _step("wso2 application", created)
    if created.status_code == 409:
        found = await client.get(
            f"{host}/api/am/devportal/v3/applications",
            headers=headers,
            params={"query": "desk"},
        )
        found.raise_for_status()
        matches = [item for item in found.json().get("list", []) if item.get("name") == "desk"]
        if not matches:
            created.raise_for_status()
        app_id = matches[0]["applicationId"]
    else:
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
    subscription = await client.post(
        f"{host}/api/am/devportal/v3/subscriptions",
        headers=headers,
        json={"applicationId": app_id, "apiId": api_id, "throttlingPolicy": "Gold"},
    )
    _step("wso2 subscription", subscription)
    if subscription.status_code >= 400 and "already" not in subscription.text.lower():
        subscription.raise_for_status()
    _step("wso2 keys", keys)
    if keys.status_code < 300 and keys.json().get("consumerKey"):
        print("wso2 keys issued", flush=True)

TABLE = "u_itil_event"
SERVICE_ID = "desk_events"
PATH = "/api/now/desk_events/event"

FIELDS = (
    ("source", "Source", "string", "100"),
    ("node", "Node", "string", "100"),
    ("type", "Type", "string", "100"),
    ("resource", "Resource", "string", "100"),
    ("metric_name", "Metric name", "string", "100"),
    ("event_class", "Event class", "string", "40"),
    ("severity", "Severity", "integer", "40"),
    ("description", "Description", "string", "4000"),
    ("additional_info", "Additional information", "string", "4000"),
    ("message_key", "Message key", "string", "255"),
    ("time_of_event", "Time of event", "glide_date_time", "40"),
    ("state", "State", "string", "40"),
    ("event_number", "Event number", "string", "40"),
)


def load_env():
    path = Path(__file__).resolve().parent / ".env"
    if not path.exists():
        raise SystemExit("Missing .env")
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ[key.strip()] = value.strip().strip('"').strip("'")


def client():
    base = os.environ["SERVICENOW_BASE_URL"].rstrip("/")
    auth = (os.environ["SERVICENOW_USER"], os.environ["SERVICENOW_PASSWORD"])
    return httpx.Client(base_url=base, auth=auth, timeout=40, headers={"Accept": "application/json"})


def ensure_table(http):
    found = http.get(
        "/api/now/table/sys_db_object",
        params={"sysparm_query": f"name={TABLE}", "sysparm_fields": "sys_id,name", "sysparm_limit": "1"},
    )
    _step("snow table lookup", found)
    found.raise_for_status()
    rows = found.json().get("result") or []
    if rows:
        return rows[0]["sys_id"]
    created = http.post(
        "/api/now/table/sys_db_object",
        json={
            "label": "ITIL Event",
            "name": TABLE,
            "super_class": "",
            "is_extendable": "false",
            "create_access_controls": "false",
            "create_module": "true",
            "create_access": "true",
        },
    )
    _step("snow table create", created)
    created.raise_for_status()
    return created.json()["result"]["sys_id"]


def ensure_fields(http):
    existing = http.get(
        "/api/now/table/sys_dictionary",
        params={
            "sysparm_query": f"name={TABLE}^elementISNOTEMPTY",
            "sysparm_fields": "element",
            "sysparm_limit": "100",
        },
    )
    _step("snow fields lookup", existing)
    existing.raise_for_status()
    present = {row["element"] for row in existing.json().get("result") or []}
    for element, label, internal_type, length in FIELDS:
        if element in present or f"u_{element}" in present:
            continue
        created = http.post(
            "/api/now/table/sys_dictionary",
            json={
                "name": TABLE,
                "element": element,
                "column_label": label,
                "internal_type": internal_type,
                "max_length": length,
                "active": "true",
            },
        )
        _step(f"snow field {element}", created)
        created.raise_for_status()
    listed = http.get(
        "/api/now/table/sys_dictionary",
        params={
            "sysparm_query": f"name={TABLE}^elementISNOTEMPTY",
            "sysparm_fields": "element",
            "sysparm_limit": "100",
        },
    )
    listed.raise_for_status()
    return {row["element"] for row in listed.json().get("result") or []}


def column(present, logical):
    if logical in present:
        return logical
    if f"u_{logical}" in present:
        return f"u_{logical}"
    raise SystemExit(f"Column {logical} was not created on {TABLE}")


def operation_script(present):
    assignments = []
    for logical in (
        "source",
        "node",
        "type",
        "resource",
        "metric_name",
        "event_class",
        "severity",
        "description",
        "additional_info",
        "message_key",
    ):
        assignments.append(f"        gr.{column(present, logical)} = rec.{logical} || '';")
    number = column(present, "event_number")
    state = column(present, "state")
    when = column(present, "time_of_event")
    lines = "\n".join(assignments)
    return f"""(function process(request, response) {{
    var body = request.body.data || {{}};
    var records = body.records || [body];
    var created = [];
    for (var i = 0; i < records.length; i++) {{
        var rec = records[i] || {{}};
        var gr = new GlideRecord('{TABLE}');
        gr.initialize();
{lines}
        gr.{state} = 'Ready';
        gr.{when} = new GlideDateTime();
        gr.{number} = 'EM' + new GlideDateTime().getNumericValue();
        var id = gr.insert();
        if (!id) {{
            response.setStatus(400);
            response.setBody({{error: {{message: 'insert failed', detail: gr.getLastErrorMessage()}}}});
            return;
        }}
        created.push({{sys_id: String(id), number: gr.getValue('{number}')}});
    }}
    response.setStatus(201);
    response.setBody({{result: created}});
}})(request, response);
"""


def ensure_api(http, present):
    found = http.get(
        "/api/now/table/sys_ws_definition",
        params={"sysparm_query": f"service_id={SERVICE_ID}", "sysparm_fields": "sys_id,base_uri", "sysparm_limit": "1"},
    )
    _step("snow api lookup", found)
    found.raise_for_status()
    rows = found.json().get("result") or []
    if rows:
        definition = rows[0]["sys_id"]
    else:
        created = http.post(
            "/api/now/table/sys_ws_definition",
            json={
                "name": "Desk Events",
                "service_id": SERVICE_ID,
                "namespace": "now",
                "short_description": "Insert an ITIL event",
                "active": "true",
            },
        )
        _step("snow api create", created)
        created.raise_for_status()
        definition = created.json()["result"]["sys_id"]
    operations = http.get(
        "/api/now/table/sys_ws_operation",
        params={
            "sysparm_query": f"web_service_definition={definition}^http_method=POST",
            "sysparm_fields": "sys_id",
            "sysparm_limit": "1",
        },
    )
    _step("snow operation lookup", operations)
    operations.raise_for_status()
    script = operation_script(present)
    payload = {
        "name": "Insert ITIL event",
        "web_service_definition": definition,
        "http_method": "POST",
        "relative_path": "/event",
        "active": "true",
        "consumes": "application/json",
        "produces": "application/json",
        "operation_script": script,
        "requires_authentication": "true",
        "requires_acl_authorization": "false",
    }
    existing = operations.json().get("result") or []
    if existing:
        updated = http.patch(f"/api/now/table/sys_ws_operation/{existing[0]['sys_id']}", json=payload)
        _step("snow operation update", updated)
        updated.raise_for_status()
    else:
        created = http.post("/api/now/table/sys_ws_operation", json=payload)
        _step("snow operation create", created)
        created.raise_for_status()
    return PATH


def prepare_service_now():
    with client() as http:
        ensure_table(http)
        present = ensure_fields(http)
        path = ensure_api(http, present)
    print(f"snow events {path}", flush=True)
    print(f"snow table {TABLE}", flush=True)


def _wso2_host():
    host = os.environ.get("WSO2_HOST", "https://127.0.0.1:9443").rstrip("/")
    if "://wso2" not in host:
        return host
    try:
        socket.getaddrinfo("wso2", 9443)
        return host
    except socket.gaierror:
        return "https://127.0.0.1:9443"


def register_bob():
    username = "bob"
    user = user_uuid(username)
    url = os.environ.get("DESK_URL", "http://127.0.0.1:8080").rstrip("/") + "/add_spammer"
    headers = {"Accept": "application/json"}
    api_key = os.environ.get("API_KEY", "")
    if api_key:
        headers["X-API-Key"] = api_key
    response = httpx.post(
        url,
        json={"uuid": user, "username": username},
        headers=headers,
        timeout=15,
    )
    _step("flask add_spammer", response)
    response.raise_for_status()
    print(f"{username} {user}", flush=True)


def main():
    load_env()
    os.environ["WSO2_HOST"] = _wso2_host()
    prepare_service_now()
    asyncio.run(provision())
    register_bob()


if __name__ == "__main__":
    main()

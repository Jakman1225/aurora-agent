"""Programmatic evidence APIs. Human approval uses the separate browser session."""
from dataclasses import dataclass
from urllib.parse import quote, urlencode, urlsplit
from jsonschema import Draft202012Validator

from .contract import canonical, parse, reference, schema, validate, raw_digest, timestamp, Rejected
from .errors import ProtocolError, SealPending, UncertainExecution, UnsupportedProfile, api_error
from .transport import HTTPS

def segment(value):
    if type(value) is not str or not 0 < len(value) <= 200:
        raise ProtocolError("IDENTIFIER_INVALID")
    return quote(value, safe="")

def projection(value):
    definition = {"$ref": "#/$defs/Projection", "$defs": schema()["$defs"]}
    if next(Draft202012Validator(definition).iter_errors(value), None):
        raise ProtocolError("PROJECTION_SCHEMA_INVALID")
    # The SDK applies the frozen shape; it does not claim to verify the evidence.
    canonical(value)
    timestamp(value["reference_time"])
    return value

def json_response(response):
    try:
        value = parse(response.body)
    except Rejected as exc:
        raise ProtocolError("RESPONSE_JSON_INVALID", status=response.status) from exc
    if type(value) is not dict:
        raise ProtocolError("RESPONSE_OBJECT_REQUIRED")
    if response.status >= 400:
        code = value.get("error", value.get("detail", "STAGE_G_REQUEST_FAILED"))
        if type(code) is not str:
            code = "STAGE_G_REQUEST_FAILED"
        raise api_error(code, response.status)
    if response.status != 200:
        raise ProtocolError("RESPONSE_STATUS_INVALID", status=response.status)
    if response.headers.get("content-type", "").split(";")[0] != "application/json":
        raise ProtocolError("RESPONSE_CONTENT_TYPE_INVALID")
    return value

@dataclass(frozen=True)
class BundleExport:
    data: bytes
    reference_time: str
    trust_selection_digest: str
    projection_sha256: str

    def compare_projection(self, offline_projection):
        """Compare the exact standalone G-4 result; no server trust inference."""
        projection(offline_projection)
        return (offline_projection["reference_time"] == self.reference_time and
                offline_projection["trust_policy_digest"] == self.trust_selection_digest and
                raw_digest(canonical(offline_projection)) == self.projection_sha256)

class StageGClient:
    def __init__(self, base_url, api_key, tenant_id, *, transport=None, ca_file=None):
        if not api_key or any(c in api_key for c in "\r\n"):
            raise ProtocolError("API_KEY_REQUIRED")
        segment(tenant_id)
        self.tenant_id = tenant_id
        self._headers = {"Authorization": "Bearer " + api_key}
        self.transport = transport or HTTPS(base_url, ca_file=ca_file)

    def _call(self, method, path, payload=None):
        return json_response(self.transport.request(method, path, payload=payload, headers=self._headers))

    def _artifact(self, value, kind=None):
        try:
            validate(value, kind)
        except Rejected as exc:
            if exc.code == "UNSUPPORTED_CONTRACT":
                raise UnsupportedProfile(exc.code) from exc
            raise ProtocolError(exc.code) from exc
        if value["tenant_id"] != self.tenant_id:
            raise ProtocolError("TENANT_MISMATCH")
        return value

    def append_evidence(self, value, *, action_id, idempotency_key, supporting_artifacts=()):
        self._artifact(value)
        for a in supporting_artifacts:
            self._artifact(a)
        return self._call("POST", "/evidence", dict(artifact=value, action_id=action_id,
            idempotency_key=idempotency_key, supporting_artifacts=list(supporting_artifacts)))

    def register_intent(self, intent, *, idempotency_key, supporting_artifacts=()):
        self._artifact(intent, "intent")
        return self.append_evidence(intent, action_id=intent["body"]["action_id"],
            idempotency_key=idempotency_key, supporting_artifacts=supporting_artifacts)

    def artifact(self, artifact_id):
        return self._artifact(self._call("GET", "/artifacts/" + segment(artifact_id)))

    def approval_status(self, action_id):
        return self._call("GET", "/actions/" + segment(action_id) + "/approval")

    def review_url(self, frontend_origin, action_id):
        """Credential-free deep link to the signed-in browser workspace."""
        p = urlsplit(frontend_origin)
        if (p.scheme != "https" or not p.hostname or p.username or p.password or
                p.path not in ("", "/") or p.query or p.fragment):
            raise ProtocolError("FRONTEND_ORIGIN_INVALID")
        segment(action_id)
        return frontend_origin.rstrip("/") + "/app/actions?" + urlencode({
            "organization_id": self.tenant_id, "action_id": action_id})

    def control_snapshot(self, artifacts, binding_ref):
        for a in artifacts:
            self._artifact(a)
        return self._call("POST", "/control-snapshot", dict(artifacts=artifacts, binding_ref=binding_ref))

    def seal(self, commitment, *, idempotency_key):
        self._artifact(commitment, "commitment")
        try:
            result = self._call("POST", "/seal", dict(commitment=commitment, idempotency_key=idempotency_key))
        except UncertainExecution as exc:
            raise SealPending() from exc
        except Exception as exc:
            if getattr(exc, "status", None) == 503:
                raise SealPending(status=503) from exc
            raise
        return self._sealed(result, commitment)

    def _sealed(self, result, commitment):
        receipt = self._artifact(result["receipt"], "seal_receipt")
        if receipt["body"]["commitment_ref"] != reference(commitment):
            raise ProtocolError("SEAL_RECEIPT_SCOPE_MISMATCH")
        return result

    def recover_seal_receipt(self, commitment):
        self._artifact(commitment, "commitment")
        return self._sealed(self._call("GET", "/seal-receipts/" + segment(commitment["artifact_id"])), commitment)

    def timeline(self, action_id, *, reference_time=None):
        query = "?" + urlencode({"reference_time": reference_time}) if reference_time else ""
        value = self._call("GET", "/actions/" + segment(action_id) + "/timeline" + query)
        projection(value["projection"])
        for event in value["events"]:
            self._artifact(event, "event")
        return value

    def projection(self, action_id, *, reference_time=None):
        query = "?" + urlencode({"reference_time": reference_time}) if reference_time else ""
        return projection(self._call("GET", "/actions/" + segment(action_id) + "/projection" + query))

    def observations(self, payload):
        return self._call("POST", "/observations", payload)

    def archive_intake(self, payload):
        return self._call("POST", "/archive-intake", payload)

    def export_bundle(self, action_id, *, reference_time=None, scope="FULL_ACTION", disclose_manifest_identifiers=False):
        payload = dict(scope=scope, disclose_manifest_identifiers=disclose_manifest_identifiers)
        if reference_time:
            timestamp(reference_time); payload["reference_time"] = reference_time
        response = self.transport.request("POST", "/actions/" + segment(action_id) + "/bundle",
            payload=payload, headers=self._headers, max_bytes=67108864)
        if response.status != 200:
            json_response(response)
        h = response.headers
        if h.get("content-type", "").split(";")[0] != "application/zip" or not response.body.startswith(b"PK"):
            raise ProtocolError("BUNDLE_RESPONSE_INVALID")
        try:
            at = h["x-aurora-g-reference-time"]; timestamp(at)
            trust, digest = h["x-aurora-g-trust-selection-digest"], h["x-aurora-g-projection-sha256"]
            import re
            if not all(re.fullmatch(r"sha256:[0-9a-f]{64}", x) for x in (trust, digest)):
                raise ValueError()
        except (KeyError, ValueError) as exc:
            raise ProtocolError("BUNDLE_METADATA_INVALID") from exc
        return BundleExport(response.body, at, trust, digest)

    def publish(self, action_id, *, publication_enabled, ttl_seconds=86400):
        if publication_enabled is not True:
            raise ProtocolError("EXPLICIT_PUBLICATION_REQUIRED")
        return self._call("POST", "/actions/" + segment(action_id) + "/publication",
            dict(publication_enabled=True, ttl_seconds=ttl_seconds))

    def revoke_publication(self, public_id):
        return self._call("DELETE", "/publications/" + segment(public_id))

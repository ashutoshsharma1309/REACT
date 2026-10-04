"""Phase 6: AWS integration code exercised against botocore Stubber (real boto3 clients, no network, no credentials).

Stubber validates each request against the real service model, so a wrong parameter name or shape fails here.
"""
from __future__ import annotations

import io

import boto3
import pytest
from botocore.response import StreamingBody
from botocore.stub import ANY, Stubber

from app.agents.provider import BedrockProvider, ProviderError
from app.storage import LocalArtifactStore, S3ArtifactStore, StorageError
from app.storage.base import sha256_hex

FAKE = {"aws_access_key_id": "testing", "aws_secret_access_key": "testing", "region_name": "ap-south-1"}  # never used to sign: Stubber answers first


def _bedrock():
    return boto3.client("bedrock-runtime", **FAKE)


def test_bedrock_converse_request_shape_and_tool_use_parsing():
    client = _bedrock()
    stub = Stubber(client)
    expected = {
        "modelId": "configured-model",
        "system": [{"text": "sys"}],
        "messages": [{"role": "user", "content": [{"text": "which machine?"}]}],
        "inferenceConfig": {"maxTokens": 512, "temperature": 0.0},
        "toolConfig": {"tools": [{"toolSpec": {"name": "get_machine_readings", "description": "Sensor readings", "inputSchema": {"json": {"type": "object", "properties": {"machine_id": {"type": "string"}}}}}}]},
    }
    response = {
        "output": {"message": {"role": "assistant", "content": [{"text": "Checking readings."}, {"toolUse": {"toolUseId": "tu-1", "name": "get_machine_readings", "input": {"machine_id": "C1"}}}]}},
        "stopReason": "tool_use",
        "usage": {"inputTokens": 210, "outputTokens": 35, "totalTokens": 245},
        "metrics": {"latencyMs": 800},
    }
    stub.add_response("converse", response, expected)
    with stub:
        p = BedrockProvider(model="configured-model", region="ap-south-1", max_tokens=512, client=client)
        out = p.generate_with_tools("sys", [{"role": "user", "content": "which machine?"}], [{"name": "get_machine_readings", "description": "Sensor readings", "input_schema": {"type": "object", "properties": {"machine_id": {"type": "string"}}}}])
    stub.assert_no_pending_responses()
    assert out.tool_calls[0].tool_name == "get_machine_readings" and out.tool_calls[0].arguments == {"machine_id": "C1"}
    assert (out.input_tokens, out.output_tokens, out.stop_reason, out.provider) == (210, 35, "tool_use", "bedrock")


@pytest.mark.parametrize("code", ["ThrottlingException", "AccessDeniedException", "ValidationException", "ServiceUnavailableException"])
def test_bedrock_errors_become_safe_provider_errors(code):
    client = _bedrock()
    stub = Stubber(client)
    stub.add_client_error("converse", service_error_code=code, service_message="arn:aws:iam::123456789012:role/secret-role details", http_status_code=400)
    with stub, pytest.raises(ProviderError) as exc:
        BedrockProvider(model="m", region="ap-south-1", client=client).generate("s", [{"role": "user", "content": "u"}])
    assert "123456789012" not in str(exc.value) and "secret-role" not in str(exc.value)


def test_s3_store_put_uses_encryption_and_records_sha256():
    client = boto3.client("s3", **FAKE)
    stub = Stubber(client)
    data = b'{"kind": "evidence_pack"}'
    stub.add_response("put_object", {"ETag": '"abc"'}, {"Bucket": "react-artifacts", "Key": "evidence-packs/x/y.json", "Body": data, "ContentType": "application/json", "ServerSideEncryption": "AES256", "Metadata": {"decision-id": "d1", "sha256": sha256_hex(data)}})
    stub.add_response("get_object", {"Body": StreamingBody(io.BytesIO(data), len(data))}, {"Bucket": "react-artifacts", "Key": "evidence-packs/x/y.json"})
    with stub:
        store = S3ArtifactStore(bucket="react-artifacts", region="ap-south-1", client=client)
        obj = store.put("evidence-packs/x/y.json", data, metadata={"decision-id": "d1"})
        assert store.get("evidence-packs/x/y.json") == data
    assert obj.sha256 == sha256_hex(data) and obj.size_bytes == len(data) and obj.backend == "s3"


def test_s3_store_errors_are_sanitized():
    client = boto3.client("s3", **FAKE)
    stub = Stubber(client)
    stub.add_client_error("put_object", service_error_code="AccessDenied", service_message="User arn:aws:sts::123456789012:assumed-role/x is not authorized", http_status_code=403)
    stub.add_client_error("get_object", service_error_code="NoSuchKey", http_status_code=404)
    with stub:
        store = S3ArtifactStore(bucket="b", region="ap-south-1", client=client)
        with pytest.raises(StorageError) as e1:
            store.put("evidence-packs/a.json", b"{}")
        with pytest.raises(StorageError) as e2:
            store.get("evidence-packs/missing.json")
    assert str(e1.value) == "S3 upload failed (AccessDenied)" and "123456789012" not in str(e1.value)
    assert "not found" in str(e2.value)


def test_local_store_round_trip_and_key_safety(tmp_path):
    store = LocalArtifactStore(tmp_path)
    obj = store.put("evidence-packs/2026/09/29/a.json", b"{}")
    assert store.get(obj.key) == b"{}" and obj.sha256 == sha256_hex(b"{}")
    for bad in ("../escape.json", "/etc/passwd", "a/../../b", "a//b", ""):
        with pytest.raises(StorageError):
            store.put(bad, b"x")
    with pytest.raises(StorageError):
        store.get("evidence-packs/none.json")

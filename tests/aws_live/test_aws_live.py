"""AWS LIVE tier. Skipped unless REACT_AWS_LIVE=1. Never runs in normal local tests or CI.

These call real AWS and may cost a little money (one short Bedrock request, one tiny S3 object).
Required: AWS credentials in the standard chain (profile, SSO or role), and
    REACT_AWS_LIVE=1  LLM_MODEL=<Bedrock model or inference-profile id>  AWS_REGION=<region>
    ARTIFACTS_BUCKET=<bucket> (for the S3 test)
Run:  REACT_AWS_LIVE=1 .venv/bin/pytest tests/aws_live -m aws_live
"""
from __future__ import annotations

import os
import uuid

import pytest
from pydantic import BaseModel

pytestmark = [pytest.mark.aws_live, pytest.mark.skipif(os.environ.get("REACT_AWS_LIVE") != "1", reason="AWS live tests need REACT_AWS_LIVE=1 and AWS credentials")]


class Ping(BaseModel):
    status: str


def test_bedrock_converse_live():
    from app.agents.provider import BedrockProvider

    model = os.environ.get("LLM_MODEL")
    if not model:
        pytest.skip("LLM_MODEL not set")
    p = BedrockProvider(model=model, region=os.environ.get("AWS_REGION", "us-east-1"), max_tokens=64, timeout_seconds=30)
    obj, resp = p.generate_structured("You are a health check. Reply with JSON only.", [{"role": "user", "content": 'Return {"status": "ok"}.'}], Ping)
    assert obj.status.lower() == "ok" and resp.input_tokens > 0 and resp.provider == "bedrock"


def test_s3_artifact_round_trip_live():
    from app.storage import S3ArtifactStore

    bucket = os.environ.get("ARTIFACTS_BUCKET")
    if not bucket:
        pytest.skip("ARTIFACTS_BUCKET not set")
    store = S3ArtifactStore(bucket=bucket, region=os.environ.get("AWS_REGION", "us-east-1"))
    key = f"evidence-packs/live-test/{uuid.uuid4()}.json"
    obj = store.put(key, b'{"live_test": true}')
    assert store.get(key) == b'{"live_test": true}' and obj.sha256

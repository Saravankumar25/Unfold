from __future__ import annotations

import os
import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any

import boto3
import pytest
from moto import mock_aws

from unfold.core.config import Settings
from unfold.persistence.dynamodb import table as ddb_table

# Tests must never read a developer's real .env keys or talk to real providers unless marked `llm`.
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("LOG_FORMAT", "console")

REGION = "us-east-1"


@dataclass
class AwsEnv:
    """An in-process AWS (moto): the same table, queues and bucket shape the Terraform modules create."""

    settings: Settings
    table: str
    bucket: str
    queue_url: str
    dlq_url: str
    cluster_arn: str

    def client(self, service: str) -> object:
        return boto3.client(service, region_name=REGION)


def _create_table(ddb: object, name: str) -> None:
    attrs = ["PK", "SK", "GSI1PK", "GSI1SK", "GSI2PK", "GSI2SK", "GSI3PK", "GSI3SK"]
    ddb.create_table(  # type: ignore[attr-defined]
        TableName=name,
        BillingMode="PAY_PER_REQUEST",
        AttributeDefinitions=[{"AttributeName": a, "AttributeType": "S"} for a in attrs],
        KeySchema=[{"AttributeName": "PK", "KeyType": "HASH"}, {"AttributeName": "SK", "KeyType": "RANGE"}],
        GlobalSecondaryIndexes=[
            {
                "IndexName": f"GSI{i}",
                "KeySchema": [
                    {"AttributeName": f"GSI{i}PK", "KeyType": "HASH"},
                    {"AttributeName": f"GSI{i}SK", "KeyType": "RANGE"},
                ],
                "Projection": {"ProjectionType": "ALL" if i < 3 else "KEYS_ONLY"},
            }
            for i in (1, 2, 3)
        ],
    )


@pytest.fixture
def aws(monkeypatch: pytest.MonkeyPatch) -> Iterator[AwsEnv]:
    for var in (
        "AWS_ENDPOINT_URL",
        "AWS_ENDPOINT_URL_DYNAMODB",
        "AWS_ENDPOINT_URL_S3",
        "AWS_ENDPOINT_URL_SQS",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", REGION)
    # moto's backends are not linearizable under threads (a concurrent ADD can be lost); DynamoDB's single-item
    # operations are. Serialising each call gives the tests the real service's atomicity per request.
    gate = threading.Lock()
    real_call = ddb_table.DynamoTable._call

    async def serial_call[T](self: Any, fn: Callable[[], T]) -> T:
        def locked() -> T:
            with gate:
                return fn()

        return await real_call(self, locked)

    monkeypatch.setattr(ddb_table.DynamoTable, "_call", serial_call)
    with mock_aws():
        name, bucket = "unfold-test", "unfold-test-bucket"
        _create_table(boto3.client("dynamodb", region_name=REGION), name)
        boto3.client("s3", region_name=REGION).create_bucket(Bucket=bucket)
        sqs = boto3.client("sqs", region_name=REGION)
        dlq = sqs.create_queue(QueueName=f"{name}-jobs-dlq")["QueueUrl"]
        dlq_arn = sqs.get_queue_attributes(QueueUrl=dlq, AttributeNames=["QueueArn"])["Attributes"][
            "QueueArn"
        ]
        queue = sqs.create_queue(
            QueueName=f"{name}-jobs",
            Attributes={
                "RedrivePolicy": f'{{"deadLetterTargetArn":"{dlq_arn}","maxReceiveCount":"5"}}',
                "VisibilityTimeout": "60",
            },
        )["QueueUrl"]
        cluster = "arn:aws:ecs:us-east-1:123456789012:cluster/unfold-test"
        settings = Settings(
            _env_file=None,  # type: ignore[call-arg]
            app_env="test",
            persistence_backend="dynamodb",
            dense_backend="artifact",
            graph_backend="memory",
            aws_region_name=REGION,
            dynamodb_table=name,
            object_store_bucket=bucket,
            jobs_queue_url=queue,
            ecs_cluster_arn=cluster,
            task_definition_analysis="unfold-test-analysis",
            subnet_ids="subnet-1,subnet-2",
            security_group_ids="sg-1",
            max_concurrent_jobs=1,
            max_active_jobs_per_session=2,
            max_queued_jobs_global=5,
        )
        yield AwsEnv(settings, name, bucket, queue, dlq, cluster)

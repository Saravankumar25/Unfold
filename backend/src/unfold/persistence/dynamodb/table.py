"""Thin async wrapper over one DynamoDB table (boto3 is sync, so every call runs in a worker thread).

Owns three concerns and nothing else: attribute-value (de)serialisation (floats are Decimals on the wire),
condition failures as a typed exception, and store outages as `StoreUnavailable` (the API maps it to 503 and
callers fail closed).
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from decimal import Decimal
from typing import Any

import boto3
from boto3.dynamodb.types import TypeDeserializer, TypeSerializer
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from unfold.core.config import Settings
from unfold.core.errors import StoreUnavailable

_SER = TypeSerializer()
_DES = TypeDeserializer()
MAX_ITEM_BYTES = 300_000  # DynamoDB's limit is 400 KB; refuse early with a typed error instead
_THROTTLE = {
    "ProvisionedThroughputExceededException",
    "ThrottlingException",
    "RequestLimitExceeded",
    "InternalServerError",
    "ServiceUnavailable",
    "TransactionInProgressException",
}
_TX_KIND = {"put": "Put", "update": "Update", "delete": "Delete", "check": "ConditionCheck"}


class ConditionFailed(Exception):
    """A conditional write did not apply (the item changed, exists, or a counter hit its limit)."""

    def __init__(self, reasons: Sequence[str] = ()) -> None:
        super().__init__("condition failed")
        self.reasons = list(reasons)


def to_ddb(value: Any) -> Any:
    """Python value -> value DynamoDB accepts (no floats)."""
    if isinstance(value, float):
        return Decimal(repr(value))
    if isinstance(value, dict):
        return {k: to_ddb(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [to_ddb(v) for v in value]
    return value


def from_ddb(value: Any) -> Any:
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, dict):
        return {k: from_ddb(v) for k, v in value.items()}
    if isinstance(value, list):
        return [from_ddb(v) for v in value]
    return value


def _marshal(item: dict[str, Any]) -> dict[str, Any]:
    return {k: _SER.serialize(to_ddb(v)) for k, v in item.items()}


def _unmarshal(item: dict[str, Any]) -> dict[str, Any]:
    return {k: from_ddb(_DES.deserialize(v)) for k, v in item.items()}


def _key(pk: str, sk: str) -> dict[str, Any]:
    return _marshal({"PK": pk, "SK": sk})


def _expr(
    args: dict[str, Any],
    condition: str | None,
    names: dict[str, str] | None,
    values: dict[str, Any] | None,
) -> None:
    if condition:
        args["ConditionExpression"] = condition
    if names:
        args["ExpressionAttributeNames"] = names
    if values:
        args["ExpressionAttributeValues"] = _marshal(values)


class DynamoTable:
    def __init__(self, settings: Settings, *, client: Any | None = None) -> None:
        self.name = settings.dynamodb_table
        if client is None:
            kwargs: dict[str, Any] = {}
            if settings.aws_region_name:
                kwargs["region_name"] = settings.aws_region_name
            if settings.aws_endpoint_url:
                kwargs["endpoint_url"] = settings.aws_endpoint_url
            client = boto3.client(
                "dynamodb",
                config=Config(
                    retries={"max_attempts": 4, "mode": "standard"},
                    connect_timeout=3,
                    read_timeout=10,
                    max_pool_connections=32,
                ),
                **kwargs,
            )
        self._client = client

    async def _call[T](self, fn: Callable[[], T]) -> T:
        try:
            return await asyncio.to_thread(fn)
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code == "ConditionalCheckFailedException":
                raise ConditionFailed() from exc
            if code == "TransactionCanceledException":
                reasons = [r.get("Code", "") for r in exc.response.get("CancellationReasons", [])]
                if "ConditionalCheckFailed" in reasons:
                    raise ConditionFailed(reasons) from exc
                raise StoreUnavailable(f"Control store transaction cancelled: {','.join(reasons)}") from exc
            if code in _THROTTLE:
                raise StoreUnavailable(f"Control store throttled: {code}") from exc
            raise StoreUnavailable(f"Control store error: {code or type(exc).__name__}") from exc
        except BotoCoreError as exc:
            raise StoreUnavailable(f"Control store unreachable: {type(exc).__name__}") from exc

    # --- single item ------------------------------------------------------------------------------------

    async def get(self, pk: str, sk: str, *, consistent: bool = True) -> dict[str, Any] | None:
        resp = await self._call(
            lambda: self._client.get_item(TableName=self.name, Key=_key(pk, sk), ConsistentRead=consistent)
        )
        raw = resp.get("Item")
        return None if raw is None else _unmarshal(raw)

    async def put(
        self,
        item: dict[str, Any],
        *,
        condition: str | None = None,
        names: dict[str, str] | None = None,
        values: dict[str, Any] | None = None,
    ) -> None:
        self.check_size(item)
        args: dict[str, Any] = {"TableName": self.name, "Item": _marshal(item)}
        _expr(args, condition, names, values)
        await self._call(lambda: self._client.put_item(**args))

    async def update(
        self,
        pk: str,
        sk: str,
        expression: str,
        *,
        names: dict[str, str] | None = None,
        values: dict[str, Any] | None = None,
        condition: str | None = None,
    ) -> dict[str, Any]:
        """UpdateItem; returns the item as it is after the update."""
        args: dict[str, Any] = {
            "TableName": self.name,
            "Key": _key(pk, sk),
            "UpdateExpression": expression,
            "ReturnValues": "ALL_NEW",
        }
        _expr(args, condition, names, values)
        resp = await self._call(lambda: self._client.update_item(**args))
        return _unmarshal(resp.get("Attributes", {}))

    async def delete(self, pk: str, sk: str) -> None:
        await self._call(lambda: self._client.delete_item(TableName=self.name, Key=_key(pk, sk)))

    # --- collections ------------------------------------------------------------------------------------

    async def query(
        self,
        pk: str,
        *,
        sk_prefix: str | None = None,
        sk_lt: str | None = None,
        sk_between: tuple[str, str] | None = None,
        index: str | None = None,
        consistent: bool = False,
        forward: bool = True,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """Items of one partition (or one index partition, whose key attributes are `<index>PK`/`<index>SK`)."""
        pk_attr, sk_attr = (f"{index}PK", f"{index}SK") if index else ("PK", "SK")
        names = {"#p": pk_attr}
        values: dict[str, Any] = {":p": pk}
        cond = "#p = :p"
        if sk_prefix is not None:
            names["#s"] = sk_attr
            values[":s"] = sk_prefix
            cond += " AND begins_with(#s, :s)"
        elif sk_lt is not None:
            names["#s"] = sk_attr
            values[":s"] = sk_lt
            cond += " AND #s < :s"
        elif sk_between is not None:
            names["#s"] = sk_attr
            values[":lo"], values[":hi"] = sk_between
            cond += " AND #s BETWEEN :lo AND :hi"
        out: list[dict[str, Any]] = []
        start: dict[str, Any] | None = None
        while True:
            args: dict[str, Any] = {
                "TableName": self.name,
                "KeyConditionExpression": cond,
                "ExpressionAttributeNames": names,
                "ExpressionAttributeValues": _marshal(values),
                "ScanIndexForward": forward,
            }
            if index:
                args["IndexName"] = index
            elif consistent:
                args["ConsistentRead"] = True  # not allowed on a GSI
            if limit is not None:
                args["Limit"] = limit - len(out)
            if start:
                args["ExclusiveStartKey"] = start
            resp = await self._call(lambda a=args: self._client.query(**a))  # type: ignore[misc]
            out.extend(_unmarshal(i) for i in resp.get("Items", []))
            start = resp.get("LastEvaluatedKey")
            if not start or (limit is not None and len(out) >= limit):
                return out

    async def transact(self, ops: Sequence[dict[str, Any]]) -> None:
        """All-or-nothing. Each op is {kind: spec}: put {item, condition?}, update {pk, sk, expression, ...},
        delete {pk, sk}, check {pk, sk, condition, ...}. Conditions may reference `names`/`values`."""
        items: list[dict[str, Any]] = []
        for op in ops:
            kind, spec = next(iter(op.items()))
            args: dict[str, Any] = {"TableName": self.name}
            if kind == "put":
                self.check_size(spec["item"])
                args["Item"] = _marshal(spec["item"])
            else:
                args["Key"] = _key(spec["pk"], spec["sk"])
            if kind == "update":
                args["UpdateExpression"] = spec["expression"]
            _expr(args, spec.get("condition"), spec.get("names"), spec.get("values"))
            items.append({_TX_KIND[kind]: args})
        await self._call(lambda: self._client.transact_write_items(TransactItems=items))

    async def batch_write(
        self, puts: Sequence[dict[str, Any]] = (), deletes: Sequence[tuple[str, str]] = ()
    ) -> None:
        """Unconditional writes in batches of 25, retrying unprocessed items with backoff."""
        reqs: list[dict[str, Any]] = [{"PutRequest": {"Item": _marshal(i)}} for i in puts]
        reqs += [{"DeleteRequest": {"Key": _key(pk, sk)}} for pk, sk in deletes]
        for i in range(0, len(reqs), 25):
            pending: dict[str, Any] = {self.name: reqs[i : i + 25]}
            for attempt in range(6):
                resp = await self._call(lambda p=pending: self._client.batch_write_item(RequestItems=p))  # type: ignore[misc]
                pending = resp.get("UnprocessedItems") or {}
                if not pending:
                    break
                await asyncio.sleep(min(2.0, 0.05 * 2**attempt))
            else:
                raise StoreUnavailable("Control store write was throttled")

    async def delete_partition(self, pk: str, *, keep_prefix: str | None = None) -> int:
        """Delete every item of one partition (cleanup: explicit, resumable, never left to TTL)."""
        items = await self.query(pk, consistent=True)
        doomed = [
            (i["PK"], i["SK"]) for i in items if keep_prefix is None or not i["SK"].startswith(keep_prefix)
        ]
        await self.batch_write(deletes=doomed)
        return len(doomed)

    @staticmethod
    def check_size(item: dict[str, Any]) -> None:
        approx = len(repr(item))  # a cheap upper bound; the real limit is 400 KB per item
        if approx > MAX_ITEM_BYTES:
            raise StoreUnavailable(f"Record too large for the control store ({approx} bytes).")

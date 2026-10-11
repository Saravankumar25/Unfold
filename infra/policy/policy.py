"""Cost-guard and security policy over an offline Terraform plan (`terraform show -json`).

Why plan JSON and not HCL: modules, `for_each` and variables are already resolved, so the rules see what would
actually be created. IAM policy documents built by `data.aws_iam_policy_document` are unknown at plan time (they
embed ARNs of resources that do not exist yet), so those are read from the plan's `configuration` section, where
the statement actions and the literal resources are constants.

Each rule is a function `(plan) -> list[str]` of violation messages and is registered in `RULES` under a stable
id; `test_policy.py` proves every rule fires on a violating fixture and stays silent on the real environments.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any

MAX_LOG_RETENTION_DAYS = 30


@dataclass(frozen=True)
class Res:
    address: str
    type: str
    name: str
    values: dict[str, Any]
    module: str  # module path as it appears in addresses, "" for the root


@dataclass
class Plan:
    resources: list[Res] = field(default_factory=list)
    # configuration-side view: module path -> list of {type, name, mode, expressions}
    config: dict[str, list[dict[str, Any]]] = field(default_factory=dict)

    def of_type(self, *types: str) -> list[Res]:
        return [r for r in self.resources if r.type in types]


def _walk_planned(module: dict[str, Any], out: list[Res]) -> None:
    for r in module.get("resources", []):
        if r.get("mode") != "managed":
            continue
        address = r["address"]
        module_path = address.rsplit(f".{r['type']}.", 1)[0] if address.startswith("module.") else ""
        out.append(Res(address, r["type"], r["name"], r.get("values") or {}, module_path))
    for child in module.get("child_modules", []):
        _walk_planned(child, out)


def _walk_config(module: dict[str, Any], path: str, out: dict[str, list[dict[str, Any]]]) -> None:
    out[path] = list(module.get("resources", []))
    for name, call in (module.get("module_calls") or {}).items():
        child = f"{path}.module.{name}" if path else f"module.{name}"
        _walk_config(call.get("module", {}), child, out)


def load_plan(plan_json: dict[str, Any]) -> Plan:
    plan = Plan()
    _walk_planned((plan_json.get("planned_values") or {}).get("root_module", {}), plan.resources)
    _walk_config((plan_json.get("configuration") or {}).get("root_module", {}), "", plan.config)
    return plan


# --- helpers -----------------------------------------------------------------------------------------------------


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _constant(expr: Any) -> Any:
    return expr.get("constant_value") if isinstance(expr, dict) else None


def _refs(expr: Any) -> set[str]:
    return set(expr.get("references", [])) if isinstance(expr, dict) else set()


def _first_block(value: Any) -> dict[str, Any]:
    items = _as_list(value)
    return items[0] if items and isinstance(items[0], dict) else {}


def _config_resources(plan: Plan, *types: str) -> Iterator[tuple[str, dict[str, Any]]]:
    for module_path, resources in plan.config.items():
        for r in resources:
            if r.get("mode") == "managed" and r.get("type") in types:
                yield module_path, r


def _referenced_addresses(expr: Any, module_path: str) -> set[str]:
    """Resource addresses (with module path) a configuration expression refers to, stripped of attributes."""
    out: set[str] = set()
    for ref in _refs(expr):
        parts = ref.split(".")
        if len(parts) >= 2 and parts[0] != "var" and parts[0] != "local" and parts[0] != "module":
            base = ".".join(parts[:2])
            out.add(f"{module_path}.{base}" if module_path else base)
    return out


def _statements_of(policy_json: str) -> list[dict[str, Any]]:
    try:
        doc = json.loads(policy_json)
    except (TypeError, ValueError):
        return []
    return [s for s in _as_list(doc.get("Statement")) if isinstance(s, dict)]


def _is_wildcard_action(action: str) -> bool:
    return action in ("*", "*:*")


def _service_wide(action: str) -> bool:
    return action.endswith(":*") and action != "*:*"


def _iam_findings(effect: str, actions: list[str], resources: list[str], where: str) -> list[str]:
    if effect.lower() != "allow":
        return []
    found = []
    if any(_is_wildcard_action(a) for a in actions):
        found.append(f"{where}: Allow with Action {actions!r} (full wildcard)")
    elif "*" in resources and any(_service_wide(a) for a in actions):
        found.append(f"{where}: Allow {actions!r} on Resource '*' (service-wide wildcard)")
    if "*" in resources and "iam:PassRole" in actions:
        found.append(f"{where}: iam:PassRole on Resource '*'")
    return found


# --- rules -------------------------------------------------------------------------------------------------------

_FORBIDDEN_TYPES: dict[str, tuple[str, ...]] = {
    "no_ecs_service": ("aws_ecs_service",),
    "no_nat_gateway": ("aws_nat_gateway", "aws_eip", "aws_eip_association"),
    "no_load_balancer": ("aws_lb", "aws_alb", "aws_elb", "aws_lb_listener", "aws_lb_target_group", "aws_alb_listener"),
    "no_relational_database": (
        "aws_db_instance",
        "aws_rds_cluster",
        "aws_rds_cluster_instance",
        "aws_db_proxy",
    ),
    "no_provisioned_concurrency": ("aws_lambda_provisioned_concurrency_config",),
    "no_unapproved_services": (
        "aws_api_gateway_rest_api",
        "aws_apigatewayv2_api",
        "aws_sfn_state_machine",
        "aws_cloudfront_distribution",
        "aws_wafv2_web_acl",
        "aws_wafv2_web_acl_association",
        "aws_secretsmanager_secret",
        "aws_kms_key",
        "aws_elasticache_cluster",
        "aws_elasticache_replication_group",
        "aws_opensearch_domain",
        "aws_elasticsearch_domain",
        "aws_network_firewall",
        "aws_msk_cluster",
        "aws_mq_broker",
        "aws_instance",
        "aws_autoscaling_group",
        "aws_dax_cluster",
        "aws_dynamodb_table_replica",
        "aws_route53_zone",
        "aws_cloudtrail",
        "aws_cloudwatch_dashboard",
    ),
    "no_static_access_keys": ("aws_iam_access_key", "aws_iam_user_login_profile"),
    # a refresh reads a SecureString back decrypted, so the value would land in the state file
    "no_secrets_in_state": ("aws_ssm_parameter",),
}


def _forbidden(rule_id: str) -> Callable[[Plan], list[str]]:
    types = _FORBIDDEN_TYPES[rule_id]

    def rule(plan: Plan) -> list[str]:
        return [f"{r.address}: {r.type} is not allowed ({rule_id})" for r in plan.of_type(*types)]

    return rule


def no_interface_vpc_endpoints(plan: Plan) -> list[str]:
    out = []
    configured = {
        f"{m + '.' if m else ''}aws_vpc_endpoint.{c['name']}": (c.get("expressions") or {}).get("vpc_endpoint_type")
        for m, c in _config_resources(plan, "aws_vpc_endpoint")
    }
    for r in plan.of_type("aws_vpc_endpoint"):
        kind = r.values.get("vpc_endpoint_type")
        if kind is None:  # unknown at plan time: only a literal "Gateway" (or no setting) is accepted
            expr = configured.get(r.address.split("[")[0])
            kind = "Gateway" if expr is None or _constant(expr) == "Gateway" else "unknown"
        if kind != "Gateway":
            out.append(f"{r.address}: {kind} VPC endpoint bills hourly per AZ; only Gateway endpoints are allowed")
    return out


def no_provisioned_concurrency_scaling(plan: Plan) -> list[str]:
    out = []
    for r in plan.of_type("aws_appautoscaling_target"):
        if r.values.get("service_namespace") == "lambda":
            out.append(f"{r.address}: autoscaled Lambda provisioned concurrency is not allowed")
    for r in plan.of_type("aws_lambda_alias"):
        if r.values.get("provisioned_concurrent_executions"):
            out.append(f"{r.address}: provisioned concurrency is not allowed")
    return out


def s3_private_and_encrypted(plan: Plan) -> list[str]:
    out: list[str] = []
    blocks: dict[str, dict[str, Any]] = {}
    sse: set[str] = set()
    for module_path, r in _config_resources(plan, "aws_s3_bucket_public_access_block"):
        for bucket in _referenced_addresses(r["expressions"].get("bucket"), module_path):
            blocks[bucket] = r["expressions"]
    for module_path, r in _config_resources(plan, "aws_s3_bucket_server_side_encryption_configuration"):
        sse |= _referenced_addresses(r["expressions"].get("bucket"), module_path)
    for module_path, r in _config_resources(plan, "aws_s3_bucket"):
        address = f"{module_path}.aws_s3_bucket.{r['name']}" if module_path else f"aws_s3_bucket.{r['name']}"
        exprs = blocks.get(address)
        if exprs is None:
            out.append(f"{address}: no aws_s3_bucket_public_access_block")
        else:
            for flag in ("block_public_acls", "block_public_policy", "ignore_public_acls", "restrict_public_buckets"):
                if _constant(exprs.get(flag)) is not True:
                    out.append(f"{address}: public access block does not set {flag} = true")
        if address not in sse:
            out.append(f"{address}: no server-side encryption configuration")
    for r in plan.of_type("aws_s3_bucket_acl"):
        if str(r.values.get("acl", "")).startswith("public-"):
            out.append(f"{r.address}: public ACL {r.values.get('acl')!r}")
    for r in plan.of_type("aws_s3_bucket_public_access_block"):
        for flag in ("block_public_acls", "block_public_policy", "ignore_public_acls", "restrict_public_buckets"):
            if r.values.get(flag) is False:
                out.append(f"{r.address}: {flag} = false")
    for r in plan.of_type("aws_s3_bucket_policy"):
        for st in _statements_of(r.values.get("policy") or ""):
            principal = st.get("Principal")
            public = principal == "*" or (isinstance(principal, dict) and "*" in _as_list(principal.get("AWS")))
            if st.get("Effect") == "Allow" and public and not st.get("Condition"):
                out.append(f"{r.address}: Allow to Principal '*' without a condition")
    return out


def no_iam_wildcards(plan: Plan) -> list[str]:
    out: list[str] = []
    # inline / managed policies whose JSON is known at plan time (jsonencode of constants)
    for r in plan.of_type("aws_iam_policy", "aws_iam_role_policy", "aws_iam_user_policy", "aws_iam_group_policy"):
        for st in _statements_of(r.values.get("policy") or ""):
            out += _iam_findings(
                st.get("Effect", "Allow"),
                [str(a) for a in _as_list(st.get("Action"))],
                [str(x) for x in _as_list(st.get("Resource"))],
                r.address,
            )
    # data.aws_iam_policy_document statements: actions and literal resources are constants in the configuration
    for module_path, resources in plan.config.items():
        for r in resources:
            if r.get("mode") != "data" or r.get("type") != "aws_iam_policy_document":
                continue
            where = f"{module_path + '.' if module_path else ''}data.aws_iam_policy_document.{r['name']}"
            for st in _as_list((r.get("expressions") or {}).get("statement")):
                if not isinstance(st, dict):
                    continue
                effect = _constant(st.get("effect")) or "Allow"
                if effect == "Allow" and (st.get("not_actions") or st.get("not_resources")):
                    out.append(f"{where}: Allow with not_actions/not_resources grants everything else")
                if (st.get("actions") or {}).get("references") and _constant(st.get("actions")) is None:
                    out.append(f"{where}: actions are not literal, the rule cannot read them")
                actions = [str(a) for a in _as_list(_constant(st.get("actions")))]
                resources_ = [str(a) for a in _as_list(_constant(st.get("resources")))]
                out += _iam_findings(effect, actions, resources_, where)
    for r in plan.of_type("aws_iam_role_policy_attachment", "aws_iam_user_policy_attachment", "aws_iam_policy_attachment"):
        arn = str(r.values.get("policy_arn") or "")
        if re.search(r"/(AdministratorAccess|PowerUserAccess|IAMFullAccess)$", arn):
            out.append(f"{r.address}: attaches {arn}")
    return out


def log_retention_set(plan: Plan) -> list[str]:
    out: list[str] = []
    group_names: set[str] = set()
    for r in plan.of_type("aws_cloudwatch_log_group"):
        days = r.values.get("retention_in_days")
        group_names.add(str(r.values.get("name")))
        if not days:
            out.append(f"{r.address}: no retention_in_days (logs would never expire)")
        elif days > MAX_LOG_RETENTION_DAYS:
            out.append(f"{r.address}: retention {days} days exceeds {MAX_LOG_RETENTION_DAYS}")
    # a group that is not managed is created by the service at first use with no expiry
    for r in plan.of_type("aws_lambda_function"):
        group = _first_block(r.values.get("logging_config")).get("log_group")
        if not group or group not in group_names:
            out.append(f"{r.address}: log group {group!r} is not a managed aws_cloudwatch_log_group")
    for r in plan.of_type("aws_ecs_task_definition"):
        try:
            containers = json.loads(r.values.get("container_definitions") or "[]")
        except ValueError:
            containers = []
        for c in containers:
            group = (c.get("logConfiguration") or {}).get("options", {}).get("awslogs-group")
            if group not in group_names:
                out.append(f"{r.address}: container {c.get('name')!r} logs to unmanaged group {group!r}")
    return out


def lambda_outside_vpc(plan: Plan) -> list[str]:
    msg = "Lambda in a VPC needs NAT or interface endpoints to reach AWS APIs"
    out = [
        f"{r.address}: {msg}"
        for r in plan.of_type("aws_lambda_function")
        if _first_block(r.values.get("vpc_config")).get("subnet_ids")
    ]
    for module_path, r in _config_resources(plan, "aws_lambda_function"):
        if (r.get("expressions") or {}).get("vpc_config"):
            out.append(f"{module_path + '.' if module_path else ''}aws_lambda_function.{r['name']}: {msg}")
    return sorted(set(out))


def dynamodb_protected_no_pitr(plan: Plan) -> list[str]:
    out = []
    for r in plan.of_type("aws_dynamodb_table"):
        if r.values.get("deletion_protection_enabled") is not True:
            out.append(f"{r.address}: deletion protection is off")
        pitr = _first_block(r.values.get("point_in_time_recovery"))
        if pitr.get("enabled"):
            out.append(f"{r.address}: point-in-time recovery bills continuously; data lives days")
        if r.values.get("billing_mode") != "PAY_PER_REQUEST":
            out.append(f"{r.address}: provisioned capacity bills while idle")
    return out


def security_groups_closed(plan: Plan) -> list[str]:
    out = []
    for r in plan.of_type("aws_security_group"):
        if _as_list(r.values.get("ingress")):
            out.append(f"{r.address}: inline ingress rules (tasks accept no inbound traffic)")
    for r in plan.of_type("aws_vpc_security_group_ingress_rule", "aws_security_group_rule"):
        if r.type == "aws_vpc_security_group_ingress_rule" or r.values.get("type") == "ingress":
            out.append(f"{r.address}: ingress rule")
    return out


def ecr_lifecycle_present(plan: Plan) -> list[str]:
    covered: set[str] = set()
    for module_path, r in _config_resources(plan, "aws_ecr_lifecycle_policy"):
        # `for_each = aws_ecr_repository.this` + `repository = each.value.name` covers every repository
        for expr in (r["expressions"].get("repository"), r.get("for_each_expression")):
            covered |= _referenced_addresses(expr, module_path)
    out = []
    for module_path, r in _config_resources(plan, "aws_ecr_repository"):
        address = f"{module_path}.aws_ecr_repository.{r['name']}" if module_path else f"aws_ecr_repository.{r['name']}"
        if address not in covered:
            out.append(f"{address}: no lifecycle policy (images would accumulate)")
        planned = [x for x in plan.of_type("aws_ecr_repository") if x.address.split("[")[0] == address]
        if any(x.values.get("image_tag_mutability") != "IMMUTABLE" for x in planned):
            out.append(f"{address}: tags are mutable (a pushed tag could be replaced)")
    return out


def sqs_has_redrive(plan: Plan) -> list[str]:
    queues = plan.of_type("aws_sqs_queue")
    out = []
    redriven = [q for q in queues if q.values.get("redrive_policy") or _has_redrive_expr(plan, q)]
    if queues and not redriven:
        out.append("no aws_sqs_queue has a redrive_policy: poison messages would loop until retention")
    return out


def _has_redrive_expr(plan: Plan, queue: Res) -> bool:
    for module_path, r in _config_resources(plan, "aws_sqs_queue"):
        if r["name"] == queue.name and module_path == queue.module and (r.get("expressions") or {}).get("redrive_policy"):
            return True
    return False


RULES: dict[str, Callable[[Plan], list[str]]] = {
    "no_ecs_service": _forbidden("no_ecs_service"),
    "no_nat_gateway": _forbidden("no_nat_gateway"),
    "no_load_balancer": _forbidden("no_load_balancer"),
    "no_relational_database": _forbidden("no_relational_database"),
    "no_provisioned_concurrency": lambda p: _forbidden("no_provisioned_concurrency")(p)
    + no_provisioned_concurrency_scaling(p),
    "no_interface_vpc_endpoints": no_interface_vpc_endpoints,
    "no_unapproved_services": _forbidden("no_unapproved_services"),
    "no_static_access_keys": _forbidden("no_static_access_keys"),
    "no_secrets_in_state": _forbidden("no_secrets_in_state"),
    "s3_private_and_encrypted": s3_private_and_encrypted,
    "no_iam_wildcards": no_iam_wildcards,
    "log_retention_set": log_retention_set,
    "lambda_outside_vpc": lambda_outside_vpc,
    "dynamodb_protected_no_pitr": dynamodb_protected_no_pitr,
    "security_groups_closed": security_groups_closed,
    "ecr_lifecycle_present": ecr_lifecycle_present,
    "sqs_has_redrive": sqs_has_redrive,
}


def evaluate(plan: Plan) -> dict[str, list[str]]:
    """Violations by rule id (rules with none are omitted)."""
    found = {rule_id: rule(plan) for rule_id, rule in RULES.items()}
    return {k: v for k, v in found.items() if v}

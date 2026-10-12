"""Cost-guard and security policy tests.

Real roots (bootstrap, envs/dev, envs/prod) are planned offline (dummy credentials, every provider API call
skipped, local backend) and must satisfy every rule. Each rule also has a violating fixture under `fixtures/` that
must make exactly that rule fire (the negative tests), and `fixtures/clean` must satisfy all of them.

Needs the `terraform` binary and a provider download (cached in UNFOLD_TF_PLUGIN_CACHE, default a temp folder).
Set UNFOLD_REQUIRE_TERRAFORM=1 in CI so a missing binary fails instead of skipping.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest

from policy import _FORBIDDEN_TYPES, RULES, Plan, evaluate, load_plan

HERE = Path(__file__).resolve().parent
TERRAFORM_ROOT = HERE.parent / "terraform"
FIXTURES = HERE / "fixtures"
REAL_ROOTS = ["bootstrap", "envs/dev", "envs/prod"]

OFFLINE_OVERRIDE = """
provider "aws" {
  access_key                  = "dummy"
  secret_key                  = "dummy"
  skip_credentials_validation = true
  skip_requesting_account_id  = true
  skip_metadata_api_check     = true
  skip_region_validation      = true
}

terraform {
  backend "local" {}
}
"""

# Values for variables without defaults. Dummies: nothing here reaches AWS.
DUMMY_VARS = {
    "account_id": "123456789012",
    "web_origin": "https://unfold.example.com",
    "alert_emails": '["ops@example.com"]',
    "api_image_uri": "123456789012.dkr.ecr.us-east-1.amazonaws.com/unfold-api:test",
    "worker_image_uri": "123456789012.dkr.ecr.us-east-1.amazonaws.com/unfold-worker:test",
    "exporter_image_uri": "123456789012.dkr.ecr.us-east-1.amazonaws.com/unfold-exporter:test",
}


def _terraform() -> str:
    exe = shutil.which("terraform")
    if exe is None:
        if os.environ.get("UNFOLD_REQUIRE_TERRAFORM") == "1":
            pytest.fail("terraform is required (UNFOLD_REQUIRE_TERRAFORM=1) but not on PATH")
        pytest.skip("terraform binary not available")
    return exe


def _run(cmd: list[str], cwd: Path, env: dict[str, str]) -> str:
    # The provider binary is ~600 MB; several starting at once can exceed Terraform's fixed 1 minute plugin start
    # timeout (slow disk, antivirus). That is the one transient failure worth a retry; anything else is real.
    for attempt in range(3):
        proc = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, check=False)  # noqa: S603
        if proc.returncode == 0:
            return proc.stdout
        if "waiting for plugin to start" not in proc.stdout + proc.stderr or attempt == 2:
            break
    raise RuntimeError(f"{' '.join(cmd[:3])} failed in {cwd.name}:\n{proc.stdout[-2000:]}\n{proc.stderr[-2000:]}")


def _plan_json(workdir: Path, variables: dict[str, str]) -> dict[str, Any]:
    exe = _terraform()
    cache = Path(os.environ.get("UNFOLD_TF_PLUGIN_CACHE", Path(tempfile.gettempdir()) / "unfold-tf-plugin-cache"))
    cache.mkdir(parents=True, exist_ok=True)
    env = {
        **os.environ,
        "TF_PLUGIN_CACHE_DIR": str(cache),
        "TF_IN_AUTOMATION": "1",
        "AWS_ACCESS_KEY_ID": "dummy",
        "AWS_SECRET_ACCESS_KEY": "dummy",
        "AWS_EC2_METADATA_DISABLED": "true",
    }
    (workdir / "zz_offline_override.tf").write_text(OFFLINE_OVERRIDE, encoding="utf-8")
    _run([exe, "init", "-input=false", "-no-color", "-reconfigure"], workdir, env)
    var_args = [a for k, v in variables.items() for a in ("-var", f"{k}={v}")]
    _run([exe, "plan", "-input=false", "-no-color", "-refresh=false", "-out=tfplan", *var_args], workdir, env)
    return json.loads(_run([exe, "show", "-json", "tfplan"], workdir, env))


@pytest.fixture(scope="session")
def terraform_tree(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Copy of infra/terraform so planning never writes `.terraform` into the repository."""
    dest = tmp_path_factory.mktemp("tf") / "terraform"
    shutil.copytree(
        TERRAFORM_ROOT,
        dest,
        ignore=shutil.ignore_patterns(".terraform", "*.tfstate*", "tfplan", "backend.hcl"),
    )
    return dest


@pytest.fixture(scope="session")
def plans(terraform_tree: Path, tmp_path_factory: pytest.TempPathFactory) -> dict[str, Plan]:
    """Plan every real root and fixture once (in parallel after a serial warm-up of the provider cache)."""
    jobs: dict[str, tuple[Path, dict[str, str]]] = {}
    for root in REAL_ROOTS:
        jobs[root] = (terraform_tree / root, DUMMY_VARS if root.startswith("envs/") else {"account_id": "123456789012"})
    for fixture in sorted(p for p in FIXTURES.iterdir() if p.is_dir()):
        work = tmp_path_factory.mktemp(f"fx-{fixture.name}")
        shutil.copytree(fixture, work, dirs_exist_ok=True)
        # the committed lock file pins the provider hashes and spares init a registry lookup per fixture
        shutil.copy(TERRAFORM_ROOT / "envs" / "dev" / ".terraform.lock.hcl", work)
        jobs[f"fixture:{fixture.name}"] = (work, {})
    order = list(jobs)
    first = order[0]
    results: dict[str, Plan] = {first: load_plan(_plan_json(*jobs[first]))}
    with ThreadPoolExecutor(max_workers=int(os.environ.get("UNFOLD_TF_WORKERS", "2"))) as pool:
        futures = {name: pool.submit(_plan_json, *jobs[name]) for name in order[1:]}
        for name, fut in futures.items():
            results[name] = load_plan(fut.result())
    return results


# --- the real stacks satisfy every rule ----------------------------------------------------------------------------


@pytest.mark.parametrize("root", REAL_ROOTS)
def test_real_root_satisfies_every_rule(plans: dict[str, Plan], root: str) -> None:
    violations = evaluate(plans[root])
    assert not violations, json.dumps(violations, indent=2)


@pytest.mark.parametrize("env", ["envs/dev", "envs/prod"])
def test_environments_have_the_expected_shape(plans: dict[str, Plan], env: str) -> None:
    plan = plans[env]
    types = [r.type for r in plan.resources]
    # nothing runs while idle ...
    assert "aws_ecs_service" not in types and "aws_nat_gateway" not in types and "aws_lb" not in types
    # ... and the pieces the design requires exist
    assert types.count("aws_budgets_budget") == 2
    assert types.count("aws_cloudwatch_metric_alarm") == 4
    assert types.count("aws_cloudwatch_log_metric_filter") == 2
    fn_url = [r for r in plan.resources if r.type == "aws_lambda_function_url"]
    assert len(fn_url) == 1 and fn_url[0].values["invoke_mode"] == "RESPONSE_STREAM"
    assert {r.name for r in plan.resources if r.type == "aws_lambda_function"} == {"api", "this"}
    assert len([r for r in plan.resources if r.type == "aws_lambda_function"]) == 4  # api + 3 control handlers
    assert any(r.type == "aws_sqs_queue" and r.values.get("name", "").endswith("-jobs") for r in plan.resources)
    assert len([r for r in plan.resources if r.type == "aws_iam_role"]) == 6  # api, worker task, export task, execution, dispatcher, scheduler
    table = next(r for r in plan.resources if r.type == "aws_dynamodb_table")
    assert table.values["deletion_protection_enabled"] is True
    assert env.split("/")[-1] in table.values["name"]


def test_dev_and_prod_use_separate_state_keys() -> None:
    keys = {}
    for env in ("dev", "prod"):
        text = (TERRAFORM_ROOT / "envs" / env / "backend.tf").read_text(encoding="utf-8")
        keys[env] = next(line.split('"')[1] for line in text.splitlines() if line.strip().startswith("key"))
    assert keys["dev"] != keys["prod"] and "dev" in keys["dev"] and "prod" in keys["prod"]


# --- every rule fires on its violating fixture (negative tests) ----------------------------------------------------


def test_every_rule_has_a_violating_fixture() -> None:
    fixtures = {p.name for p in FIXTURES.iterdir() if p.is_dir()}
    assert set(RULES) <= fixtures
    assert fixtures - {"clean"} <= set(RULES), "a fixture without a registered rule is never evaluated"


def test_every_forbidden_type_rule_is_registered() -> None:
    assert set(_FORBIDDEN_TYPES) <= set(RULES)


@pytest.mark.parametrize("rule_id", sorted(RULES))
def test_rule_fires_on_its_violating_fixture(plans: dict[str, Plan], rule_id: str) -> None:
    violations = evaluate(plans[f"fixture:{rule_id}"])
    assert rule_id in violations, f"{rule_id} did not fire; got {sorted(violations)}"


def test_clean_fixture_satisfies_every_rule(plans: dict[str, Plan]) -> None:
    assert evaluate(plans["fixture:clean"]) == {}


# --- the export task role is least privilege and the dispatcher can pass it ----------------------------------------


def _statements(plan: Plan, doc: str) -> list[dict[str, Any]]:
    resource = next(r for r in plan.config["module.iam"] if r["type"] == "aws_iam_policy_document" and r["name"] == doc)
    return [s for s in resource["expressions"]["statement"]]


@pytest.mark.parametrize("env", ["envs/dev", "envs/prod"])
def test_export_task_role_is_least_privilege(plans: dict[str, Plan], env: str) -> None:
    statements = _statements(plans[env], "export_task")
    assert "source_policy_documents" not in next(
        r for r in plans[env].config["module.iam"] if r["name"] == "export_task" and r["type"] == "aws_iam_policy_document"
    )["expressions"], "the export role must not inherit another role's document"
    actions = {a for s in statements for a in s["actions"]["constant_value"]}
    assert actions == {
        "dynamodb:GetItem", "dynamodb:Query", "dynamodb:UpdateItem",
        "s3:PutObject", "s3:DeleteObject", "s3:AbortMultipartUpload", "s3:ListBucket", "s3:ListBucketMultipartUploads",
    }  # fmt: skip
    assert not any(a == "*" or a.endswith(":*") or a.split(":")[0] in {"ssm", "sqs", "ecs", "secretsmanager", "iam"} for a in actions)
    assert all("*" not in str(s["resources"].get("constant_value", [])) for s in statements)  # no bare '*' resource


@pytest.mark.parametrize("env", ["envs/dev", "envs/prod"])
def test_dispatcher_can_pass_the_export_task_role(plans: dict[str, Plan], env: str) -> None:
    pass_role = next(s for s in _statements(plans[env], "dispatcher") if s["sid"]["constant_value"] == "PassTaskRoles")
    refs = set(pass_role["resources"]["references"])
    assert {"aws_iam_role.export_task.arn", "aws_iam_role.worker_task.arn", "aws_iam_role.task_execution.arn"} <= refs

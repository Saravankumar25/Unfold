"""Versioned technology-detection rules (LLD §7). Pure data + tiny matchers; consumed by analyzers and `detect`.

Bump `RULES_VERSION` whenever a rule is added, removed or re-weighted: it is stored with every `DetectionResult`
so analyses stay explainable and re-runs are comparable.

Matching is by *name* only (dependency names, import module prefixes, container image names, Terraform resource
types). Nothing is resolved over the network and nothing from the repository is executed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

RULES_VERSION = "2026.09.1"

DetectedKind = Literal["framework", "datastore", "queue", "external_service", "ai"]


@dataclass(frozen=True)
class TechRule:
    """One technology and every name that evidences it."""

    technology: str  # stable id, e.g. "fastapi"
    name: str  # display name, e.g. "FastAPI"
    kind: DetectedKind
    category: str
    deps: tuple[str, ...] = ()  # dependency names (canonical, lowercase); "prefix*" matches a prefix
    imports: tuple[
        str, ...
    ] = ()  # import module prefixes ("fastapi", "@prisma/client", "github.com/gin-gonic/gin")
    images: tuple[
        str, ...
    ] = ()  # container image repositories without registry/tag ("postgres", "qdrant/qdrant")
    infra: tuple[
        str, ...
    ] = ()  # terraform resource types / k8s kinds / prisma datasource providers ("prisma:postgresql")
    implies: tuple[tuple[str, float], ...] = ()  # (technology, confidence) also evidenced by this one
    extra: dict[str, str] = field(default_factory=dict)


def _r(
    technology: str,
    name: str,
    kind: DetectedKind,
    category: str,
    *,
    deps: tuple[str, ...] = (),
    imports: tuple[str, ...] = (),
    images: tuple[str, ...] = (),
    infra: tuple[str, ...] = (),
    implies: tuple[tuple[str, float], ...] = (),
) -> TechRule:
    return TechRule(technology, name, kind, category, deps, imports, images, infra, implies)


# ------------------------------------------------------------------------------------------------ frameworks

FRAMEWORK_RULES: tuple[TechRule, ...] = (
    # python web
    _r("fastapi", "FastAPI", "framework", "web_backend", deps=("fastapi",), imports=("fastapi",)),
    _r("flask", "Flask", "framework", "web_backend", deps=("flask",), imports=("flask",)),
    _r("django", "Django", "framework", "web_backend", deps=("django",), imports=("django",)),
    _r(
        "django_rest_framework",
        "Django REST framework",
        "framework",
        "web_backend",
        deps=("djangorestframework",),
        imports=("rest_framework",),
    ),
    _r(
        "django_ninja", "Django Ninja", "framework", "web_backend", deps=("django-ninja",), imports=("ninja",)
    ),
    _r("starlette", "Starlette", "framework", "web_backend", deps=("starlette",), imports=("starlette",)),
    _r("aiohttp", "aiohttp", "framework", "web_backend", deps=("aiohttp",), imports=("aiohttp.web",)),
    _r("tornado", "Tornado", "framework", "web_backend", deps=("tornado",), imports=("tornado",)),
    _r("sanic", "Sanic", "framework", "web_backend", deps=("sanic",), imports=("sanic",)),
    _r("litestar", "Litestar", "framework", "web_backend", deps=("litestar",), imports=("litestar",)),
    _r("quart", "Quart", "framework", "web_backend", deps=("quart",), imports=("quart",)),
    _r("falcon", "Falcon", "framework", "web_backend", deps=("falcon",), imports=("falcon",)),
    _r(
        "grpc",
        "gRPC",
        "framework",
        "web_backend",
        deps=("grpcio", "@grpc/grpc-js", "google.golang.org/grpc", "tonic"),
        imports=("grpc", "@grpc/grpc-js", "google.golang.org/grpc"),
    ),
    _r(
        "graphql",
        "GraphQL",
        "framework",
        "web_backend",
        deps=(
            "strawberry-graphql",
            "graphene",
            "ariadne",
            "graphql",
            "@apollo/server",
            "apollo-server",
            "graphql-yoga",
            "type-graphql",
            "github.com/99designs/gqlgen",
        ),
        imports=("strawberry", "graphene", "ariadne", "@apollo/server", "apollo-server"),
    ),
    _r("uvicorn", "Uvicorn", "framework", "other", deps=("uvicorn",), imports=("uvicorn",)),
    _r("streamlit", "Streamlit", "framework", "web_frontend", deps=("streamlit",), imports=("streamlit",)),
    _r("gradio", "Gradio", "framework", "web_frontend", deps=("gradio",), imports=("gradio",)),
    # js/ts backend
    _r("express", "Express", "framework", "web_backend", deps=("express",), imports=("express",)),
    _r("koa", "Koa", "framework", "web_backend", deps=("koa",), imports=("koa",)),
    _r("fastify", "Fastify", "framework", "web_backend", deps=("fastify",), imports=("fastify",)),
    _r("hapi", "hapi", "framework", "web_backend", deps=("@hapi/hapi",), imports=("@hapi/hapi",)),
    _r(
        "nestjs",
        "NestJS",
        "framework",
        "web_backend",
        deps=("@nestjs/core",),
        imports=("@nestjs/core", "@nestjs/common"),
    ),
    _r("hono", "Hono", "framework", "web_backend", deps=("hono",), imports=("hono",)),
    _r("trpc", "tRPC", "framework", "web_backend", deps=("@trpc/server",), imports=("@trpc/server",)),
    # js/ts frontend
    _r("nextjs", "Next.js", "framework", "web_frontend", deps=("next",), imports=("next",)),
    _r("react", "React", "framework", "web_frontend", deps=("react",), imports=("react",)),
    _r("vue", "Vue", "framework", "web_frontend", deps=("vue",), imports=("vue",)),
    _r("nuxt", "Nuxt", "framework", "web_frontend", deps=("nuxt",), imports=("nuxt", "#app")),
    _r("svelte", "Svelte", "framework", "web_frontend", deps=("svelte",), imports=("svelte",)),
    _r(
        "sveltekit",
        "SvelteKit",
        "framework",
        "web_frontend",
        deps=("@sveltejs/kit",),
        imports=("@sveltejs/kit",),
    ),
    _r(
        "angular", "Angular", "framework", "web_frontend", deps=("@angular/core",), imports=("@angular/core",)
    ),
    _r(
        "remix",
        "Remix",
        "framework",
        "web_frontend",
        deps=("@remix-run/react", "@remix-run/node"),
        imports=("@remix-run",),
    ),
    _r("astro", "Astro", "framework", "web_frontend", deps=("astro",), imports=("astro",)),
    _r("solid", "SolidJS", "framework", "web_frontend", deps=("solid-js",), imports=("solid-js",)),
    _r("preact", "Preact", "framework", "web_frontend", deps=("preact",), imports=("preact",)),
    _r("vite", "Vite", "framework", "other", deps=("vite",)),
    _r("tailwindcss", "Tailwind CSS", "framework", "other", deps=("tailwindcss",)),
    _r("electron", "Electron", "framework", "desktop", deps=("electron",), imports=("electron",)),
    _r(
        "react_native",
        "React Native",
        "framework",
        "mobile",
        deps=("react-native",),
        imports=("react-native",),
    ),
    _r("expo", "Expo", "framework", "mobile", deps=("expo",), imports=("expo",)),
    _r("flutter", "Flutter", "framework", "mobile", deps=("flutter",), imports=("package:flutter",)),
    # go
    _r(
        "gin",
        "Gin",
        "framework",
        "web_backend",
        deps=("github.com/gin-gonic/gin",),
        imports=("github.com/gin-gonic/gin",),
    ),
    _r(
        "chi",
        "chi",
        "framework",
        "web_backend",
        deps=("github.com/go-chi/chi*",),
        imports=("github.com/go-chi/chi",),
    ),
    _r(
        "echo",
        "Echo",
        "framework",
        "web_backend",
        deps=("github.com/labstack/echo*",),
        imports=("github.com/labstack/echo",),
    ),
    _r(
        "fiber",
        "Fiber",
        "framework",
        "web_backend",
        deps=("github.com/gofiber/fiber*",),
        imports=("github.com/gofiber/fiber",),
    ),
    _r(
        "gorilla_mux",
        "gorilla/mux",
        "framework",
        "web_backend",
        deps=("github.com/gorilla/mux",),
        imports=("github.com/gorilla/mux",),
    ),
    _r(
        "cobra",
        "Cobra",
        "framework",
        "cli",
        deps=("github.com/spf13/cobra",),
        imports=("github.com/spf13/cobra",),
    ),
    # jvm
    _r(
        "spring_boot",
        "Spring Boot",
        "framework",
        "web_backend",
        deps=("org.springframework.boot*",),
        imports=("org.springframework.boot",),
    ),
    _r(
        "spring",
        "Spring Framework",
        "framework",
        "web_backend",
        deps=("org.springframework:*",),
        imports=("org.springframework",),
    ),
    _r("quarkus", "Quarkus", "framework", "web_backend", deps=("io.quarkus*",), imports=("io.quarkus",)),
    _r(
        "micronaut",
        "Micronaut",
        "framework",
        "web_backend",
        deps=("io.micronaut*",),
        imports=("io.micronaut",),
    ),
    _r("ktor", "Ktor", "framework", "web_backend", deps=("io.ktor*",), imports=("io.ktor",)),
    # rust
    _r("actix_web", "Actix Web", "framework", "web_backend", deps=("actix-web",), imports=("actix_web",)),
    _r("axum", "Axum", "framework", "web_backend", deps=("axum",), imports=("axum",)),
    _r("rocket", "Rocket", "framework", "web_backend", deps=("rocket",), imports=("rocket",)),
    _r("clap", "clap", "framework", "cli", deps=("clap",), imports=("clap",)),
    _r("tokio", "Tokio", "framework", "other", deps=("tokio",), imports=("tokio",)),
    # php / ruby / dotnet
    _r(
        "laravel", "Laravel", "framework", "web_backend", deps=("laravel/framework",), imports=("Illuminate",)
    ),
    _r(
        "symfony",
        "Symfony",
        "framework",
        "web_backend",
        deps=("symfony/framework-bundle", "symfony/http-kernel"),
        imports=("Symfony",),
    ),
    _r("rails", "Ruby on Rails", "framework", "web_backend", deps=("rails",)),
    _r("sinatra", "Sinatra", "framework", "web_backend", deps=("sinatra",)),
    _r(
        "aspnet_core",
        "ASP.NET Core",
        "framework",
        "web_backend",
        deps=("microsoft.aspnetcore*",),
        imports=("Microsoft.AspNetCore",),
    ),
    # cli
    _r("click", "Click", "framework", "cli", deps=("click",), imports=("click",)),
    _r("typer", "Typer", "framework", "cli", deps=("typer",), imports=("typer",)),
    _r("commander", "Commander.js", "framework", "cli", deps=("commander",), imports=("commander",)),
    _r("yargs", "yargs", "framework", "cli", deps=("yargs",), imports=("yargs",)),
    _r("oclif", "oclif", "framework", "cli", deps=("@oclif/core",), imports=("@oclif/core",)),
    # orm / data access
    _r("sqlalchemy", "SQLAlchemy", "framework", "orm", deps=("sqlalchemy",), imports=("sqlalchemy",)),
    _r("sqlmodel", "SQLModel", "framework", "orm", deps=("sqlmodel",), imports=("sqlmodel",)),
    _r("alembic", "Alembic", "framework", "orm", deps=("alembic",), imports=("alembic",)),
    _r("peewee", "peewee", "framework", "orm", deps=("peewee",), imports=("peewee",)),
    _r("tortoise", "Tortoise ORM", "framework", "orm", deps=("tortoise-orm",), imports=("tortoise",)),
    _r(
        "prisma", "Prisma", "framework", "orm", deps=("@prisma/client", "prisma"), imports=("@prisma/client",)
    ),
    _r("typeorm", "TypeORM", "framework", "orm", deps=("typeorm",), imports=("typeorm",)),
    _r("sequelize", "Sequelize", "framework", "orm", deps=("sequelize",), imports=("sequelize",)),
    _r(
        "mongoose",
        "Mongoose",
        "framework",
        "orm",
        deps=("mongoose",),
        imports=("mongoose",),
        implies=(("mongodb", 0.8),),
    ),
    _r("drizzle", "Drizzle ORM", "framework", "orm", deps=("drizzle-orm",), imports=("drizzle-orm",)),
    _r("knex", "Knex", "framework", "orm", deps=("knex",), imports=("knex",)),
    _r("gorm", "GORM", "framework", "orm", deps=("gorm.io/gorm",), imports=("gorm.io/gorm",)),
    _r(
        "hibernate",
        "Hibernate / JPA",
        "framework",
        "orm",
        deps=(
            "org.hibernate*",
            "org.springframework.boot:spring-boot-starter-data-jpa",
            "jakarta.persistence*",
        ),
        imports=("jakarta.persistence", "javax.persistence", "org.hibernate"),
    ),
    _r("diesel", "Diesel", "framework", "orm", deps=("diesel",), imports=("diesel",)),
    _r(
        "sqlx",
        "SQLx",
        "framework",
        "orm",
        deps=("sqlx", "github.com/jmoiron/sqlx"),
        imports=("sqlx", "github.com/jmoiron/sqlx"),
    ),
    _r(
        "entity_framework",
        "Entity Framework Core",
        "framework",
        "orm",
        deps=("microsoft.entityframeworkcore*",),
        imports=("Microsoft.EntityFrameworkCore",),
    ),
    # testing
    _r("pytest", "pytest", "framework", "testing", deps=("pytest",), imports=("pytest",)),
    _r("jest", "Jest", "framework", "testing", deps=("jest",)),
    _r("vitest", "Vitest", "framework", "testing", deps=("vitest",), imports=("vitest",)),
    _r("mocha", "Mocha", "framework", "testing", deps=("mocha",)),
    _r(
        "playwright",
        "Playwright",
        "framework",
        "testing",
        deps=("@playwright/test", "playwright"),
        imports=("@playwright/test",),
    ),
    _r("cypress", "Cypress", "framework", "testing", deps=("cypress",)),
    _r("supertest", "SuperTest", "framework", "testing", deps=("supertest",), imports=("supertest",)),
    _r("junit", "JUnit", "framework", "testing", deps=("org.junit*", "junit:junit"), imports=("org.junit",)),
    _r(
        "testify",
        "testify",
        "framework",
        "testing",
        deps=("github.com/stretchr/testify",),
        imports=("github.com/stretchr/testify",),
    ),
    _r("rspec", "RSpec", "framework", "testing", deps=("rspec", "rspec-rails")),
    # ml / data
    _r("pytorch", "PyTorch", "framework", "ml", deps=("torch", "pytorch"), imports=("torch",)),
    _r(
        "pytorch_lightning",
        "PyTorch Lightning",
        "framework",
        "ml",
        deps=("pytorch-lightning", "lightning"),
        imports=("pytorch_lightning", "lightning"),
    ),
    _r(
        "tensorflow",
        "TensorFlow",
        "framework",
        "ml",
        deps=("tensorflow", "tensorflow-cpu", "tensorflow-gpu", "@tensorflow/tfjs"),
        imports=("tensorflow", "@tensorflow/tfjs"),
    ),
    _r("keras", "Keras", "framework", "ml", deps=("keras",), imports=("keras",)),
    _r("jax", "JAX", "framework", "ml", deps=("jax", "flax"), imports=("jax", "flax")),
    _r(
        "scikit_learn",
        "scikit-learn",
        "framework",
        "ml",
        deps=("scikit-learn", "sklearn"),
        imports=("sklearn",),
    ),
    _r("xgboost", "XGBoost", "framework", "ml", deps=("xgboost",), imports=("xgboost",)),
    _r("lightgbm", "LightGBM", "framework", "ml", deps=("lightgbm",), imports=("lightgbm",)),
    _r("catboost", "CatBoost", "framework", "ml", deps=("catboost",), imports=("catboost",)),
    _r(
        "transformers",
        "Hugging Face Transformers",
        "framework",
        "ml",
        deps=("transformers",),
        imports=("transformers",),
    ),
    _r("mlflow", "MLflow", "framework", "ml", deps=("mlflow",), imports=("mlflow",)),
    _r("wandb", "Weights & Biases", "framework", "ml", deps=("wandb",), imports=("wandb",)),
    _r("pandas", "pandas", "framework", "data", deps=("pandas",), imports=("pandas",)),
    _r("polars", "Polars", "framework", "data", deps=("polars",), imports=("polars",)),
    _r("airflow", "Apache Airflow", "framework", "data", deps=("apache-airflow",), imports=("airflow",)),
    _r("prefect", "Prefect", "framework", "data", deps=("prefect",), imports=("prefect",)),
    _r("dagster", "Dagster", "framework", "data", deps=("dagster",), imports=("dagster",)),
    _r("luigi", "Luigi", "framework", "data", deps=("luigi",), imports=("luigi",)),
    _r("dbt", "dbt", "framework", "data", deps=("dbt-core", "dbt-postgres", "dbt-bigquery", "dbt-snowflake")),
    _r(
        "spark",
        "Apache Spark",
        "framework",
        "data",
        deps=("pyspark", "org.apache.spark*"),
        imports=("pyspark", "org.apache.spark"),
    ),
    _r("kedro", "Kedro", "framework", "data", deps=("kedro",), imports=("kedro",)),
    _r("dvc", "DVC", "framework", "data", deps=("dvc",), imports=("dvc",)),
    # llm / agents
    _r(
        "langchain",
        "LangChain",
        "framework",
        "llm",
        deps=(
            "langchain",
            "langchain-core",
            "langchain-community",
            "langchain-openai",
            "@langchain/core",
            "langchain*",
        ),
        imports=(
            "langchain",
            "langchain_core",
            "langchain_community",
            "langchain_openai",
            "@langchain",
            "langchain/",
        ),
    ),
    _r(
        "langgraph",
        "LangGraph",
        "framework",
        "llm",
        deps=("langgraph", "@langchain/langgraph"),
        imports=("langgraph", "@langchain/langgraph"),
    ),
    _r(
        "llama_index",
        "LlamaIndex",
        "framework",
        "llm",
        deps=("llama-index", "llama-index-core", "llamaindex"),
        imports=("llama_index", "llamaindex"),
    ),
    _r(
        "haystack",
        "Haystack",
        "framework",
        "llm",
        deps=("haystack-ai", "farm-haystack"),
        imports=("haystack",),
    ),
    _r("dspy", "DSPy", "framework", "llm", deps=("dspy", "dspy-ai"), imports=("dspy",)),
    _r(
        "semantic_kernel",
        "Semantic Kernel",
        "framework",
        "llm",
        deps=("semantic-kernel",),
        imports=("semantic_kernel",),
    ),
    _r("crewai", "CrewAI", "framework", "llm", deps=("crewai",), imports=("crewai",)),
    _r(
        "autogen",
        "AutoGen",
        "framework",
        "llm",
        deps=("autogen", "pyautogen", "autogen-agentchat"),
        imports=("autogen", "autogen_agentchat"),
    ),
    _r("deepagents", "Deep Agents", "framework", "llm", deps=("deepagents",), imports=("deepagents",)),
    _r(
        "openai_agents", "OpenAI Agents SDK", "framework", "llm", deps=("openai-agents",), imports=("agents",)
    ),
    _r("pydantic_ai", "Pydantic AI", "framework", "llm", deps=("pydantic-ai",), imports=("pydantic_ai",)),
    _r("smolagents", "smolagents", "framework", "llm", deps=("smolagents",), imports=("smolagents",)),
    _r("instructor", "Instructor", "framework", "llm", deps=("instructor",), imports=("instructor",)),
    _r(
        "mcp",
        "Model Context Protocol SDK",
        "framework",
        "llm",
        deps=("mcp", "fastmcp", "@modelcontextprotocol/sdk"),
        imports=("mcp", "fastmcp", "@modelcontextprotocol/sdk"),
    ),
    _r("vercel_ai", "Vercel AI SDK", "framework", "llm", deps=("ai",), imports=("ai",)),
    # validation / misc
    _r("pydantic", "Pydantic", "framework", "other", deps=("pydantic",), imports=("pydantic",)),
    _r("zod", "Zod", "framework", "other", deps=("zod",), imports=("zod",)),
    # infra frameworks (evidenced by files, see detect)
    _r("terraform", "Terraform", "framework", "infra", infra=("terraform",)),
    _r("kubernetes", "Kubernetes", "framework", "infra", infra=("kubernetes",)),
    _r("helm", "Helm", "framework", "infra", infra=("helm",)),
    _r("docker", "Docker", "framework", "infra", infra=("dockerfile",)),
    _r("docker_compose", "Docker Compose", "framework", "infra", infra=("compose",)),
    _r("github_actions", "GitHub Actions", "framework", "infra", infra=("github_actions",)),
    _r("gitlab_ci", "GitLab CI", "framework", "infra", infra=("gitlab_ci",)),
    _r(
        "pulumi",
        "Pulumi",
        "framework",
        "infra",
        deps=("pulumi", "@pulumi/pulumi"),
        imports=("pulumi", "@pulumi/pulumi"),
    ),
    _r(
        "aws_cdk",
        "AWS CDK",
        "framework",
        "infra",
        deps=("aws-cdk-lib", "aws-cdk.core"),
        imports=("aws-cdk-lib", "aws_cdk"),
    ),
    _r(
        "serverless",
        "Serverless Framework",
        "framework",
        "infra",
        deps=("serverless",),
        infra=("serverless",),
    ),
)

# ------------------------------------------------------------------------------------------------ queues

QUEUE_RULES: tuple[TechRule, ...] = (
    _r("celery", "Celery", "queue", "task_queue", deps=("celery",), imports=("celery",)),
    _r("arq", "arq", "queue", "task_queue", deps=("arq",), imports=("arq",), implies=(("redis", 0.6),)),
    _r("rq", "RQ", "queue", "task_queue", deps=("rq",), imports=("rq",), implies=(("redis", 0.6),)),
    _r("dramatiq", "Dramatiq", "queue", "task_queue", deps=("dramatiq",), imports=("dramatiq",)),
    _r("huey", "Huey", "queue", "task_queue", deps=("huey",), imports=("huey",)),
    _r("taskiq", "TaskIQ", "queue", "task_queue", deps=("taskiq",), imports=("taskiq",)),
    _r(
        "bullmq",
        "BullMQ",
        "queue",
        "task_queue",
        deps=("bullmq", "bull"),
        imports=("bullmq", "bull"),
        implies=(("redis", 0.6),),
    ),
    _r("sidekiq", "Sidekiq", "queue", "task_queue", deps=("sidekiq",), implies=(("redis", 0.6),)),
    _r(
        "temporal",
        "Temporal",
        "queue",
        "workflow_engine",
        deps=("temporalio", "@temporalio/client", "go.temporal.io/sdk"),
        imports=("temporalio", "@temporalio", "go.temporal.io/sdk"),
        images=("temporalio/auto-setup", "temporalio/server"),
    ),
    _r(
        "kafka",
        "Apache Kafka",
        "queue",
        "broker",
        deps=(
            "kafka-python",
            "confluent-kafka",
            "aiokafka",
            "kafkajs",
            "github.com/segmentio/kafka-go",
            "github.com/ibm/sarama",
            "github.com/shopify/sarama",
            "org.apache.kafka*",
            "spring-kafka",
        ),
        imports=("kafka", "confluent_kafka", "aiokafka", "kafkajs", "github.com/segmentio/kafka-go"),
        images=(
            "confluentinc/cp-kafka",
            "bitnami/kafka",
            "apache/kafka",
            "redpandadata/redpanda",
            "vectorized/redpanda",
        ),
        infra=("aws_msk_cluster",),
    ),
    _r(
        "rabbitmq",
        "RabbitMQ",
        "queue",
        "broker",
        deps=(
            "pika",
            "aio-pika",
            "amqplib",
            "amqp-connection-manager",
            "kombu",
            "github.com/rabbitmq/amqp091-go",
            "spring-boot-starter-amqp",
        ),
        imports=("pika", "aio_pika", "amqplib", "kombu"),
        images=("rabbitmq",),
        infra=("aws_mq_broker",),
    ),
    _r(
        "nats",
        "NATS",
        "queue",
        "broker",
        deps=("nats-py", "nats", "github.com/nats-io/nats.go"),
        imports=("nats", "github.com/nats-io/nats.go"),
        images=("nats",),
    ),
    _r(
        "sqs",
        "Amazon SQS",
        "queue",
        "broker",
        deps=("@aws-sdk/client-sqs",),
        imports=("@aws-sdk/client-sqs",),
        infra=("aws_sqs_queue",),
    ),
    _r(
        "sns",
        "Amazon SNS",
        "queue",
        "broker",
        deps=("@aws-sdk/client-sns",),
        imports=("@aws-sdk/client-sns",),
        infra=("aws_sns_topic",),
    ),
    _r(
        "pubsub",
        "Google Cloud Pub/Sub",
        "queue",
        "broker",
        deps=("google-cloud-pubsub", "@google-cloud/pubsub", "cloud.google.com/go/pubsub"),
        imports=("google.cloud.pubsub", "@google-cloud/pubsub", "cloud.google.com/go/pubsub"),
        infra=("google_pubsub_topic", "google_pubsub_subscription"),
    ),
)

# ------------------------------------------------------------------------------------------------ datastores

DATASTORE_RULES: tuple[TechRule, ...] = (
    _r(
        "postgresql",
        "PostgreSQL",
        "datastore",
        "relational",
        deps=(
            "psycopg2",
            "psycopg2-binary",
            "psycopg",
            "psycopg-binary",
            "asyncpg",
            "pg",
            "pg-promise",
            "postgres",
            "github.com/jackc/pgx*",
            "github.com/lib/pq",
            "org.postgresql:postgresql",
            "npgsql",
            "npgsql.entityframeworkcore.postgresql",
            "tokio-postgres",
        ),
        imports=("psycopg2", "psycopg", "asyncpg", "pg", "github.com/jackc/pgx", "github.com/lib/pq"),
        images=(
            "postgres",
            "postgis/postgis",
            "bitnami/postgresql",
            "pgvector/pgvector",
            "timescale/timescaledb",
            "supabase/postgres",
        ),
        infra=(
            "prisma:postgresql",
            "prisma:postgres",
            "google_sql_database_instance:postgres",
            "aws_db_instance:postgres",
            "aws_rds_cluster:postgres",
            "azurerm_postgresql_server",
            "azurerm_postgresql_flexible_server",
        ),
    ),
    _r(
        "mysql",
        "MySQL",
        "datastore",
        "relational",
        deps=(
            "mysqlclient",
            "pymysql",
            "aiomysql",
            "asyncmy",
            "mysql-connector-python",
            "mysql2",
            "mysql",
            "github.com/go-sql-driver/mysql",
            "mysql:mysql-connector-java",
            "com.mysql:mysql-connector-j",
        ),
        imports=("MySQLdb", "pymysql", "aiomysql", "mysql2", "github.com/go-sql-driver/mysql"),
        images=("mysql", "mariadb", "bitnami/mysql"),
        infra=(
            "prisma:mysql",
            "google_sql_database_instance:mysql",
            "aws_db_instance:mysql",
            "aws_db_instance:mariadb",
            "aws_rds_cluster:mysql",
            "azurerm_mysql_server",
            "azurerm_mysql_flexible_server",
        ),
    ),
    _r(
        "sqlite",
        "SQLite",
        "datastore",
        "relational",
        deps=("aiosqlite", "better-sqlite3", "sqlite3", "github.com/mattn/go-sqlite3", "rusqlite"),
        imports=("sqlite3", "aiosqlite", "better-sqlite3", "github.com/mattn/go-sqlite3"),
        infra=("prisma:sqlite",),
    ),
    _r(
        "sqlserver",
        "SQL Server",
        "datastore",
        "relational",
        deps=("pyodbc", "pymssql", "mssql", "tedious", "microsoft.entityframeworkcore.sqlserver"),
        imports=("pyodbc", "pymssql", "mssql"),
        images=("mcr.microsoft.com/mssql/server",),
        infra=("prisma:sqlserver", "azurerm_mssql_server", "azurerm_sql_server"),
    ),
    _r(
        "mongodb",
        "MongoDB",
        "datastore",
        "document",
        deps=("pymongo", "motor", "mongoengine", "beanie", "mongodb", "go.mongodb.org/mongo-driver"),
        imports=("pymongo", "motor", "mongoengine", "beanie", "mongodb", "go.mongodb.org/mongo-driver"),
        images=("mongo", "bitnami/mongodb"),
        infra=("prisma:mongodb", "mongodbatlas_cluster"),
    ),
    _r(
        "redis",
        "Redis",
        "datastore",
        "cache",
        deps=(
            "redis",
            "aioredis",
            "ioredis",
            "github.com/redis/go-redis*",
            "github.com/go-redis/redis*",
            "redis-py",
            "spring-boot-starter-data-redis",
            "stackexchange.redis",
        ),
        imports=("redis", "aioredis", "ioredis", "github.com/redis/go-redis", "github.com/go-redis/redis"),
        images=("redis", "bitnami/redis", "redis/redis-stack", "valkey/valkey", "eqalpha/keydb"),
        infra=(
            "google_redis_instance",
            "aws_elasticache_cluster",
            "aws_elasticache_replication_group",
            "azurerm_redis_cache",
        ),
    ),
    _r(
        "memcached",
        "Memcached",
        "datastore",
        "cache",
        deps=("pymemcache", "python-memcached", "memcached"),
        imports=("pymemcache", "memcache"),
        images=("memcached",),
    ),
    _r(
        "elasticsearch",
        "Elasticsearch",
        "datastore",
        "search",
        deps=("elasticsearch", "@elastic/elasticsearch", "elasticsearch-dsl"),
        imports=("elasticsearch", "@elastic/elasticsearch"),
        images=("elasticsearch", "docker.elastic.co/elasticsearch/elasticsearch"),
        infra=("aws_elasticsearch_domain",),
    ),
    _r(
        "opensearch",
        "OpenSearch",
        "datastore",
        "search",
        deps=("opensearch-py", "@opensearch-project/opensearch"),
        imports=("opensearchpy", "@opensearch-project/opensearch"),
        images=("opensearchproject/opensearch",),
        infra=("aws_opensearch_domain",),
    ),
    _r(
        "meilisearch",
        "Meilisearch",
        "datastore",
        "search",
        deps=("meilisearch",),
        imports=("meilisearch",),
        images=("getmeili/meilisearch",),
    ),
    _r(
        "neo4j",
        "Neo4j",
        "datastore",
        "graph",
        deps=("neo4j", "py2neo", "neo4j-driver", "neomodel"),
        imports=("neo4j", "py2neo", "neo4j-driver", "neomodel"),
        images=("neo4j",),
    ),
    _r(
        "qdrant",
        "Qdrant",
        "datastore",
        "vector",
        deps=("qdrant-client", "@qdrant/js-client-rest", "langchain-qdrant"),
        imports=("qdrant_client", "@qdrant/js-client-rest"),
        images=("qdrant/qdrant",),
    ),
    _r(
        "chroma",
        "Chroma",
        "datastore",
        "vector",
        deps=("chromadb", "langchain-chroma"),
        imports=("chromadb",),
        images=("chromadb/chroma",),
    ),
    _r(
        "pinecone",
        "Pinecone",
        "datastore",
        "vector",
        deps=("pinecone-client", "pinecone", "@pinecone-database/pinecone"),
        imports=("pinecone", "@pinecone-database/pinecone"),
    ),
    _r(
        "weaviate",
        "Weaviate",
        "datastore",
        "vector",
        deps=("weaviate-client", "weaviate-ts-client"),
        imports=("weaviate",),
        images=("semitechnologies/weaviate", "cr.weaviate.io/semitechnologies/weaviate"),
    ),
    _r(
        "milvus",
        "Milvus",
        "datastore",
        "vector",
        deps=("pymilvus",),
        imports=("pymilvus",),
        images=("milvusdb/milvus",),
    ),
    _r(
        "pgvector",
        "pgvector",
        "datastore",
        "vector",
        deps=("pgvector",),
        imports=("pgvector",),
        images=("pgvector/pgvector",),
    ),
    _r("faiss", "FAISS", "datastore", "vector", deps=("faiss-cpu", "faiss-gpu", "faiss"), imports=("faiss",)),
    _r("lancedb", "LanceDB", "datastore", "vector", deps=("lancedb",), imports=("lancedb",)),
    _r(
        "cassandra",
        "Cassandra",
        "datastore",
        "document",
        deps=("cassandra-driver",),
        imports=("cassandra",),
        images=("cassandra", "scylladb/scylla"),
    ),
    _r(
        "dynamodb",
        "DynamoDB",
        "datastore",
        "key_value",
        deps=("@aws-sdk/client-dynamodb", "pynamodb"),
        imports=("@aws-sdk/client-dynamodb", "pynamodb"),
        images=("amazon/dynamodb-local",),
        infra=("aws_dynamodb_table",),
    ),
    _r(
        "clickhouse",
        "ClickHouse",
        "datastore",
        "relational",
        deps=("clickhouse-driver", "clickhouse-connect", "@clickhouse/client"),
        imports=("clickhouse_driver", "clickhouse_connect", "@clickhouse/client"),
        images=("clickhouse/clickhouse-server", "yandex/clickhouse-server"),
    ),
    _r("duckdb", "DuckDB", "datastore", "relational", deps=("duckdb",), imports=("duckdb",)),
    _r(
        "bigquery",
        "BigQuery",
        "datastore",
        "relational",
        deps=("google-cloud-bigquery", "@google-cloud/bigquery"),
        imports=("google.cloud.bigquery", "@google-cloud/bigquery"),
        infra=("google_bigquery_dataset", "google_bigquery_table"),
    ),
    _r(
        "snowflake",
        "Snowflake",
        "datastore",
        "relational",
        deps=("snowflake-connector-python", "snowflake-sqlalchemy"),
        imports=("snowflake",),
    ),
    _r(
        "s3",
        "Amazon S3",
        "datastore",
        "object",
        deps=("@aws-sdk/client-s3", "s3fs", "aioboto3"),
        imports=("@aws-sdk/client-s3", "s3fs"),
        infra=("aws_s3_bucket",),
    ),
    _r(
        "gcs",
        "Google Cloud Storage",
        "datastore",
        "object",
        deps=("google-cloud-storage", "@google-cloud/storage", "gcsfs"),
        imports=("google.cloud.storage", "@google-cloud/storage"),
        infra=("google_storage_bucket",),
    ),
    _r(
        "azure_blob",
        "Azure Blob Storage",
        "datastore",
        "object",
        deps=("azure-storage-blob", "@azure/storage-blob"),
        imports=("azure.storage.blob", "@azure/storage-blob"),
        infra=("azurerm_storage_account",),
    ),
    _r(
        "minio",
        "MinIO",
        "datastore",
        "object",
        deps=("minio",),
        imports=("minio",),
        images=("minio/minio", "bitnami/minio", "quay.io/minio/minio"),
    ),
    _r(
        "firestore",
        "Firestore",
        "datastore",
        "document",
        deps=("google-cloud-firestore", "firebase-admin", "firebase"),
        imports=("google.cloud.firestore", "firebase_admin", "firebase/firestore"),
        infra=("google_firestore_database",),
    ),
    _r(
        "supabase",
        "Supabase",
        "datastore",
        "relational",
        deps=("supabase", "@supabase/supabase-js"),
        imports=("supabase", "@supabase/supabase-js"),
    ),
    _r(
        "cloud_spanner",
        "Cloud Spanner",
        "datastore",
        "relational",
        deps=("google-cloud-spanner",),
        imports=("google.cloud.spanner",),
        infra=("google_spanner_instance",),
    ),
)

# ------------------------------------------------------------------------------------------------ external services

SERVICE_RULES: tuple[TechRule, ...] = (
    _r(
        "openai",
        "OpenAI API",
        "external_service",
        "llm_api",
        deps=("openai", "langchain-openai", "@langchain/openai", "@ai-sdk/openai"),
        imports=("openai", "langchain_openai", "@langchain/openai", "@ai-sdk/openai"),
    ),
    _r(
        "anthropic",
        "Anthropic API",
        "external_service",
        "llm_api",
        deps=("anthropic", "@anthropic-ai/sdk", "langchain-anthropic", "@ai-sdk/anthropic"),
        imports=("anthropic", "@anthropic-ai/sdk", "langchain_anthropic", "@ai-sdk/anthropic"),
    ),
    _r(
        "gemini",
        "Google Gemini API",
        "external_service",
        "llm_api",
        deps=(
            "google-generativeai",
            "google-genai",
            "@google/generative-ai",
            "@google/genai",
            "langchain-google-genai",
            "@ai-sdk/google",
        ),
        imports=(
            "google.generativeai",
            "google.genai",
            "@google/generative-ai",
            "@google/genai",
            "langchain_google_genai",
        ),
    ),
    _r(
        "vertex_ai",
        "Google Vertex AI",
        "external_service",
        "llm_api",
        deps=("google-cloud-aiplatform", "vertexai", "@google-cloud/vertexai"),
        imports=("vertexai", "google.cloud.aiplatform", "@google-cloud/vertexai"),
        infra=("google_vertex_ai_endpoint",),
    ),
    _r(
        "groq",
        "Groq API",
        "external_service",
        "llm_api",
        deps=("groq", "groq-sdk", "langchain-groq"),
        imports=("groq", "groq-sdk", "langchain_groq"),
    ),
    _r(
        "mistral",
        "Mistral API",
        "external_service",
        "llm_api",
        deps=("mistralai", "@mistralai/mistralai"),
        imports=("mistralai", "@mistralai/mistralai"),
    ),
    _r(
        "cohere",
        "Cohere API",
        "external_service",
        "llm_api",
        deps=("cohere", "cohere-ai"),
        imports=("cohere", "cohere-ai"),
    ),
    _r(
        "aws_bedrock",
        "AWS Bedrock",
        "external_service",
        "llm_api",
        deps=("@aws-sdk/client-bedrock-runtime", "langchain-aws"),
        imports=("@aws-sdk/client-bedrock-runtime", "langchain_aws"),
    ),
    _r(
        "azure_openai",
        "Azure OpenAI",
        "external_service",
        "llm_api",
        deps=("@azure/openai",),
        imports=("@azure/openai",),
    ),
    _r(
        "ollama",
        "Ollama",
        "external_service",
        "llm_api",
        deps=("ollama", "langchain-ollama"),
        imports=("ollama", "langchain_ollama"),
        images=("ollama/ollama",),
    ),
    _r(
        "huggingface_hub",
        "Hugging Face Hub",
        "external_service",
        "llm_api",
        deps=("huggingface-hub", "@huggingface/inference"),
        imports=("huggingface_hub", "@huggingface/inference"),
    ),
    _r("replicate", "Replicate", "external_service", "llm_api", deps=("replicate",), imports=("replicate",)),
    _r("litellm", "LiteLLM", "external_service", "llm_api", deps=("litellm",), imports=("litellm",)),
    _r(
        "stripe",
        "Stripe",
        "external_service",
        "payments",
        deps=("stripe", "@stripe/stripe-js", "github.com/stripe/stripe-go*"),
        imports=("stripe", "@stripe/stripe-js", "github.com/stripe/stripe-go"),
    ),
    _r(
        "paypal",
        "PayPal",
        "external_service",
        "payments",
        deps=("paypalrestsdk", "@paypal/checkout-server-sdk"),
        imports=("paypalrestsdk", "@paypal/checkout-server-sdk"),
    ),
    _r(
        "auth0",
        "Auth0",
        "external_service",
        "auth",
        deps=("auth0-python", "@auth0/auth0-react", "@auth0/nextjs-auth0", "auth0"),
        imports=("auth0", "@auth0"),
    ),
    _r(
        "clerk",
        "Clerk",
        "external_service",
        "auth",
        deps=("@clerk/nextjs", "@clerk/clerk-react", "@clerk/express"),
        imports=("@clerk",),
    ),
    _r(
        "firebase_auth",
        "Firebase",
        "external_service",
        "auth",
        deps=("firebase-admin", "firebase"),
        imports=("firebase_admin", "firebase/auth", "firebase/app"),
    ),
    _r(
        "nextauth",
        "NextAuth.js",
        "external_service",
        "auth",
        deps=("next-auth", "@auth/core"),
        imports=("next-auth", "@auth/core"),
    ),
    _r(
        "sendgrid",
        "SendGrid",
        "external_service",
        "email",
        deps=("sendgrid", "@sendgrid/mail"),
        imports=("sendgrid", "@sendgrid/mail"),
    ),
    _r(
        "mailgun",
        "Mailgun",
        "external_service",
        "email",
        deps=("mailgun", "mailgun.js", "mailgun-js"),
        imports=("mailgun.js", "mailgun-js"),
    ),
    _r(
        "postmark",
        "Postmark",
        "external_service",
        "email",
        deps=("postmarker", "postmark"),
        imports=("postmarker", "postmark"),
    ),
    _r("resend", "Resend", "external_service", "email", deps=("resend",), imports=("resend",)),
    _r(
        "smtp",
        "SMTP email",
        "external_service",
        "email",
        deps=("nodemailer", "aiosmtplib"),
        imports=("nodemailer", "aiosmtplib", "smtplib"),
    ),
    _r("twilio", "Twilio", "external_service", "messaging", deps=("twilio",), imports=("twilio",)),
    _r(
        "slack",
        "Slack API",
        "external_service",
        "messaging",
        deps=("slack-sdk", "slack_sdk", "slack-bolt", "@slack/web-api", "@slack/bolt"),
        imports=("slack_sdk", "slack_bolt", "@slack/web-api", "@slack/bolt"),
    ),
    _r(
        "discord",
        "Discord API",
        "external_service",
        "messaging",
        deps=("discord.py", "discord-py", "discord.js"),
        imports=("discord",),
    ),
    _r(
        "telegram",
        "Telegram Bot API",
        "external_service",
        "messaging",
        deps=("python-telegram-bot", "aiogram", "telegraf", "node-telegram-bot-api"),
        imports=("telegram", "aiogram", "telegraf"),
    ),
    _r(
        "sentry",
        "Sentry",
        "external_service",
        "monitoring",
        deps=(
            "sentry-sdk",
            "@sentry/node",
            "@sentry/react",
            "@sentry/nextjs",
            "@sentry/browser",
            "github.com/getsentry/sentry-go",
        ),
        imports=("sentry_sdk", "@sentry", "github.com/getsentry/sentry-go"),
    ),
    _r(
        "datadog",
        "Datadog",
        "external_service",
        "monitoring",
        deps=("ddtrace", "datadog", "dd-trace"),
        imports=("ddtrace", "datadog", "dd-trace"),
    ),
    _r("newrelic", "New Relic", "external_service", "monitoring", deps=("newrelic",), imports=("newrelic",)),
    _r(
        "opentelemetry",
        "OpenTelemetry",
        "external_service",
        "monitoring",
        deps=("opentelemetry-*", "@opentelemetry/*", "go.opentelemetry.io/otel*"),
        imports=("opentelemetry", "@opentelemetry", "go.opentelemetry.io/otel"),
    ),
    _r(
        "prometheus",
        "Prometheus",
        "external_service",
        "monitoring",
        deps=("prometheus-client", "prometheus_client", "prom-client", "github.com/prometheus/client_golang"),
        imports=("prometheus_client", "prom-client", "github.com/prometheus/client_golang"),
        images=("prom/prometheus",),
    ),
    _r(
        "langsmith",
        "LangSmith",
        "external_service",
        "monitoring",
        deps=("langsmith",),
        imports=("langsmith",),
    ),
    _r(
        "aws",
        "Amazon Web Services",
        "external_service",
        "cloud",
        deps=("boto3", "botocore", "aws-sdk", "@aws-sdk/*", "github.com/aws/aws-sdk-go*"),
        imports=("boto3", "botocore", "aws-sdk", "@aws-sdk", "github.com/aws/aws-sdk-go"),
    ),
    _r(
        "gcp",
        "Google Cloud",
        "external_service",
        "cloud",
        deps=("google-cloud-*", "@google-cloud/*", "cloud.google.com/go*"),
        imports=("google.cloud", "@google-cloud", "cloud.google.com/go"),
    ),
    _r(
        "azure",
        "Microsoft Azure",
        "external_service",
        "cloud",
        deps=("azure-*", "@azure/*"),
        imports=("azure", "@azure"),
    ),
    _r(
        "github_api",
        "GitHub API",
        "external_service",
        "developer_platform",
        deps=("pygithub", "@octokit/rest", "@octokit/core", "octokit", "github.com/google/go-github*"),
        imports=("github", "@octokit", "octokit", "github.com/google/go-github"),
    ),
    _r(
        "algolia",
        "Algolia",
        "external_service",
        "search",
        deps=("algoliasearch",),
        imports=("algoliasearch",),
    ),
    _r(
        "cloudinary",
        "Cloudinary",
        "external_service",
        "storage",
        deps=("cloudinary",),
        imports=("cloudinary",),
    ),
    _r(
        "mapbox",
        "Mapbox",
        "external_service",
        "maps",
        deps=("mapbox-gl", "@mapbox/mapbox-sdk"),
        imports=("mapbox-gl", "@mapbox"),
    ),
    _r(
        "google_maps",
        "Google Maps",
        "external_service",
        "maps",
        deps=("googlemaps", "@googlemaps/js-api-loader", "@react-google-maps/api"),
        imports=("googlemaps", "@googlemaps", "@react-google-maps/api"),
    ),
)

ALL_TECH_RULES: tuple[TechRule, ...] = FRAMEWORK_RULES + QUEUE_RULES + DATASTORE_RULES + SERVICE_RULES
RULES_BY_TECH: dict[str, TechRule] = {r.technology: r for r in ALL_TECH_RULES}

# ------------------------------------------------------------------------------------------------ AI components


@dataclass(frozen=True)
class AIRule:
    prefix: str  # import module / dependency prefix
    component_kind: str  # llm_client | embedding | vector_search | lexical_search | reranker | agent | training | inference | dataset | llm_framework | fusion | chunking | experiment_tracking
    technology: str


AI_IMPORT_RULES: tuple[AIRule, ...] = (
    AIRule("openai", "llm_client", "openai"),
    AIRule("anthropic", "llm_client", "anthropic"),
    AIRule("@anthropic-ai/sdk", "llm_client", "anthropic"),
    AIRule("google.generativeai", "llm_client", "gemini"),
    AIRule("google.genai", "llm_client", "gemini"),
    AIRule("@google/generative-ai", "llm_client", "gemini"),
    AIRule("@google/genai", "llm_client", "gemini"),
    AIRule("vertexai", "llm_client", "vertex_ai"),
    AIRule("groq", "llm_client", "groq"),
    AIRule("groq-sdk", "llm_client", "groq"),
    AIRule("mistralai", "llm_client", "mistral"),
    AIRule("cohere", "llm_client", "cohere"),
    AIRule("ollama", "llm_client", "ollama"),
    AIRule("litellm", "llm_client", "litellm"),
    AIRule("langchain_openai", "llm_client", "openai"),
    AIRule("langchain_anthropic", "llm_client", "anthropic"),
    AIRule("langchain_google_genai", "llm_client", "gemini"),
    AIRule("@langchain/openai", "llm_client", "openai"),
    AIRule("@ai-sdk/openai", "llm_client", "openai"),
    AIRule("ai", "llm_client", "vercel_ai"),
    AIRule("langchain", "llm_framework", "langchain"),
    AIRule("langchain_core", "llm_framework", "langchain"),
    AIRule("langchain_community", "llm_framework", "langchain"),
    AIRule("@langchain", "llm_framework", "langchain"),
    AIRule("llama_index", "llm_framework", "llama_index"),
    AIRule("llamaindex", "llm_framework", "llama_index"),
    AIRule("haystack", "llm_framework", "haystack"),
    AIRule("dspy", "llm_framework", "dspy"),
    AIRule("semantic_kernel", "llm_framework", "semantic_kernel"),
    AIRule("instructor", "llm_framework", "instructor"),
    AIRule("langgraph", "agent", "langgraph"),
    AIRule("@langchain/langgraph", "agent", "langgraph"),
    AIRule("crewai", "agent", "crewai"),
    AIRule("autogen", "agent", "autogen"),
    AIRule("autogen_agentchat", "agent", "autogen"),
    AIRule("deepagents", "agent", "deepagents"),
    AIRule("smolagents", "agent", "smolagents"),
    AIRule("pydantic_ai", "agent", "pydantic_ai"),
    AIRule("mcp", "agent", "mcp"),
    AIRule("fastmcp", "agent", "mcp"),
    AIRule("@modelcontextprotocol/sdk", "agent", "mcp"),
    AIRule("sentence_transformers", "embedding", "sentence_transformers"),
    AIRule("fastembed", "embedding", "fastembed"),
    AIRule("voyageai", "embedding", "voyage"),
    AIRule("langchain_huggingface", "embedding", "huggingface"),
    AIRule("qdrant_client", "vector_search", "qdrant"),
    AIRule("@qdrant/js-client-rest", "vector_search", "qdrant"),
    AIRule("faiss", "vector_search", "faiss"),
    AIRule("chromadb", "vector_search", "chroma"),
    AIRule("pinecone", "vector_search", "pinecone"),
    AIRule("@pinecone-database/pinecone", "vector_search", "pinecone"),
    AIRule("weaviate", "vector_search", "weaviate"),
    AIRule("pymilvus", "vector_search", "milvus"),
    AIRule("lancedb", "vector_search", "lancedb"),
    AIRule("pgvector", "vector_search", "pgvector"),
    AIRule("hnswlib", "vector_search", "hnswlib"),
    AIRule("annoy", "vector_search", "annoy"),
    AIRule("usearch", "vector_search", "usearch"),
    AIRule("rank_bm25", "lexical_search", "bm25"),
    AIRule("bm25s", "lexical_search", "bm25"),
    AIRule("pyserini", "lexical_search", "bm25"),
    AIRule("tantivy", "lexical_search", "tantivy"),
    AIRule("whoosh", "lexical_search", "whoosh"),
    AIRule("elasticsearch", "lexical_search", "elasticsearch"),
    AIRule("opensearchpy", "lexical_search", "opensearch"),
    AIRule("rerankers", "reranker", "rerankers"),
    AIRule("flashrank", "reranker", "flashrank"),
    AIRule("torch", "training", "pytorch"),
    AIRule("pytorch_lightning", "training", "pytorch_lightning"),
    AIRule("lightning", "training", "pytorch_lightning"),
    AIRule("tensorflow", "training", "tensorflow"),
    AIRule("keras", "training", "keras"),
    AIRule("jax", "training", "jax"),
    AIRule("sklearn", "training", "scikit_learn"),
    AIRule("xgboost", "training", "xgboost"),
    AIRule("lightgbm", "training", "lightgbm"),
    AIRule("catboost", "training", "catboost"),
    AIRule("peft", "training", "peft"),
    AIRule("trl", "training", "trl"),
    AIRule("accelerate", "training", "accelerate"),
    AIRule("transformers", "inference", "huggingface_transformers"),
    AIRule("vllm", "inference", "vllm"),
    AIRule("onnxruntime", "inference", "onnxruntime"),
    AIRule("llama_cpp", "inference", "llama_cpp"),
    AIRule("spacy", "inference", "spacy"),
    AIRule("datasets", "dataset", "huggingface_datasets"),
    AIRule("mlflow", "experiment_tracking", "mlflow"),
    AIRule("wandb", "experiment_tracking", "wandb"),
)

# Dependency names (canonical) that evidence an AI component even without an import (e.g. only in a manifest).
AI_DEPENDENCY_RULES: tuple[AIRule, ...] = (
    AIRule("sentence-transformers", "embedding", "sentence_transformers"),
    AIRule("fastembed", "embedding", "fastembed"),
    AIRule("qdrant-client", "vector_search", "qdrant"),
    AIRule("faiss-cpu", "vector_search", "faiss"),
    AIRule("faiss-gpu", "vector_search", "faiss"),
    AIRule("chromadb", "vector_search", "chroma"),
    AIRule("pinecone-client", "vector_search", "pinecone"),
    AIRule("pinecone", "vector_search", "pinecone"),
    AIRule("pymilvus", "vector_search", "milvus"),
    AIRule("weaviate-client", "vector_search", "weaviate"),
    AIRule("pgvector", "vector_search", "pgvector"),
    AIRule("rank-bm25", "lexical_search", "bm25"),
    AIRule("bm25s", "lexical_search", "bm25"),
    AIRule("rerankers", "reranker", "rerankers"),
    AIRule("flashrank", "reranker", "flashrank"),
    AIRule("openai", "llm_client", "openai"),
    AIRule("anthropic", "llm_client", "anthropic"),
    AIRule("@anthropic-ai/sdk", "llm_client", "anthropic"),
    AIRule("google-genai", "llm_client", "gemini"),
    AIRule("google-generativeai", "llm_client", "gemini"),
    AIRule("groq", "llm_client", "groq"),
    AIRule("mistralai", "llm_client", "mistral"),
    AIRule("cohere", "llm_client", "cohere"),
    AIRule("ollama", "llm_client", "ollama"),
    AIRule("litellm", "llm_client", "litellm"),
    AIRule("langchain", "llm_framework", "langchain"),
    AIRule("langchain-core", "llm_framework", "langchain"),
    AIRule("llama-index", "llm_framework", "llama_index"),
    AIRule("langgraph", "agent", "langgraph"),
    AIRule("crewai", "agent", "crewai"),
    AIRule("deepagents", "agent", "deepagents"),
    AIRule("torch", "training", "pytorch"),
    AIRule("tensorflow", "training", "tensorflow"),
    AIRule("scikit-learn", "training", "scikit_learn"),
    AIRule("transformers", "inference", "huggingface_transformers"),
    AIRule("vllm", "inference", "vllm"),
)


def module_matches(module: str, prefix: str) -> bool:
    """`module` is `prefix` or a sub-module of it (`.` for Python/Java, `/` for npm scopes and Go paths)."""
    if module == prefix:
        return True
    if prefix.endswith(("/", ".")):
        return module.startswith(prefix)
    return module.startswith((prefix + ".", prefix + "/"))


def match_ai_import(module: str) -> AIRule | None:
    """The most specific AI rule for an import module (longest prefix wins)."""
    best: AIRule | None = None
    for rule in AI_IMPORT_RULES:
        if module_matches(module, rule.prefix) and (best is None or len(rule.prefix) > len(best.prefix)):
            best = rule
    return best


def dep_matches(dep: str, pattern: str) -> bool:
    """Dependency-name match; a trailing `*` in `pattern` matches any suffix (e.g. `org.springframework.boot*`)."""
    if pattern.endswith("*"):
        return dep.startswith(pattern[:-1])
    return dep == pattern


_PEP503 = re.compile(r"[-_.]+")


def canonical_dep_name(name: str, ecosystem: str) -> str:
    """Normalise a dependency name for matching (PEP 503 for PyPI; lowercase for everything else)."""
    name = name.strip()
    if ecosystem == "pypi":
        return _PEP503.sub("-", name).lower()
    return name.lower()


def image_repository(image: str) -> str:
    """`docker.io/library/postgres:16-alpine@sha256:…` → `postgres`; `qdrant/qdrant:v1.9.0` → `qdrant/qdrant`."""
    ref = image.strip().split("@", 1)[0]
    # a ':' after the last '/' is a tag; a ':' before it is a registry port
    last_slash = ref.rfind("/")
    colon = ref.rfind(":")
    if colon > last_slash:
        ref = ref[:colon]
    parts = ref.split("/")
    if len(parts) > 1 and ("." in parts[0] or ":" in parts[0] or parts[0] == "localhost"):
        registry, rest = parts[0], parts[1:]
        if registry in ("docker.io", "index.docker.io", "registry-1.docker.io"):
            parts = rest
        elif registry in ("mcr.microsoft.com", "docker.elastic.co", "quay.io", "cr.weaviate.io"):
            return "/".join(parts).lower()
        else:
            parts = rest
    if len(parts) == 2 and parts[0] == "library":
        parts = parts[1:]
    return "/".join(parts).lower()


# ------------------------------------------------------------------------------------------------ categories

CATEGORY_THRESHOLD = 0.5

PROJECT_CATEGORIES: tuple[str, ...] = (
    "api_service",
    "web_app",
    "frontend_app",
    "fullstack",
    "rag_system",
    "llm_application",
    "agent_system",
    "ml_training",
    "data_pipeline",
    "cli_tool",
    "library",
    "infrastructure",
    "microservices",
    "mobile_app",
    "desktop_app",
    "background_worker",
)

SERVER_RENDERED_FRAMEWORKS = frozenset(
    {
        "django",
        "flask",
        "rails",
        "laravel",
        "symfony",
        "nextjs",
        "nuxt",
        "remix",
        "sveltekit",
        "aspnet_core",
        "spring_boot",
    }
)
CLI_APP_OBJECTS = frozenset(
    {"typer", "click", "argparse", "fire", "cli", "cobra", "commander", "clap", "yargs"}
)
WEB_APP_OBJECTS = frozenset(
    {
        "fastapi",
        "flask",
        "django",
        "starlette",
        "quart",
        "sanic",
        "litestar",
        "django_ninja",
        "aiohttp",
        "express",
        "koa",
        "fastify",
        "nestjs",
        "hono",
        "gin",
        "chi",
        "echo",
        "fiber",
        "spring",
        "http_server",
    }
)
WORKER_APP_OBJECTS = frozenset(
    {"celery", "arq_worker", "rq_worker", "dramatiq", "huey", "taskiq", "bullmq_worker", "consumer"}
)

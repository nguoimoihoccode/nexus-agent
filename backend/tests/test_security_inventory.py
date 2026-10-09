"""Repository-level security assertions for the private-beta baseline."""

import json
import re
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


class SecurityInventoryTests(unittest.TestCase):
    def test_beta_profile_keeps_identity_worker_and_database_boundaries(self) -> None:
        compose = (REPO_ROOT / "docker-compose.beta.yml").read_text(encoding="utf-8")

        for required in (
            "NEXUS_OIDC_ISSUER",
            "NEXUS_OIDC_AUDIENCE",
            "NEXUS_TENANT_HMAC_KEY",
            "NEXUS_QUANT_WORKER_TOKEN",
            "NEXUS_QUANT_DATA_WORKER_TOKEN",
            "LANGGRAPH_AES_KEY",
            "NEXUS_BACKEND_DB_USER",
            "NEXUS_QUANT_DATA_DB_USER",
            "NEXUS_QUANT_WORKER_DB_USER",
            "no-new-privileges:true",
            "read_only: true",
            "pids_limit:",
            "internal: true",
        ):
            self.assertIn(required, compose)
        self.assertGreaterEqual(compose.count("/ready"), 3)

    def test_base_profile_uses_pinned_official_postgres_and_named_worker_volumes(self) -> None:
        compose = (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")

        self.assertRegex(
            compose,
            r"image: postgres:16\.15-alpine3\.24@sha256:[0-9a-f]{64}",
        )
        self.assertNotIn("infra/postgres", compose)
        self.assertIn("quant-artifacts:/data/artifacts", compose)
        self.assertIn("qlib-data:/data/qlib", compose)
        self.assertIn(
            '"${APP_BIND_HOST:-127.0.0.1}:${APP_PORT:-8080}:8080"', compose
        )

    def test_actor_rls_uses_expand_contract_migrations(self) -> None:
        expand = (
            REPO_ROOT / "backend/migrations/domain/0009_actor_rls_expand.up.sql"
        ).read_text(encoding="utf-8")
        contract = (
            REPO_ROOT / "backend/migrations/domain/0010_actor_rls_contract.up.sql"
        ).read_text(encoding="utf-8")
        memory_items = (
            REPO_ROOT / "backend/migrations/domain/0011_user_memory_items.up.sql"
        ).read_text(encoding="utf-8")
        authorization_leases = (
            REPO_ROOT / "backend/migrations/domain/0012_authorization_leases.up.sql"
        ).read_text(encoding="utf-8")

        self.assertIn("nexus.actor_key", expand)
        self.assertIn("CREATE POLICY nexus_actor_isolation", expand)
        self.assertIn("SECURITY DEFINER", expand)
        self.assertIn("session_user", expand)
        self.assertNotIn("BYPASSRLS", expand)
        self.assertNotIn("system:", expand)
        self.assertIn("ENABLE ROW LEVEL SECURITY", contract)
        self.assertIn("CREATE POLICY nexus_actor_isolation", memory_items)
        self.assertIn("ENABLE ROW LEVEL SECURITY", memory_items)
        self.assertIn("CREATE POLICY nexus_actor_isolation", authorization_leases)
        self.assertIn("ENABLE ROW LEVEL SECURITY", authorization_leases)
        self.assertIn("authorization_lease_sensitive_mode", authorization_leases)

    def test_runtime_keeps_strict_serialization_and_auth_first_http(self) -> None:
        dockerfile = (REPO_ROOT / "backend/Dockerfile").read_text(encoding="utf-8")
        config = json.loads(
            (REPO_ROOT / "backend/langgraph.json").read_text(encoding="utf-8")
        )

        self.assertIn("LANGGRAPH_STRICT_MSGPACK='true'", dockerfile)
        self.assertFalse(config["checkpointer"]["serde"]["pickle_fallback"])
        self.assertEqual(config["http"]["middleware_order"], "auth_first")
        self.assertTrue(config["http"]["disable_mcp"])
        self.assertTrue(config["http"]["disable_a2a"])
        self.assertTrue(config["http"]["disable_webhooks"])

    def test_supervisor_prompt_and_memory_are_not_repo_file_backed(self) -> None:
        factory = (REPO_ROOT / "backend/source/agents/factory.py").read_text(
            encoding="utf-8"
        )
        auth = (REPO_ROOT / "backend/source/security/auth.py").read_text(
            encoding="utf-8"
        )

        self.assertIn("SUPERVISOR_SYSTEM_PROMPT", factory)
        self.assertIn("save_user_memory", factory)
        self.assertNotIn("auth.on." + "store", auth)

    def test_removed_infrastructure_dependencies_do_not_return(self) -> None:
        backend_project = (REPO_ROOT / "backend/pyproject.toml").read_text(
            encoding="utf-8"
        )
        worker_project = (REPO_ROOT / "quant-worker/pyproject.toml").read_text(
            encoding="utf-8"
        )

        self.assertIn('"psycopg[binary,pool]', backend_project)
        self.assertIn('"fastapi', worker_project)
        self.assertNotIn('"redis', backend_project)
        self.assertNotIn('"boto3', worker_project)

    def test_demo_profile_keeps_quant_workers_off_the_default_start_set(self) -> None:
        compose = (REPO_ROOT / "docker-compose.demo.yml").read_text(encoding="utf-8")

        # Compose starts a profiled service when an active service depends on
        # it, so the backend's inherited depends_on list has to be replaced
        # rather than merged, and the base profile's backend env_file dropped.
        self.assertIn('profiles: ["quant"]', compose)
        self.assertIn("depends_on: !override", compose)
        self.assertIn("env_file: !reset []", compose)
        self.assertIn("NEXUS_BROWSER_SESSION_AUTH: disabled", compose)
        for required in ("read_only: true", "no-new-privileges:true", "pids_limit:"):
            self.assertIn(required, compose)

    def test_demo_start_set_fits_the_small_host_the_overlay_targets(self) -> None:
        compose = (REPO_ROOT / "docker-compose.demo.yml").read_text(encoding="utf-8")

        # The refresh worker is the least essential service here and the one
        # whose memory the backend most needs, so it stays out of the default
        # start set the way the quant workers do. Both numbers are a budget the
        # deploy overlay's ~1 GB host depends on, not free parameters.
        self.assertIn('profiles: ["refresh"]', compose)
        self.assertIn("${BACKEND_MEMORY_LIMIT:-384m}", compose)

        # `pull` takes an explicit service list rather than resolving profiles,
        # so a profiled-off name must not appear there or the deploy fetches an
        # image nothing runs.
        script = (REPO_ROOT / "deploy/remote-deploy.sh").read_text(encoding="utf-8")
        services = script.split("SERVICES=(", 1)[1].split(")", 1)[0]
        self.assertNotIn("ai-trader-worker-refresh", services)
        self.assertIn("backend", services)

    def test_deploy_overlay_runs_published_images_instead_of_building(self) -> None:
        compose = (REPO_ROOT / "docker-compose.deploy.yml").read_text(encoding="utf-8")

        # Every build section is cleared by the overlay, so a missing `context:`
        # is what proves no service can silently fall back to a local build.
        self.assertNotIn("context:", compose)
        self.assertEqual(compose.count("build: !reset null"), 5)
        self.assertEqual(
            compose.count("${NEXUS_IMAGE_TAG:?NEXUS_IMAGE_TAG is required}"), 5
        )
        # No NGINX_BACKEND_UPSTREAM key: the image default targets the
        # development stage's port, and the beta override would point at a port
        # the demo backend does not listen on.
        self.assertNotIn("NGINX_BACKEND_UPSTREAM:", compose)

    def test_setup_script_checks_its_tooling_before_it_changes_the_host(self) -> None:
        script = (REPO_ROOT / "deploy/setup-vps.sh").read_text(encoding="utf-8")

        # setup-vps.sh runs on a host that already serves other sites, so a
        # missing package has to fail before the script creates the deploy
        # account, clones the checkout, or reloads nginx -- not after. Both
        # checks used to sit at their point of use, which meant a host without
        # apache2-utils got a new user and a checkout before being told no.
        self.assertLess(
            script.index("command -v htpasswd"),
            script.index("Creating the ${DEPLOY_USER} account"),
        )
        self.assertLess(
            script.index("command -v certbot"),
            script.index('log "running the ACME challenge'),
        )

    def test_deploy_workflow_requires_a_green_run_and_proves_the_gate(self) -> None:
        workflow = (REPO_ROOT / ".github/workflows/deploy.yml").read_text(
            encoding="utf-8"
        )

        # workflow_run holds repository secrets even when triggered by a pull
        # request, so both the branch filter and the conclusion check are load
        # bearing.
        self.assertIn("workflows: [\"Quality checks\"]", workflow)
        self.assertIn("branches: [main]", workflow)
        self.assertIn("github.event.workflow_run.conclusion == 'success'", workflow)
        # A started deploy is never cancelled halfway.
        self.assertIn("cancel-in-progress: false", workflow)
        # Verification must fail when the development admin identity becomes
        # reachable without a credential, not only when the stack is down.
        self.assertIn('"$anonymous" = "401"', workflow)
        self.assertIn('"$authenticated" = "200"', workflow)

    def test_deploy_gate_reads_a_variable_the_job_can_actually_see(self) -> None:
        workflow = (REPO_ROOT / ".github/workflows/deploy.yml").read_text(
            encoding="utf-8"
        )
        job = re.search(
            r"\n  deploy:\n(.*?)(?=\n  [a-z][a-z0-9_-]*:\n|\Z)", workflow, re.S
        )
        self.assertIsNotNone(job, "no deploy job found")
        body = job.group(1)
        gate = re.search(r"\n    if: (.+)", body)
        self.assertIsNotNone(gate, "the deploy job has no job-level gate")

        # GitHub evaluates a job-level `if` before it assigns `environment:`,
        # so the `vars` context there holds repository variables only.
        # Gating on VPS_HOST, which the setup guide puts in the Environment,
        # made the condition permanently false: the job reported "skipped" on
        # every run and the deploy never happened, with nothing saying why.
        condition = gate.group(1)
        self.assertNotIn("VPS_HOST", condition)
        self.assertIn("vars.DEPLOY_ENABLED", condition)
        # Secrets still come from the Environment, which is what makes a
        # separately scoped flag necessary rather than redundant.
        self.assertIn("environment: demo", body)

    def test_setup_script_repairs_authorized_keys_holding_the_wrong_key(self) -> None:
        script = (REPO_ROOT / "deploy/setup-vps.sh").read_text(encoding="utf-8")

        # The old version wrote authorized_keys only when nothing was
        # authorized yet, so a host that already trusted a different key kept
        # it forever: the script reported success, and the deploy failed much
        # later with "Permission denied (publickey)". The key has to be
        # appended when it is missing, and what the host accepts has to be
        # printed, or the mismatch stays invisible until a red deploy run.
        self.assertNotIn('> "/home/${DEPLOY_USER}/.ssh/authorized_keys"', script)
        self.assertIn('>> "$AUTHORIZED_KEYS"', script)
        self.assertIn("ssh-keygen -lf", script)

    def test_demo_model_endpoint_is_configurable_and_required(self) -> None:
        compose = (REPO_ROOT / "docker-compose.demo.yml").read_text(encoding="utf-8")

        # The backend reaches the model through langchain's `deepseek:`
        # provider, which reads DEEPSEEK_API_BASE from the environment and
        # otherwise calls DeepSeek's own API. The demo overlay is the only
        # place that can hand it a gateway, and both values must be required
        # rather than defaulted: an empty base URL makes the client build
        # relative URLs instead of falling back, and an unset model falls back
        # to a repository default that no gateway is obliged to serve.
        self.assertIn("${DEEPSEEK_API_BASE:?", compose)
        self.assertIn("${MODEL:?", compose)

        template = (REPO_ROOT / "deploy/.env.example").read_text(encoding="utf-8")
        self.assertIn("MODEL=", template)
        self.assertIn("DEEPSEEK_API_BASE=", template)

    def test_demo_ingress_authenticates_before_reaching_development_admin(self) -> None:
        vhost = (
            REPO_ROOT / "deploy/nginx/nguoimoihoccode.io.vn.conf"
        ).read_text(encoding="utf-8")

        self.assertIn('auth_basic "Nexus demo";', vhost)
        self.assertIn("auth_basic_user_file /etc/nginx/nexus-demo.htpasswd;", vhost)
        self.assertIn("proxy_pass http://127.0.0.1:8080;", vhost)
        # HSTS is remembered for its whole max-age, so it stays commented until
        # HTTPS is confirmed for the complete hostname.
        self.assertIn("# add_header Strict-Transport-Security", vhost)


if __name__ == "__main__":
    unittest.main()

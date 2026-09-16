"""The deploy workflow has to carry the app's whole environment.

Two defects live here and nothing else can see either of them. `bazel test`
does not read .github/, and both failures surface only on a merge to main —
one as a deploy that leaves the service configured wrongly, the other as a
crash loop on a revision nobody is watching.

1. `gcloud run deploy --set-env-vars` REPLACES the service's environment
   rather than adding to it. Every variable the app needs has to be on that
   one line; a value set by hand in the Cloud Run console survives until the
   next deploy and no further.

2. Its default delimiter is a comma, and VR_ALLOWED_EMAILS is a
   comma-separated list. With the default, gcloud splits mid-value and tries
   to set an environment variable named after the second address — which
   fails the deploy if you are lucky and silently truncates the allowlist if
   you are not.

So this reads the workflow and the app's own configuration and checks they
agree, rather than restating either.
"""

import re
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import auth  # noqa: E402
from spa_harness import repo_file  # noqa: E402


def workflow() -> dict:
    with open(repo_file(".github", "workflows", "deploy.yml"), encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def steps() -> list[dict]:
    return workflow()["jobs"]["test-and-deploy"]["steps"]


def deploy_step() -> dict:
    for step in steps():
        if "gcloud run deploy" in (step.get("run") or ""):
            return step
    raise AssertionError("deploy.yml has no step running `gcloud run deploy`")


def set_env_vars() -> str:
    match = re.search(r'--set-env-vars="([^"]*)"', deploy_step()["run"])
    assert match, "the deploy step must pass --set-env-vars=\"...\""
    return match.group(1)


class TestEnvironmentIsComplete:
    def test_every_variable_the_app_reads_is_deployed(self):
        """Derived from auth.py, so adding a knob there and forgetting it here
        is a test failure rather than a surprise in production."""
        spelled = set_env_vars()
        for name in ("VR_AUTH_MODE", "CF_ACCESS_TEAM_DOMAIN", "CF_ACCESS_AUD",
                     "VR_ALLOWED_EMAILS", "GITHUB_TOKEN"):
            assert f"{name}=" in spelled, f"{name} is missing from --set-env-vars"

    def test_the_mode_deployed_is_the_one_that_enforces(self):
        """Not merely present: a deploy pinning VR_AUTH_MODE=disabled would
        pass the check above and serve the whole thing unauthenticated."""
        assert f"VR_AUTH_MODE={auth.MODE_ACCESS}" in set_env_vars()

    def test_auth_py_has_not_grown_a_setting_this_deploy_ignores(self):
        """Every VR_/CF_ACCESS_ variable auth.py reads must be deployed."""
        source = open(repo_file("auth.py"), encoding="utf-8").read()
        read_by_app = set(re.findall(r'env\.get\(\s*"((?:VR_|CF_ACCESS_)[A-Z_]+)"',
                                     source))
        assert read_by_app, "expected auth.py to read its config out of env.get"
        spelled = set_env_vars()
        undeployed = sorted(n for n in read_by_app if f"{n}=" not in spelled)
        assert not undeployed, (
            f"auth.py reads {undeployed} but deploy.yml does not set them")


class TestDelimiter:
    def test_a_custom_delimiter_is_used(self):
        """VR_ALLOWED_EMAILS holds commas, so the comma default cannot be
        left in place. gcloud's form for this is a leading ^DELIM^."""
        spelled = set_env_vars()
        assert re.match(r"^\^[^^]+\^", spelled), (
            "--set-env-vars must start with a ^DELIM^ prefix, because "
            "VR_ALLOWED_EMAILS is itself comma-separated")

    def test_the_delimiter_cannot_occur_in_any_value(self):
        """An `@` would parse as a delimiter and is in every email address;
        a comma is in the allowlist. Whatever is chosen has to be neither."""
        delim = re.match(r"^\^([^^]+)\^", set_env_vars()).group(1)
        assert delim not in ",@.-_:/", (
            f"{delim!r} can appear inside an email address, a team domain or "
            "an AUD tag, so it cannot also delimit them")


class TestGuard:
    def _guard_index(self, all_steps) -> int:
        for i, step in enumerate(all_steps):
            run = step.get("run") or ""
            if "CF_ACCESS_AUD" in run and "exit 1" in run:
                return i
        raise AssertionError(
            "deploy.yml must fail early when the Access configuration is unset")

    def test_the_guard_names_both_required_variables(self):
        guard = steps()[self._guard_index(steps())]
        for name in ("CF_ACCESS_TEAM_DOMAIN", "CF_ACCESS_AUD"):
            assert name in (guard.get("run") or ""), \
                f"the guard does not check {name}"

    def test_the_guard_runs_before_anything_touches_gcp(self):
        """Ordering is the whole value. After the GCP auth step it would still
        fail the job, but only after building and pushing an image, and the
        failure a reader sees would be a container that would not start rather
        than the name of the variable that is missing."""
        all_steps = steps()
        guard = self._guard_index(all_steps)
        gcp = [i for i, s in enumerate(all_steps)
               if "google-github-actions" in str(s.get("uses", ""))]
        assert gcp, "expected the deploy job to authenticate to GCP"
        assert guard < min(gcp), \
            "the Access-configuration guard must come before the GCP steps"

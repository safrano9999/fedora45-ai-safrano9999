#!/usr/bin/env python3
"""Package the pinned Hermes checkout using its own PM/uv build interfaces.

Run only during the GitHub Actions image build. System Python bootstraps PM;
Hermes then supplies its locked Python, uv, Node and isolated environments.
"""
from pathlib import Path
import json
import os
import shutil
import subprocess
import sys
import tempfile


def main():
    root = Path(sys.argv[1]).resolve()
    os.chdir(root)
    sys.path.insert(0, str(root))
    with tempfile.TemporaryDirectory(prefix="hermes-build-") as scratch:
        scratch = Path(scratch)
        os.environ.update(
            HERMES_HOME=str(scratch / "home"),
            HERMES_RUNTIME_DIR=str(root / "tools"),
            CC="gcc", CXX="g++", npm_config_install_links="false",
        )
        from pm import ensure, env_for, installed_package, stage_manager_runtime
        from scripts.bundles.payload import seal_pm_runtime

        for name in ("uv", "chromium", "npm", "ffmpeg", "ripgrep"):
            ensure(name, explicit=True)
        python = installed_package("python").binary.resolve()
        node = installed_package("node").binary.resolve()
        stage_manager_runtime(python=python, destination=root / "pm-runtime", project=root / "pm")
        seal_pm_runtime(root, python)
        env = env_for("npm", base_env=dict(os.environ))
        env["HERMES_PYTHON"] = str(python)

        def run(*args):
            subprocess.run([str(arg) for arg in args], env=env, check=True)

        run(python, "-m", "pm.build_env", "--source", root, "--python", python,
            "--cache", root / "uv-cache", "--out", root / ".venv",
            "--no-install-project", "--sealed", "--record-selection",
            "--extra", "all", "--extra", "messaging")
        app_python = root / ".venv/bin/python"
        env["HERMES_PYTHON"] = str(app_python)
        run(app_python, "-I", "scripts/generate_icons.py", "--source", root,
            "--out", scratch / "icons")
        run(node, "scripts/build/node-deps.mjs", "--source", root,
            "--workspace", "ui-tui", "--workspace", "web")
        run(node, "scripts/build/tui.mjs", "--source", root, "--out", scratch / "tui")
        run(node, "scripts/build/web.mjs", "--source", root,
            "--icons", scratch / "icons", "--out", root / "hermes_cli/web_dist")
        shutil.copytree(scratch / "tui", root / "ui-tui", dirs_exist_ok=True)
        run(app_python, "-c", "from pathlib import Path; from docker.build_agent import assemble_image; assemble_image(Path.cwd())")
        run(python, "scripts/write_install_stamp.py", "--output", root / "install-stamp.json",
            "--commit", os.environ["HERMES_COMMIT"], "--branch", "main",
            "--base-version", os.environ["HERMES_VERSION"], "--distance", os.environ["HERMES_DISTANCE"],
            "--source", "ci", "--distribution", "docker", "--update-mechanism", "external")
        stamp_path = root / "install-stamp.json"
        stamp = json.loads(stamp_path.read_text())
        stamp["pmRuntime"] = str(root / "pm-runtime")
        stamp_path.write_text(json.dumps(stamp, indent=2) + "\n")
        (root / ".install_method").write_text("docker\n")
        (root / ".hermes/bin").mkdir(parents=True, exist_ok=True)
        (root / ".hermes/bin/hermes").symlink_to(root / ".venv/bin/hermes")
        Path("/usr/local/bin/hermes").symlink_to(root / ".venv/bin/hermes")
        # Match upstream: retain TypeScript for runtime linting, not Vite/esbuild.
        typescript = scratch / "typescript"
        shutil.move(root / "node_modules/typescript", typescript)
        for path in (root / "node_modules", root / "web/node_modules", root / "ui-tui/node_modules"):
            shutil.rmtree(path, ignore_errors=True)
        (root / "node_modules/.bin").mkdir(parents=True)
        shutil.move(typescript, root / "node_modules/typescript")
        (root / "node_modules/.bin/tsc").symlink_to("../typescript/bin/tsc")
        run("/usr/local/bin/hermes", "--version")


if __name__ == "__main__":
    main()

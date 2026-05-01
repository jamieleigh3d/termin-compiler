# Copyright 2026 Jamie-Leigh Blake and Termin project contributors
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""Termin CLI: termin compile, termin serve"""

import hashlib
import json
import sys
import uuid
import zipfile
from pathlib import Path

import click

from .peg_parser import parse_peg as parse
from .analyzer import analyze
from .lower import lower
from termin_core.ir.serialize import serialize_ir


@click.group()
def main():
    """Termin: A secure-by-construction application compiler."""
    pass


def _sha256(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _generate_deploy_template(ir_dict: dict, external_channels: list) -> dict:
    """Generate a deploy config template with placeholder env vars.

    v0.9 Phase 3: emits the v0.9-shape `{version, bindings, runtime}`
    config. `bindings.compute` is populated with one entry per
    LLM/agent compute, each binding to the `anthropic` product by
    default. Operators edit the model/api_key after generation.

    The legacy v0.8-shape `{version, channels, identity, runtime,
    ai_provider}` is retired in v0.9 (Q1 of compute-provider-design.md
    resolved hard-cut). The deploy_config.py parser supports both
    shapes today; the *generator* now only emits the v0.9 shape so
    fresh compiles produce the right thing.
    """
    # v0.9 Phase 4: emit provider-based channel bindings.
    # Each channel's `provider_contract` from the IR drives the template shape.
    # Channels without a provider_contract (legacy or internal) fall back to
    # the old url/protocol shape so existing deploy configs aren't broken.
    channels = {}
    for ch in external_channels:
        display = ch["name"]["display"]
        snake = ch["name"]["snake"]
        env_prefix = snake.upper().replace("-", "_")
        provider_contract = ch.get("provider_contract")

        if provider_contract == "webhook":
            channels[display] = {
                "provider": "stub",
                "config": {
                    "target": f"https://TODO-configure-{snake}.example.com/hook",
                    "timeout_ms": 10000,
                    "auth": {"type": "bearer", "token": f"${{{env_prefix}_TOKEN}}"},
                },
            }
        elif provider_contract == "email":
            channels[display] = {
                "provider": "stub",
                "config": {
                    "from": f"noreply@TODO-configure-{snake}.example.com",
                    "api_key": f"${{{env_prefix}_API_KEY}}",
                },
            }
        elif provider_contract == "messaging":
            channels[display] = {
                "provider": "stub",
                "config": {
                    "workspace_token_ref": f"${{{env_prefix}_TOKEN}}",
                    "target": f"TODO-configure-{snake}-channel",
                },
            }
        elif provider_contract == "event-stream":
            channels[display] = {
                "provider": "stub",
                "config": {
                    "transport": "sse",
                    "endpoint_path": f"/streams/{snake}",
                },
            }
        else:
            # No provider_contract declared in source. Emit a stub
            # provider entry so the v0.9 strict channel validator
            # accepts the file (it requires `provider` on every
            # channel binding). Operators replace `"stub"` with the
            # real product name + tighten the config after generation.
            delivery = str(ch.get("delivery", "RELIABLE"))
            protocol = "websocket" if "REALTIME" in delivery else "http"
            inner_config = {
                "url": f"https://TODO-configure-{snake}.example.com/api",
                "protocol": protocol,
                "auth": {"type": "bearer", "token": f"${{{env_prefix}_TOKEN}}"},
            }
            if protocol == "http":
                inner_config["timeout_ms"] = 30000
                inner_config["retry"] = {"max_attempts": 3, "backoff_ms": 1000}
            else:
                inner_config["reconnect"] = True
                inner_config["heartbeat_ms"] = 30000
            channels[display] = {
                "provider": "stub",
                "config": inner_config,
            }

    # Auth provider from IR
    auth = ir_dict.get("auth", {})
    auth_provider = auth.get("provider", "stub")

    # v0.9 Phase 3: per-compute bindings. One entry per LLM/agent
    # compute; CEL computes use the implicit default-CEL contract
    # (no deploy entry needed).
    compute_bindings = {}
    for c in ir_dict.get("computes", []):
        contract = c.get("provider")
        if contract not in ("llm", "ai-agent"):
            continue
        compute_bindings[c["name"]["snake"]] = {
            "provider": "anthropic",
            "config": {
                "model": "claude-haiku-4-5-20251001",
                "api_key": "${ANTHROPIC_API_KEY}",
            },
        }

    return {
        "version": "0.9.0",
        "bindings": {
            "identity": {
                "provider": auth_provider,
                "config": {},
            },
            "storage": {
                "provider": "sqlite",
                "config": {},
            },
            "presentation": {
                "provider": "default",
                "config": {},
            },
            "compute": compute_bindings,
            "channels": channels,
        },
        "runtime": {},
    }


def _compile_source(source_path: Path, format_json: bool = False):
    """Compile a .termin source file. Returns (program, spec, source_text)."""
    source_text = source_path.read_text(encoding="utf-8")

    # Parse
    program, parse_errors = parse(source_text)
    if not parse_errors.ok:
        if format_json:
            click.echo(json.dumps(parse_errors.to_json_list(), indent=2), err=True)
        else:
            click.echo(parse_errors.format(), err=True)
        sys.exit(1)

    # Generate and write back app ID if missing
    if program.application and not program.application.app_id:
        new_id = str(uuid.uuid4())
        program.application.app_id = new_id
        try:
            if source_path.exists() and not source_path.name.startswith("<"):
                lines = source_text.splitlines(keepends=True)
                insert_idx = None
                for idx, line in enumerate(lines):
                    stripped = line.strip().lower()
                    if stripped.startswith("description:"):
                        insert_idx = idx + 1
                    elif stripped.startswith("application:") and insert_idx is None:
                        insert_idx = idx + 1
                if insert_idx is not None:
                    lines.insert(insert_idx, f"Id: {new_id}\n")
                    source_path.write_text("".join(lines), encoding="utf-8")
                    click.echo(f"Generated app ID: {new_id}")
                    source_text = source_path.read_text(encoding="utf-8")
                    program, parse_errors = parse(source_text)
        except (PermissionError, OSError):
            click.echo(f"Warning: Could not write app ID back to {source_path.name} (read-only)", err=True)
            click.echo(f"Add this line to your .termin file: Id: {new_id}", err=True)

    if program.application and not program.application.app_id:
        click.echo("Error: Source file has no Id: field and is not writable.", err=True)
        click.echo("Add an Id: line to your .termin file header (e.g., Id: " + str(uuid.uuid4()) + ")", err=True)
        sys.exit(1)

    # Analyze
    analysis_errors = analyze(program, source_text=source_text)
    if not analysis_errors.ok:
        if format_json:
            click.echo(json.dumps(analysis_errors.to_json_list(), indent=2), err=True)
        else:
            click.echo(analysis_errors.format(), err=True)
        sys.exit(1)

    # Lower to IR
    spec = lower(program)

    return program, spec, source_text


@main.command()
@click.argument("source", type=click.Path(exists=True))
@click.option("-o", "--output", default=None, help="Output path (default: <name>.termin.pkg)")
@click.option("--seed", "seed_path", default=None, type=click.Path(exists=True),
              help="Seed data JSON file to include in package")
@click.option("--assets", "assets_path", default=None, type=click.Path(exists=True),
              help="Assets directory to include in package")
@click.option("--version", "app_version", default=None, help="Set app version (semver)")
@click.option("--emit-ir", "ir_output", default=None, type=click.Path(),
              help="Also dump the IR JSON to this file (for debugging)")
@click.option("--format", "output_format", default=None, type=click.Choice(["json"]),
              help="Error output format: json for machine-readable errors")
def compile(source: str, output: str | None, seed_path: str | None,
            assets_path: str | None, app_version: str | None,
            ir_output: str | None, output_format: str | None):
    """Compile a .termin file into a .termin.pkg package."""
    source_path = Path(source)
    program, spec, source_text = _compile_source(source_path, format_json=(output_format == "json"))

    # Build IR JSON via the shared serializer (cli + tests share
    # this entry point post Phase 2.x retirement of the legacy
    # backend). `ir_dict` is also used below for deploy-template
    # generation, so we round-trip the JSON to dict here once.
    ir_json = serialize_ir(spec)
    ir_dict = json.loads(ir_json)

    # --emit-ir without -o: just dump the IR and exit (no package needed)
    if ir_output and not output:
        ir_path = Path(ir_output)
        ir_path.write_text(ir_json, encoding="utf-8")
        click.echo(f"IR dumped to {ir_path.name}")
        return

    # --emit-ir with -o: dump IR as a side effect of packaging
    if ir_output:
        ir_path = Path(ir_output)
        ir_path.write_text(ir_json, encoding="utf-8")
        click.echo(f"IR dumped to {ir_path.name}")

    # Phase 2.x cleanup: the legacy `.py + .json` codegen path
    # (RuntimeBackend) is retired. `.termin.pkg` is the canonical
    # compile output; deploy with `termin serve <pkg>` or load via
    # `create_termin_app(ir_json)`. .py output is rejected with
    # a clear message rather than silently switching modes.
    if output and Path(output).suffix == ".py":
        raise click.UsageError(
            "Legacy `.py + .json` output (the compiled-script form) "
            "was removed in v0.9. Compile to a `.termin.pkg` and "
            "deploy with `termin serve <pkg>`. If you need the IR "
            "JSON directly, use `--emit-ir <path>.json`."
        )

    # ── Build .termin.pkg ──
    stem = source_path.stem
    pkg_path = Path(output or f"{stem}.termin.pkg")

    # Determine revision: load existing .pkg if present
    revision = 1
    existing_version = "1.0.0"
    if pkg_path.exists():
        try:
            with zipfile.ZipFile(pkg_path, 'r') as zf:
                old_manifest = json.loads(zf.read("manifest.json"))
                old_id = old_manifest.get("app", {}).get("id", "")
                current_id = spec.app_id or ""
                if old_id == current_id:
                    revision = old_manifest.get("app", {}).get("revision", 0) + 1
                    existing_version = old_manifest.get("app", {}).get("version", "1.0.0")
        except (zipfile.BadZipFile, KeyError, json.JSONDecodeError):
            pass  # Corrupted or non-pkg file — start fresh

    # Resolve version
    version = app_version or existing_version

    # Resolve seed data
    seed_data = None
    seed_filename = None
    if seed_path:
        seed_filename = Path(seed_path).name
        seed_data = Path(seed_path).read_bytes()
    else:
        auto_seed = source_path.with_name(source_path.stem + "_seed.json")
        if auto_seed.exists():
            seed_filename = auto_seed.name
            seed_data = auto_seed.read_bytes()

    # Build file contents
    ir_filename = f"{stem}.ir.json"
    source_filename = source_path.name
    ir_bytes = ir_json.encode("utf-8")
    source_bytes = source_text.encode("utf-8")

    # Build checksums
    checksums = {
        ir_filename: _sha256(ir_bytes),
        source_filename: _sha256(source_bytes),
    }
    if seed_data and seed_filename:
        checksums[seed_filename] = _sha256(seed_data)

    # Build manifest
    manifest = {
        "manifest_version": "1.0.0",
        "app": {
            "id": spec.app_id,
            "name": spec.name,
            "version": version,
            "revision": revision,
            "description": spec.description,
        },
        "ir": {
            "version": spec.ir_version,
            "entry": ir_filename,
        },
        "source": {
            "files": [source_filename],
            "entry": source_filename,
        },
        "seed": seed_filename,
        "assets": None,
        "checksums": checksums,
    }

    # Write ZIP
    with zipfile.ZipFile(pkg_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps(manifest, indent=2))
        zf.writestr(ir_filename, ir_json)
        zf.writestr(source_filename, source_text)
        if seed_data and seed_filename:
            zf.writestr(seed_filename, seed_data)
        # TODO: assets directory

    click.echo(f"Compiled {source_path.name} -> {pkg_path.name} "
               f"(v{version} rev{revision}, id={spec.app_id[:8]}...)")

    # Auto-generate deploy config template if app needs one
    deploy_filename = f"{stem}.deploy.json"
    deploy_path = Path(deploy_filename)
    external_channels = [
        ch for ch in ir_dict.get("channels", [])
        if str(ch.get("direction", "")).replace("ChannelDirection.", "") != "INTERNAL"
        and "INTERNAL" not in str(ch.get("direction", ""))
    ]
    llm_computes = [
        c for c in ir_dict.get("computes", [])
        if (c.get("provider") or "") in ("llm", "ai-agent")
    ]
    needs_deploy = bool(external_channels or llm_computes)
    if needs_deploy and not deploy_path.exists():
        # _generate_deploy_template emits the v0.9 shape with
        # bindings.compute populated for LLM/agent computes.
        deploy_template = _generate_deploy_template(ir_dict, external_channels)
        deploy_path.write_text(
            json.dumps(deploy_template, indent=2),
            encoding="utf-8",
        )
        parts = []
        if external_channels:
            parts.append(f"{len(external_channels)} channel(s)")
        if llm_computes:
            parts.append(f"{len(llm_computes)} AI compute(s)")
        click.echo(f"Generated deploy config: {deploy_filename} "
                   f"({', '.join(parts)} — edit before serving)")


@main.command()
@click.argument("package", type=click.Path(exists=True))
@click.option("-p", "--port", default=8000, type=int, help="Port to serve on (default: 8000)")
@click.option("--host", default="0.0.0.0", help="Host to bind to (default: 0.0.0.0)")
@click.option("--deploy", default=None, type=click.Path(exists=True), help="Deploy config file (default: {app_name}.deploy.json)")
@click.option("--no-strict-channels", is_flag=True, help="Allow missing deploy config for external channels (dev mode)")
def serve(package: str, port: int, host: str, deploy: str, no_strict_channels: bool):
    """Serve a .termin.pkg package as a running application."""
    import uvicorn
    from termin_runtime import create_termin_app

    pkg_path = Path(package)

    # Read package
    if pkg_path.suffix == ".pkg" or pkg_path.name.endswith(".termin.pkg"):
        # .termin.pkg format
        with zipfile.ZipFile(pkg_path, 'r') as zf:
            manifest = json.loads(zf.read("manifest.json"))
            ir_json = zf.read(manifest["ir"]["entry"]).decode("utf-8")
            seed_data = None
            if manifest.get("seed"):
                try:
                    seed_json = zf.read(manifest["seed"]).decode("utf-8")
                    seed_data = json.loads(seed_json)
                except (KeyError, json.JSONDecodeError):
                    pass

        app_name = manifest["app"]["name"]
        app_version = manifest["app"]["version"]
        revision = manifest["app"]["revision"]
        click.echo(f"[Termin] Loading {app_name} v{app_version} (rev{revision})")

        # Verify checksums
        with zipfile.ZipFile(pkg_path, 'r') as zf:
            checksums = manifest.get("checksums", {})
            for filename, expected in checksums.items():
                actual = _sha256(zf.read(filename))
                if actual != expected:
                    click.echo(f"WARNING: Checksum mismatch for {filename}", err=True)

    elif pkg_path.suffix == ".json":
        # Raw IR JSON (for development/debugging)
        ir_json = pkg_path.read_text(encoding="utf-8")
        seed_data = None
        seed_path = pkg_path.with_name(pkg_path.stem.replace("_ir", "") + "_seed.json")
        if seed_path.exists():
            seed_data = json.loads(seed_path.read_text(encoding="utf-8"))
        ir = json.loads(ir_json)
        app_name = ir.get("name", "App")
        click.echo(f"[Termin] Loading {app_name} from raw IR")
    else:
        click.echo(f"Error: Unrecognized file type: {pkg_path.name}", err=True)
        click.echo("Expected .termin.pkg or .json", err=True)
        sys.exit(1)

    # Resolve default deploy config from the .pkg filename if the
    # caller didn't pass --deploy. The compiler writes
    # {source_stem}.deploy.json (filename-based), but the runtime's
    # fallback uses the IR app name snake-cased — these can diverge
    # when the app name contains spaces/digits differently from the
    # source file stem (e.g. "Agent Chatbot 2" -> agent_chatbot_2 vs.
    # agent_chatbot2.termin.pkg). Prefer the filename variant here so
    # `termin serve` always finds the sibling deploy config the
    # compiler just wrote.
    if deploy is None and pkg_path.suffix == ".pkg":
        # strip .termin.pkg (or .pkg) to get the source stem
        stem = pkg_path.name
        for suffix in (".termin.pkg", ".pkg"):
            if stem.endswith(suffix):
                stem = stem[: -len(suffix)]
                break
        candidate = pkg_path.parent / f"{stem}.deploy.json"
        if candidate.exists():
            deploy = str(candidate)

    # Create and serve
    try:
        app = create_termin_app(
            ir_json, seed_data=seed_data,
            deploy_config_path=deploy,
            strict_channels=not no_strict_channels,
        )
    except Exception as e:
        if "channel" in str(e).lower() and "deploy config" in str(e).lower():
            click.echo(f"\nError: {e}", err=True)
            click.echo(f"\nTo fix: create a deploy config file or use --no-strict-channels for dev mode.", err=True)
            sys.exit(1)
        raise

    click.echo(f"[Termin] Serving on http://{host}:{port}")
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()

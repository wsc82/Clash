#!/usr/bin/env python3
"""Mirror upstream script bytes and fail a Stash override over to that copy.

The override keeps the author's script URL until the URL 404s, or until
validation fails on ``FAILURE_THRESHOLD`` consecutive runs. A bad download
never replaces a mirrored file or blanks an override. Each source is handled
on its own.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import yaml

from qx_to_stash import convert_qx_text
from stash_sources import FAILURE_THRESHOLD, RAW_PREFIX, raw_url, render_readme
from surge_module_to_stash import ConvertError, convert_sgmodule, non_string_override_fields


STATE_PATH = Path("Stash/mirror-state.json")
USER_AGENT = "wsc82-Clash-stash-sync"
# ddgksf2013.top answers other clients with an HTML page and HTTP 200.
QX_USER_AGENT = "Quantumult%20X/1.5.0"
QX_HOSTS = {"ddgksf2013.top"}
# Quantumult X one-liners such as `$done({body:'{}'})` are valid and shorter
# than a generic page. Empty and HTML bodies are rejected separately, and a
# script that shrinks below one fifth of a 1000-byte copy is still rejected.
JS_MIN_BYTES = 16
OTHER_MIN_BYTES = 32


@dataclass
class FetchResult:
    status: int | None
    body: bytes
    content_type: str
    error: str | None


def user_agent_for(url: str) -> str:
    host = urllib.parse.urlparse(url).hostname or ""
    if host in QX_HOSTS:
        return QX_USER_AGENT
    return USER_AGENT


def fetch_bytes(url: str) -> FetchResult:
    if not url.startswith("https://"):
        return FetchResult(None, b"", "", f"refusing non-https URL: {url}")
    request = urllib.request.Request(url, headers={"User-Agent": user_agent_for(url)})
    last: Exception | None = None
    for attempt in range(5):
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                status = int(getattr(response, "status", 200))
                ctype = response.headers.get("Content-Type", "")
                return FetchResult(status, response.read(), ctype, None)
        except urllib.error.HTTPError as exc:
            body = exc.read() if exc.fp else b""
            ctype = exc.headers.get("Content-Type", "") if exc.headers else ""
            if exc.code >= 500 and attempt < 4:
                last = exc
                time.sleep(2)
                continue
            return FetchResult(exc.code, body, ctype, f"HTTP {exc.code}")
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last = exc
            if attempt < 4:
                time.sleep(2)
                continue
            return FetchResult(None, b"", "", f"network error: {exc}")
    return FetchResult(None, b"", "", f"network error: {last}")


def looks_like_html(body: bytes, content_type: str) -> bool:
    ctype = content_type.lower()
    if "text/html" in ctype or "application/xhtml" in ctype:
        return True
    sample = body.lstrip()[:64].lower()
    return sample.startswith((b"<!doctype", b"<html", b"<head", b"<body"))


def javascript_syntax_error(body: bytes) -> str | None:
    handle = tempfile.NamedTemporaryFile(suffix=".js", delete=False)
    try:
        handle.write(body)
        handle.close()
        try:
            proc = subprocess.run(
                ["node", "--check", handle.name],
                capture_output=True,
                text=True,
                check=False,
            )
        except FileNotFoundError:
            return "node is not available for a JavaScript syntax check"
    finally:
        os.unlink(handle.name)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "node --check failed").strip()
        return detail[:400] or "node --check failed"
    return None


def validate_download(
    body: bytes,
    content_type: str,
    *,
    kind: str,
    previous: bytes | None,
    spdx: str | None = None,
) -> str | None:
    """Return a status token when the body must not replace the current file."""
    if looks_like_html(body, content_type):
        return "html"
    if len(body) == 0:
        return "empty"
    minimum = JS_MIN_BYTES if kind == "js" else OTHER_MIN_BYTES
    if len(body) < minimum:
        return "tiny"
    if (
        kind == "js"
        and previous is not None
        and len(previous) >= 1000
        and len(body) < len(previous) // 5
    ):
        return "tiny"
    if kind == "js":
        syntax = javascript_syntax_error(body)
        if syntax:
            return "syntax"
    if kind == "license" and spdx == "Apache-2.0" and b"Apache License" not in body:
        return "license-text"
    if kind == "license" and spdx == "MIT" and (b"MIT License" not in body or b"Copyright" not in body):
        return "license-text"
    if kind == "module":
        try:
            text = body.decode("utf-8")
        except UnicodeDecodeError:
            return "not-module"
        if "#!name=" not in text and "[Script]" not in text and "[MITM]" not in text:
            return "not-module"
    if kind == "qx":
        try:
            text = body.decode("utf-8")
        except UnicodeDecodeError:
            return "not-qx"
        lowered = text.lower()
        if "hostname" not in lowered and " url " not in text:
            return "not-qx"
    return None


def script_filename(url: str) -> str:
    name = urllib.parse.urlparse(url).path.rsplit("/", 1)[-1]
    if not name or name in {".", ".."}:
        raise ConvertError(f"script URL has no file name: {url}")
    if any(char not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._-" for char in name):
        raise ConvertError(f"script file name is not safe to mirror: {name}")
    return name


def mirror_relpath(author: str, filename: str) -> str:
    if not author or any(char in author for char in "/\\") or author in {".", ".."}:
        raise ConvertError(f"author is not a safe mirror directory name: {author}")
    return f"Stash/scripts/{author}/{filename}"


def load_state(path: Path) -> dict:
    if not path.is_file():
        return {"version": 1, "failure_threshold": FAILURE_THRESHOLD, "sources": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConvertError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("sources"), dict):
        raise ConvertError(f"{path} is missing a sources object")
    data["version"] = 1
    data["failure_threshold"] = FAILURE_THRESHOLD
    return data


def dump_state(state: dict) -> str:
    return json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def write_bytes(path: Path, body: bytes) -> bool:
    if path.is_file() and path.read_bytes() == body:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    try:
        temporary.write_bytes(body)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()
    return True


def write_text(path: Path, content: str) -> bool:
    if path.is_file() and path.read_text(encoding="utf-8") == content:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    try:
        temporary.write_text(content, encoding="utf-8", newline="\n")
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()
    return True


def blank_record(mirror: str | None = None) -> dict:
    record = {
        "consecutive_failures": 0,
        "failover": False,
        "last_error": None,
        "last_status": "ok",
    }
    if mirror is not None:
        record["mirror"] = mirror
    return record


def log(message: str, *, error: bool = False) -> None:
    print(message, flush=True)
    if error:
        print(f"::error::{message}", flush=True)
    elif message.startswith("FAILOVER") or message.startswith("RECOVER"):
        print(f"::warning::{message}", flush=True)


def note_result(
    record: dict,
    status: str,
    error: str | None,
    mirror_ready: bool,
    label: str,
    *,
    switchable: bool,
) -> None:
    previous = bool(record.get("failover"))
    if status == "ok":
        record["consecutive_failures"] = 0
        record["last_error"] = None
        new_failover = False
    else:
        record["consecutive_failures"] = int(record.get("consecutive_failures") or 0) + 1
        record["last_error"] = error
        if not switchable or not mirror_ready:
            new_failover = False
        elif status == "http-404" or record["consecutive_failures"] >= FAILURE_THRESHOLD or previous:
            new_failover = True
        else:
            new_failover = False
    record["last_status"] = status
    record["failover"] = new_failover
    if new_failover != previous and mirror_ready:
        mirror = record.get("mirror")
        if new_failover:
            log(f"FAILOVER {label}: {status} after {record['consecutive_failures']} failure(s); using {raw_url(str(mirror))}")
        else:
            log(f"RECOVER {label}: upstream is valid again; restored the author URL")
    elif status != "ok":
        kept = "kept the previous copy" if mirror_ready else "no mirrored copy yet"
        log(f"UPSTREAM FAILURE {label}: {status}: {error}; {kept}", error=True)


def provider_urls(text: str) -> list[str]:
    loaded = yaml.safe_load(text)
    if not isinstance(loaded, dict):
        raise ConvertError("override is not a YAML mapping")
    providers = loaded.get("script-providers") or {}
    if not isinstance(providers, dict):
        raise ConvertError("script-providers is not a mapping")
    urls: list[str] = []
    for name, item in providers.items():
        if not isinstance(item, dict) or not isinstance(item.get("url"), str):
            raise ConvertError(f"script provider {name} has no url")
        urls.append(item["url"])
    return urls


def override_write_error(text: str) -> str | None:
    """Return why this override must not replace the last good file."""
    try:
        loaded = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        return f"not YAML: {exc}"
    if not isinstance(loaded, dict) or "arguments" in loaded:
        return "not usable"
    return non_string_override_fields(loaded)


def rewrite_script_urls(text: str, scripts: dict) -> str:
    pairs: list[tuple[str, str]] = []
    for upstream, record in scripts.items():
        mirror = record.get("mirror")
        if not isinstance(mirror, str):
            continue
        mirror_url = raw_url(mirror)
        if record.get("failover"):
            pairs.append((upstream, mirror_url))
        else:
            pairs.append((mirror_url, upstream))
    for old, new in sorted(pairs, key=lambda item: len(item[0]), reverse=True):
        if old != new:
            text = text.replace(old, new)
    return text


def string_list(value: object, label: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ConvertError(f"{label} must be a list of strings")
    return list(value)


def refresh_file(
    url: str,
    dest: Path,
    *,
    kind: str,
    spdx: str | None,
    fetch,
) -> tuple[str, str | None]:
    result = fetch(url)
    if result.status == 404:
        return "http-404", result.error or "HTTP 404"
    if result.status is None:
        return "network", result.error or "network error"
    if result.status >= 400:
        return f"http-{result.status}", result.error or f"HTTP {result.status}"
    previous = dest.read_bytes() if dest.is_file() else None
    status = validate_download(
        result.body,
        result.content_type,
        kind=kind,
        previous=previous,
        spdx=spdx,
    )
    if status:
        detail = {
            "html": "response looks like HTML",
            "empty": "response is empty",
            "tiny": f"response is suspiciously small ({len(result.body)} bytes)",
            "syntax": "JavaScript syntax check failed",
            "license-text": "license text does not match the expected license",
            "not-module": "response is not a Surge module",
            "not-qx": "response is not a Quantumult X rewrite",
        }.get(status, status)
        return status, detail
    write_bytes(dest, result.body)
    return "ok", None


def process_source(
    root: Path,
    source: dict[str, object],
    bucket: dict,
    fetch,
    claimed: dict[str, str] | None = None,
) -> bool:
    """Update one source. Return True when upstream failed but the tree is safe."""
    failed = False
    output_rel = str(source["output"])
    author = str(source["author"])
    output_path = root / output_rel
    label = output_rel
    claimed = {} if claimed is None else claimed

    license_url = source.get("license_url")
    license_rel = str(source.get("license_file") or "")
    if license_url and license_rel:
        license_record = bucket.setdefault("license", blank_record(license_rel))
        license_record["mirror"] = license_rel
        status, error = refresh_file(
            str(license_url),
            root / license_rel,
            kind="license",
            spdx=str(source.get("license_spdx") or ""),
            fetch=fetch,
        )
        note_result(
            license_record,
            status,
            error,
            (root / license_rel).is_file(),
            f"{label} license",
            switchable=False,
        )
        if status != "ok":
            failed = True

    notice_url = source.get("notice_url")
    notice_rel = source.get("notice_file")
    if isinstance(notice_url, str) and isinstance(notice_rel, str):
        notice_record = bucket.setdefault("notice", blank_record(notice_rel))
        notice_record["mirror"] = notice_rel
        status, error = refresh_file(
            notice_url,
            root / notice_rel,
            kind="notice",
            spdx=None,
            fetch=fetch,
        )
        note_result(
            notice_record,
            status,
            error,
            (root / notice_rel).is_file(),
            f"{label} notice",
            switchable=False,
        )
        if status != "ok":
            failed = True

    companions = source.get("companion_files") or []
    if not isinstance(companions, list):
        raise ConvertError(f"{label} companion_files must be a list")
    if companions:
        companion_bucket = bucket.setdefault("companions", {})
    for item in companions:
        if not isinstance(item, dict):
            raise ConvertError(f"{label} companion file is not a mapping")
        comp_url = item.get("url")
        comp_rel = item.get("file")
        if not isinstance(comp_url, str) or not isinstance(comp_rel, str):
            raise ConvertError(f"{label} companion file needs url and file")
        if not comp_rel.startswith("Stash/") or ".." in comp_rel.split("/"):
            raise ConvertError(f"{label} companion path is outside Stash/: {comp_rel}")
        comp_record = companion_bucket.setdefault(comp_rel, blank_record(comp_rel))
        comp_record["mirror"] = comp_rel
        comp_spdx = item.get("spdx")
        status, error = refresh_file(
            comp_url,
            root / comp_rel,
            kind=str(item.get("kind") or "license"),
            spdx=comp_spdx if isinstance(comp_spdx, str) else None,
            fetch=fetch,
        )
        note_result(
            comp_record,
            status,
            error,
            (root / comp_rel).is_file(),
            f"{label} {Path(comp_rel).name}",
            switchable=False,
        )
        if status != "ok":
            failed = True

    fresh: str | None = None
    module_record = bucket.setdefault("module", blank_record())
    kind = source.get("kind")
    if kind not in {"surge-sgmodule", "qx-rewrite"}:
        note_result(
            module_record,
            "unmappable",
            f"no converter for kind {kind!r}",
            output_path.is_file(),
            f"{label} module",
            switchable=False,
        )
        failed = True
    else:
        fetched = fetch(str(source["source_url"]))
        module_status = "ok"
        module_error = None
        body = fetched.body
        if fetched.status == 404:
            module_status, module_error = "http-404", fetched.error or "HTTP 404"
        elif fetched.status is None:
            module_status, module_error = "network", fetched.error or "network error"
        elif fetched.status >= 400:
            module_status, module_error = f"http-{fetched.status}", fetched.error or f"HTTP {fetched.status}"
        else:
            download_kind = "module" if kind == "surge-sgmodule" else "qx"
            problem = validate_download(
                body,
                fetched.content_type,
                kind=download_kind,
                previous=None,
            )
            if problem:
                module_status = problem
                module_error = problem
            else:
                try:
                    decoded = body.decode("utf-8")
                    if kind == "surge-sgmodule":
                        fresh = convert_sgmodule(
                            decoded,
                            source_url=str(source["source_url"]),
                            author=author,
                            homepage=str(source["homepage"]),
                            license_spdx=str(source["license_spdx"]),
                            license_file=license_rel,
                            extra_rules=string_list(source.get("extra_rules"), f"{label} extra_rules"),
                            extra_rules_comment=(
                                source["extra_rules_comment"]
                                if isinstance(source.get("extra_rules_comment"), str)
                                else None
                            ),
                        )
                    else:
                        override_name = source.get("name")
                        fresh = convert_qx_text(
                            decoded,
                            str(source["source_url"]),
                            name=override_name if isinstance(override_name, str) else None,
                        )
                except ConvertError as exc:
                    module_status, module_error = "unmappable", str(exc)
                except UnicodeDecodeError as exc:
                    module_status, module_error = "not-module", str(exc)
        note_result(
            module_record,
            module_status,
            module_error,
            output_path.is_file(),
            f"{label} module",
            switchable=False,
        )
        if module_status != "ok":
            failed = True
            log(f"KEEP {label}: module was not applied; the current override was left in place")

    scripts: dict = bucket.setdefault("scripts", {})
    upstream_urls: list[str] = []
    if fresh is not None:
        try:
            upstream_urls = provider_urls(fresh)
        except ConvertError as exc:
            log(f"UPSTREAM FAILURE {label}: {exc}", error=True)
            fresh = None
            failed = True
    else:
        upstream_urls = [url for url in scripts if isinstance(url, str)]
    deduped: list[str] = []
    for url in upstream_urls:
        if url not in deduped:
            deduped.append(url)
    upstream_urls = deduped

    seen_names: dict[str, str] = {}
    for url in upstream_urls:
        if not url.startswith("https://") or url.startswith(RAW_PREFIX):
            # Already a mirror URL, or not a remote script. Upstream URLs come
            # from a fresh conversion; stored keys are upstream URLs.
            if url.startswith(RAW_PREFIX):
                continue
        try:
            filename = script_filename(url)
        except ConvertError as exc:
            log(f"UPSTREAM FAILURE {label}: {exc}", error=True)
            failed = True
            continue
        if seen_names.get(filename) not in {None, url}:
            log(f"UPSTREAM FAILURE {label}: two scripts both want {filename}", error=True)
            failed = True
            continue
        seen_names[filename] = url
        rel = mirror_relpath(author, filename)
        owner = claimed.get(rel)
        if owner not in {None, url}:
            log(f"UPSTREAM FAILURE {label}: {rel} is already mirrored from {owner}", error=True)
            failed = True
            continue
        claimed[rel] = url
        record = scripts.setdefault(url, blank_record(rel))
        record["mirror"] = rel
        status, error = refresh_file(
            url,
            root / rel,
            kind="js",
            spdx=None,
            fetch=fetch,
        )
        # refresh_file runs node --check. Avoid checking twice: validate_download
        # for kind js already checks syntax. refresh_file also checks again.
        note_result(
            record,
            status,
            error,
            (root / rel).is_file(),
            f"{label} {filename}",
            switchable=True,
        )
        if status != "ok":
            failed = True

    base = fresh
    if base is None and output_path.is_file():
        base = output_path.read_text(encoding="utf-8")
    if base is None:
        log(f"KEEP {label}: no override exists yet, so nothing was written", error=True)
        return True
    rewritten = rewrite_script_urls(base, scripts)
    problem = override_write_error(rewritten)
    if problem:
        log(f"UPSTREAM FAILURE {label}: {problem}; kept the previous override", error=True)
        return True
    if write_text(output_path, rewritten):
        log(f"wrote {output_rel}")
    return failed


def sync_sources(root: Path, sources: list[dict[str, object]], fetch=fetch_bytes) -> int:
    state_path = root / STATE_PATH
    state = load_state(state_path)
    buckets: dict = state["sources"]
    failed = False
    seen: set[str] = set()
    claimed: dict[str, str] = {}
    for source in sources:
        output = str(source["output"])
        if output in seen:
            raise ConvertError(f"duplicate output path: {output}")
        seen.add(output)
        if not output.startswith("Stash/") or output.endswith("/README.md") or ".." in output.split("/"):
            raise ConvertError(f"output must be a Stash override: {output}")
        if "hideshorts" in output.casefold():
            raise ConvertError(f"output path must not contain HideShorts: {output}")
        bucket = buckets.setdefault(output, {})
        try:
            if process_source(root, source, bucket, fetch, claimed):
                failed = True
        except ConvertError as exc:
            log(f"UPSTREAM FAILURE {output}: {exc}", error=True)
            failed = True
    readme = render_readme(sources)
    if "HideShorts" in readme or "hideshorts" in readme.casefold():
        raise ConvertError("README must not mention HideShorts")
    for phrase in ("个人备份", "感谢原作者", "00:45", "镜像"):
        if phrase not in readme:
            raise ConvertError(f"README is missing {phrase}")
    if write_text(root / "Stash" / "README.md", readme):
        log("wrote Stash/README.md")
    if write_text(state_path, dump_state(state)):
        log(f"wrote {STATE_PATH.as_posix()}")
    return 2 if failed else 0


def _sample_module() -> str:
    return "\n".join(
        [
            "#!name=Example",
            "#!desc=Example desc",
            "[Script]",
            "demo = type=http-response,pattern=^https://example\\.com/,requires-body=1,binary-body-mode=1,script-path=https://example.com/demo.js",
            "[MITM]",
            "hostname = %APPEND% example.com",
            "",
        ]
    )


def _sample_js(marker: str, size: int = 80) -> bytes:
    body = f"function demo(value) {{ return value + 1; }}\n// {marker}\n".encode()
    if len(body) < size:
        body += b"// " + b"x" * (size - len(body) - 1) + b"\n"
    return body


def mirror_self_check() -> None:
    """Offline failover checks, including a real node --check on the fixture."""
    module_url = "https://example.com/example.sgmodule"
    script_url = "https://example.com/demo.js"
    license_url = "https://example.com/LICENSE"
    good_js = _sample_js("good", 90)
    big_js = _sample_js("big", 1200)
    license_body = b"Apache License\nVersion 2.0\n" + b"terms\n" * 8
    module_body = _sample_module().encode()
    source = {
        "output": "Stash/Example.stoverride",
        "kind": "surge-sgmodule",
        "source_url": module_url,
        "author": "Example",
        "homepage": "https://example.com/home",
        "license_spdx": "Apache-2.0",
        "license_url": license_url,
        "license_file": "Stash/scripts/Example/LICENSE",
        "extra_rules": [],
    }
    files = {
        module_url: FetchResult(200, module_body, "text/plain", None),
        script_url: FetchResult(200, good_js, "text/plain", None),
        license_url: FetchResult(200, license_body, "text/plain", None),
    }

    def fake(url: str) -> FetchResult:
        if url not in files:
            return FetchResult(404, b"missing", "text/plain", "HTTP 404")
        return files[url]

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        assert sync_sources(root, [source], fake) == 0
        override = (root / "Stash/Example.stoverride").read_text(encoding="utf-8")
        mirror = root / "Stash/scripts/Example/demo.js"
        assert script_url in override
        assert raw_url("Stash/scripts/Example/demo.js") not in override
        assert mirror.read_bytes() == good_js
        subprocess.run(["node", "--check", str(mirror)], check=True)
        state = json.loads((root / STATE_PATH).read_text(encoding="utf-8"))
        record = state["sources"]["Stash/Example.stoverride"]["scripts"][script_url]
        assert record["failover"] is False

        files[script_url] = FetchResult(404, b"gone", "text/plain", "HTTP 404")
        assert sync_sources(root, [source], fake) == 2
        failed = (root / "Stash/Example.stoverride").read_text(encoding="utf-8")
        assert raw_url("Stash/scripts/Example/demo.js") in failed
        assert script_url not in failed
        assert mirror.read_bytes() == good_js
        subprocess.run(["node", "--check", str(mirror)], check=True)
        state = json.loads((root / STATE_PATH).read_text(encoding="utf-8"))
        assert state["sources"]["Stash/Example.stoverride"]["scripts"][script_url]["failover"] is True

        files[script_url] = FetchResult(200, b"<html>nope</html>", "text/html", None)
        assert sync_sources(root, [source], fake) == 2
        assert mirror.read_bytes() == good_js

        files[script_url] = FetchResult(200, good_js, "text/plain", None)
        assert sync_sources(root, [source], fake) == 0
        restored = (root / "Stash/Example.stoverride").read_text(encoding="utf-8")
        assert script_url in restored
        state = json.loads((root / STATE_PATH).read_text(encoding="utf-8"))
        assert state["sources"]["Stash/Example.stoverride"]["scripts"][script_url]["failover"] is False

        files[script_url] = FetchResult(200, big_js, "text/plain", None)
        assert sync_sources(root, [source], fake) == 0
        assert mirror.read_bytes() == big_js
        files[script_url] = FetchResult(500, b"busy", "text/plain", "HTTP 500")
        assert sync_sources(root, [source], fake) == 2
        assert script_url in (root / "Stash/Example.stoverride").read_text(encoding="utf-8")
        assert mirror.read_bytes() == big_js
        assert sync_sources(root, [source], fake) == 2
        assert script_url in (root / "Stash/Example.stoverride").read_text(encoding="utf-8")
        assert sync_sources(root, [source], fake) == 2
        assert raw_url("Stash/scripts/Example/demo.js") in (root / "Stash/Example.stoverride").read_text(encoding="utf-8")
        assert mirror.read_bytes() == big_js

        kept_name = "name: Example"
        files[module_url] = FetchResult(404, b"missing module", "text/plain", "HTTP 404")
        before = (root / "Stash/Example.stoverride").read_text(encoding="utf-8")
        assert sync_sources(root, [source], fake) == 2
        after = (root / "Stash/Example.stoverride").read_text(encoding="utf-8")
        assert kept_name in after
        assert "name: Example" in before
        assert mirror.read_bytes() == big_js

    qx_url = "https://ddgksf2013.top/rewrite/Demo.conf"
    bad_url = "https://ddgksf2013.top/rewrite/Bad.conf"
    qx_js_url = "https://ddgksf2013.top/scripts/demo.js"
    qx_js = _sample_js("qx", 90)
    good_conf = "\n".join(
        [
            "// ==UserScript==",
            "// @ScriptName        Demo Ads",
            "// @Author            @ddgksf2013",
            "// @ScriptURL         https://ddgksf2013.top/rewrite/Demo.conf",
            "// @Desc              demo",
            "// @Attention         如需引用请注明出处，谢谢合作！",
            "// ==/UserScript==",
            "hostname = example.com",
            r"^https://example\.com/ad url reject",
            r"^https://example\.com/api url script-response-body https://ddgksf2013.top/scripts/demo.js",
            "",
        ]
    )
    bad_conf = good_conf + r"^https://example\.com/x url echo-response text/plain echo-response https://example.com/x" + "\n"
    good_source = {
        "output": "Stash/DemoAds.stoverride",
        "kind": "qx-rewrite",
        "source_url": qx_url,
        "name": "Demo Ads",
        "author": "ddgksf2013",
        "homepage": "https://github.com/ddgksf2013/Rewrite",
        "attribution": "配置原文要求「如需引用请注明出处」。",
    }
    bad_source = {
        "output": "Stash/BadAds.stoverride",
        "kind": "qx-rewrite",
        "source_url": bad_url,
        "name": "Bad Ads",
        "author": "ddgksf2013",
        "homepage": "https://github.com/ddgksf2013/Rewrite",
    }
    qx_files = {
        qx_url: FetchResult(200, good_conf.encode(), "text/plain", None),
        bad_url: FetchResult(200, bad_conf.encode(), "text/plain", None),
        qx_js_url: FetchResult(200, qx_js, "text/plain", None),
    }

    def qx_fake(url: str) -> FetchResult:
        if url not in qx_files:
            return FetchResult(404, b"missing", "text/plain", "HTTP 404")
        return qx_files[url]

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        previous = "name: Bad Ads\n# previous good copy\n"
        bad_path = root / "Stash/BadAds.stoverride"
        bad_path.parent.mkdir(parents=True)
        bad_path.write_text(previous, encoding="utf-8")
        assert sync_sources(root, [good_source, bad_source], qx_fake) == 2
        good_text = (root / "Stash/DemoAds.stoverride").read_text(encoding="utf-8")
        assert "name: 'Demo Ads'" in good_text
        assert "author: '@ddgksf2013'" in good_text
        assert "# source: https://ddgksf2013.top/rewrite/Demo.conf" in good_text
        assert qx_js_url in good_text
        assert bad_path.read_text(encoding="utf-8") == previous
        mirrored = root / "Stash/scripts/ddgksf2013/demo.js"
        assert mirrored.read_bytes() == qx_js
        subprocess.run(["node", "--check", str(mirrored)], check=True)
        readme = (root / "Stash/README.md").read_text(encoding="utf-8")
        assert "DemoAds.stoverride" in readme and "BadAds.stoverride" in readme
        assert "https://github.com/ddgksf2013/Rewrite" in readme
        qx_files[qx_url] = FetchResult(200, b"<!DOCTYPE html><html></html>", "text/html", None)
        assert sync_sources(root, [good_source, bad_source], qx_fake) == 2
        assert (root / "Stash/DemoAds.stoverride").read_text(encoding="utf-8") == good_text
        assert bad_path.read_text(encoding="utf-8") == previous
        assert mirrored.read_bytes() == qx_js

    one_liner = b"$done({body:'{}'})\n"
    assert validate_download(one_liner, "text/plain", kind="js", previous=None) is None
    assert validate_download(b"error\n", "text/plain", kind="js", previous=None) == "tiny"

    bare_number = "\n".join(
        [
            "name: x",
            "http:",
            "  mitm:",
            "    - example.com",
            "  script:",
            "    - match: '^https://example.com'",
            "      name: 12306",
            "      type: response",
            "script-providers:",
            "  12306:",
            "    url: https://example.com/12306.js",
            "    interval: 1",
            "",
        ]
    )
    assert override_write_error(bare_number)
    quoted_number = bare_number.replace("name: 12306", "name: '12306'").replace("\n  12306:", "\n  '12306':")
    assert override_write_error(quoted_number) is None
    with tempfile.TemporaryDirectory() as tmp:
        kept = Path(tmp) / "Num.stoverride"
        kept.write_text("name: kept\n", encoding="utf-8")
        if override_write_error(bare_number):
            pass
        else:
            kept.write_text(bare_number, encoding="utf-8")
        assert kept.read_text(encoding="utf-8") == "name: kept\n"

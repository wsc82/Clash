#!/usr/bin/env python3
"""Convert a Surge module into a Stash override.

Supported sections are ``[URL Rewrite]``, ``[Map Local]``, ``[Body Rewrite]``,
``[Script]``, and ``[MITM]``. Anything else fails. ``#!arguments`` defaults are
substituted into ``{{{name}}}`` placeholders. Stash's beta ``arguments`` field
is not emitted; script ``argument`` strings already contain the defaults.

Map Local rules that are an empty text 200, ``{}``, or ``[]`` with no extra
headers become ``reject-200``, ``reject-dict``, or ``reject-array``. Any other
Map Local becomes an ``http.mock`` entry. Body rewrite regex and jq rules
become Stash ``body-rewrite`` lines.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

try:
    import yaml
except ImportError as exc:  # pragma: no cover - installed by the workflow
    raise SystemExit("error: PyYAML is required (python3 -m pip install pyyaml)") from exc


SCRIPT_INTERVAL = 86400
# Surge uses max-size=-1 for no limit:
# https://manual.nssurge.com/scripting/http-response.html
# Stash only documents max-size as a byte ceiling. A body larger than that
# value does not trigger the script, and the docs never say that 0 or -1
# means unlimited: https://stash.wiki/en/script/rewrite-requests
# 10 MiB is the explicit ceiling used for Surge's unlimited value.
STASH_MAX_SIZE_UNLIMITED = 10 * 1024 * 1024
LICENSE_URLS = {
    "Apache-2.0": "http://www.apache.org/licenses/LICENSE-2.0",
}

KNOWN_META = {
    "name",
    "desc",
    "arguments",
    "arguments-desc",
    "author",
    "homepage",
    "icon",
    "category",
}
KNOWN_SECTIONS = {"url rewrite", "map local", "body rewrite", "script", "mitm"}
COMMENT_SECTIONS = {"url rewrite", "map local", "body rewrite"}
URL_REWRITE_TYPES = {
    "302",
    "307",
    "transparent",
    "reject",
    "reject-200",
    "reject-img",
    "reject-dict",
    "reject-array",
}
REJECT_TYPES = {item for item in URL_REWRITE_TYPES if item.startswith("reject")}
SCRIPT_TYPES = {"http-response": "response", "http-request": "request"}
SCRIPT_KEYS = {
    "type",
    "pattern",
    "script-path",
    "argument",
    "requires-body",
    "binary-body-mode",
    "max-size",
    "timeout",
}
MAP_LOCAL_KEYS = {"data", "data-type", "status-code", "header"}
PARAM_RE = re.compile(r"(?:^|,)\s*([A-Za-z][A-Za-z0-9-]*)=")
PLACEHOLDER_RE = re.compile(r"\{\{\{([^{}\n]+)\}\}\}")
SCRIPT_LINE_RE = re.compile(r"^([^\s=][^=]*?)\s*=\s*(.+)$")
SCRIPT_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_.-]*")
BANNER_RE = re.compile(r"=+\s*(.*?)\s*=+\s*#?")


class ConvertError(Exception):
    """The module cannot be represented as a Stash override."""


@dataclass
class Comment:
    text: str


@dataclass
class Rule:
    text: str


@dataclass
class Script:
    name: str
    match: str
    type: str
    script_path: str
    require_body: bool | None = None
    binary_mode: bool | None = None
    timeout: int | None = None
    max_size: int | None = None
    argument: str | None = None


@dataclass
class Mock:
    match: str
    status_code: int | None = None
    text: str | None = None
    base64: str | None = None
    headers: list[tuple[str, str]] = field(default_factory=list)


Entry = Comment | Rule


@dataclass
class Model:
    name: str
    desc: str
    author: str
    homepage: str
    icon: str | None
    category: str | None
    mitm: list[str]
    url_rewrite: list[Entry]
    body_rewrite: list[Entry]
    mocks: list[Entry]
    scripts: list[Script]
    source_url: str
    license_spdx: str
    license_file: str
    extra_rules: list[str]
    extra_rules_comment: str | None


def fail(lineno: int, message: str) -> None:
    raise ConvertError(f"line {lineno}: {message}")


def normalize(text: str) -> str:
    if text.startswith("\ufeff"):
        text = text[1:]
    return text.replace("\r\n", "\n").replace("\r", "\n")


def parse_arguments(value: str, lineno: int) -> dict[str, str]:
    """Parse ``#!arguments=name:default,name:default``."""
    defaults: dict[str, str] = {}
    if not value.strip():
        return defaults
    for part in value.split(","):
        if ":" not in part:
            fail(lineno, f"#!arguments entry is missing a colon: {part.strip()}")
        name, default = part.split(":", 1)
        name = name.strip()
        default = default.strip()
        if not name:
            fail(lineno, "#!arguments entry has an empty name")
        if name in defaults:
            fail(lineno, f"duplicate #!arguments name: {name}")
        if "{{{" in default or "}}}" in default:
            fail(lineno, f"#!arguments default for {name} contains a placeholder")
        defaults[name] = default
    return defaults


def substitute(text: str, defaults: dict[str, str]) -> str:
    def replace(match: re.Match[str]) -> str:
        name = match.group(1).strip()
        if name not in defaults:
            raise ConvertError(
                f"placeholder {{{{{name}}}}} is not declared in #!arguments"
            )
        return defaults[name]

    replaced = PLACEHOLDER_RE.sub(replace, text)
    if "{{{" in replaced or "}}}" in replaced:
        raise ConvertError("unresolved {{{...}}} argument placeholder")
    return replaced


def read_quoted(text: str, start: int) -> tuple[str, int]:
    quote = text[start]
    index = start + 1
    buf: list[str] = []
    while index < len(text):
        char = text[index]
        if char == "\\" and quote == '"':
            if index + 1 >= len(text):
                raise ConvertError("trailing backslash in a quoted value")
            nxt = text[index + 1]
            mapping = {"\\": "\\", '"': '"', "'": "'", "n": "\n", "t": "\t"}
            if nxt not in mapping:
                raise ConvertError(f"unsupported escape \\{nxt} in a quoted value")
            buf.append(mapping[nxt])
            index += 2
            continue
        if char == quote:
            return "".join(buf), index + 1
        buf.append(char)
        index += 1
    raise ConvertError("unterminated quote")


def tokenize(text: str) -> list[str]:
    tokens: list[str] = []
    index = 0
    while index < len(text):
        while index < len(text) and text[index].isspace():
            index += 1
        if index >= len(text):
            break
        if text[index] in "\"'":
            value, index = read_quoted(text, index)
            tokens.append(value)
            continue
        start = index
        while index < len(text) and not text[index].isspace():
            index += 1
        tokens.append(text[start:index])
    return tokens


def split_pattern(line: str) -> tuple[str, str]:
    stripped = line.strip()
    if not stripped:
        raise ConvertError("empty rule")
    if stripped[0] in "\"'":
        pattern, index = read_quoted(stripped, 0)
        return pattern, stripped[index:].strip()
    parts = stripped.split(None, 1)
    return parts[0], parts[1].strip() if len(parts) > 1 else ""


def parse_assignments(text: str) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    index = 0
    while index < len(text):
        while index < len(text) and text[index].isspace():
            index += 1
        if index >= len(text):
            break
        eq = text.find("=", index)
        if eq == -1:
            raise ConvertError(f"parameter is missing '=': {text[index:]}")
        key = text[index:eq].strip()
        if not key or any(char.isspace() for char in key):
            raise ConvertError(f"invalid parameter name near {text[index:]}")
        index = eq + 1
        if index < len(text) and text[index] in "\"'":
            value, index = read_quoted(text, index)
        else:
            start = index
            while index < len(text) and not text[index].isspace():
                index += 1
            value = text[start:index]
        pairs.append((key, value))
    return pairs


def normalize_comment(line: str) -> str:
    body = line.strip()
    if body.startswith("//"):
        body = body[2:].strip()
    elif body.startswith("#"):
        body = body[1:].strip()
    elif body.startswith(";"):
        body = body[1:].strip()
    else:
        raise ConvertError(f"unsupported comment: {line}")
    banner = BANNER_RE.fullmatch(body)
    if banner and banner.group(1).strip():
        body = banner.group(1).strip()
    return "# " + body if body else "#"


def is_comment_line(line: str) -> bool:
    return line.startswith("#") or line.startswith("//") or line.startswith(";")


def convert_url_rewrite(line: str) -> str:
    tokens = tokenize(line)
    if len(tokens) == 3 and tokens[2] in URL_REWRITE_TYPES:
        pattern, replacement, kind = tokens
        if not pattern or replacement == "":
            raise ConvertError(f"cannot map URL rewrite rule: {line}")
        return f"{pattern} {replacement} {kind}"
    if len(tokens) == 2 and tokens[1] in REJECT_TYPES:
        if not tokens[0]:
            raise ConvertError(f"cannot map URL rewrite rule: {line}")
        return f"{tokens[0]} - {tokens[1]}"
    raise ConvertError(f"cannot map URL rewrite rule: {line}")


def convert_body_rewrite(line: str) -> str:
    tokens = tokenize(line)
    if not tokens:
        raise ConvertError(f"cannot map body rewrite rule: {line}")
    kind = tokens[0].lower()
    if kind in {"http-request", "http-response"}:
        pairs = tokens[2:]
        if len(tokens) < 4 or len(pairs) % 2 != 0 or not tokens[1]:
            raise ConvertError(f"cannot map body rewrite rule: {line}")
        action = (
            "request-replace-regex" if kind == "http-request" else "response-replace-regex"
        )
        return " ".join([tokens[1], action, *pairs])
    if kind in {"http-request-jq", "http-response-jq"}:
        if len(tokens) < 3 or not tokens[1]:
            raise ConvertError(f"cannot map body rewrite rule: {line}")
        action = "request-jq" if kind == "http-request-jq" else "response-jq"
        expression = " ".join(tokens[2:]).strip()
        if not expression:
            raise ConvertError(f"cannot map body rewrite rule: {line}")
        return f"{tokens[1]} {action} {expression}"
    raise ConvertError(f"cannot map body rewrite rule: {line}")


def parse_status(value: str) -> int:
    if not re.fullmatch(r"\d{3}", value):
        raise ConvertError(f"invalid Map Local status-code: {value}")
    status = int(value)
    if not 100 <= status <= 599:
        raise ConvertError(f"Map Local status-code is out of range: {value}")
    return status


def parse_header(value: str) -> tuple[str, str]:
    if ":" not in value:
        raise ConvertError(f"Map Local header is missing a colon: {value}")
    name, content = value.split(":", 1)
    name = name.strip()
    content = content.strip()
    if not name:
        raise ConvertError(f"Map Local header is missing a name: {value}")
    return name, content


def convert_map_local(line: str) -> Rule | Mock:
    pattern, rest = split_pattern(line)
    if not pattern or not rest:
        raise ConvertError(f"cannot map Map Local rule: {line}")
    try:
        pairs = parse_assignments(rest)
    except ConvertError as exc:
        raise ConvertError(f"cannot map Map Local rule: {line} ({exc})") from exc
    data: str | None = None
    data_type: str | None = None
    status: int | None = None
    headers: list[tuple[str, str]] = []
    for key, value in pairs:
        low = key.lower()
        if low not in MAP_LOCAL_KEYS:
            raise ConvertError(f"unsupported Map Local parameter {key}: {line}")
        if low == "data":
            if data is not None:
                raise ConvertError(f"duplicate Map Local data: {line}")
            data = value
        elif low == "data-type":
            if data_type is not None:
                raise ConvertError(f"duplicate Map Local data-type: {line}")
            data_type = value.strip().lower()
        elif low == "status-code":
            if status is not None:
                raise ConvertError(f"duplicate Map Local status-code: {line}")
            status = parse_status(value.strip())
        else:
            headers.append(parse_header(value))
    if data_type not in {"text", "base64"}:
        raise ConvertError(
            f"Map Local data-type cannot be mapped to Stash"
            + (f" ({data_type})" if data_type else "")
            + f": {line}"
        )
    body = "" if data is None else data
    if data_type == "text" and not headers and (status is None or status == 200):
        stripped = body.strip()
        if stripped == "":
            return Rule(f"{pattern} - reject-200")
        if stripped == "{}":
            return Rule(f"{pattern} - reject-dict")
        if stripped == "[]":
            return Rule(f"{pattern} - reject-array")
    mock = Mock(match=pattern, status_code=status, headers=headers)
    if data_type == "text":
        mock.text = body
    else:
        if data is None:
            raise ConvertError(f"Map Local base64 payload is missing: {line}")
        mock.base64 = body
    return mock


def unquote(value: str) -> str:
    text = value.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        return text[1:-1]
    return text


def parse_bool(value: str, key: str) -> bool:
    low = value.strip().lower()
    if low in {"1", "true"}:
        return True
    if low in {"0", "false"}:
        return False
    raise ConvertError(f"script {key} is not a boolean: {value}")


def parse_script_params(body: str) -> dict[str, str]:
    matches = list(PARAM_RE.finditer(body))
    if not matches or matches[0].start() != 0:
        raise ConvertError(f"cannot parse script parameters: {body}")
    params: dict[str, str] = {}
    for index, match in enumerate(matches):
        key = match.group(1).lower()
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(body)
        if key in params:
            raise ConvertError(f"duplicate script parameter: {key}")
        if key not in SCRIPT_KEYS:
            raise ConvertError(f"unsupported script parameter: {key}")
        params[key] = unquote(body[start:end].strip())
    return params


def convert_script(line: str) -> Script:
    matched = SCRIPT_LINE_RE.match(line.strip())
    if not matched:
        raise ConvertError(f"cannot map script rule: {line}")
    name = matched.group(1).strip()
    if not SCRIPT_NAME_RE.fullmatch(name):
        raise ConvertError(f"unsupported script name: {name}")
    try:
        params = parse_script_params(matched.group(2).strip())
    except ConvertError as exc:
        raise ConvertError(f"cannot map script {name}: {exc}") from exc
    if "type" not in params or "pattern" not in params or "script-path" not in params:
        raise ConvertError(f"script {name} is missing type, pattern, or script-path")
    surge_type = params["type"].strip().lower()
    if surge_type not in SCRIPT_TYPES:
        raise ConvertError(f"script {name} has unmapped type {params['type']}")
    pattern = params["pattern"].strip()
    script_path = params["script-path"].strip()
    if not pattern:
        raise ConvertError(f"script {name} has an empty pattern")
    if not script_path.startswith(("https://", "http://")):
        raise ConvertError(f"script {name} script-path is not an http(s) URL")
    script = Script(
        name=name,
        match=pattern,
        type=SCRIPT_TYPES[surge_type],
        script_path=script_path,
    )
    if "requires-body" in params:
        script.require_body = parse_bool(params["requires-body"], "requires-body")
    if "binary-body-mode" in params:
        script.binary_mode = parse_bool(params["binary-body-mode"], "binary-body-mode")
    # An omitted timeout keeps Surge's 5 second default
    # (https://manual.nssurge.com/scripting/overview.html). Stash describes
    # timeout in seconds and marks it optional, without a different default
    # (https://stash.wiki/en/script/rewrite-requests), so it is not filled in.
    if "timeout" in params:
        raw = params["timeout"].strip()
        if not re.fullmatch(r"\d+", raw):
            raise ConvertError(f"script {name} has an invalid timeout: {raw}")
        script.timeout = int(raw)
    if "max-size" in params:
        raw = params["max-size"].strip()
        if not re.fullmatch(r"-?\d+", raw):
            raise ConvertError(f"script {name} has an invalid max-size: {raw}")
        size = int(raw)
        if size == -1:
            script.max_size = STASH_MAX_SIZE_UNLIMITED
        elif size >= 0:
            script.max_size = size
        else:
            raise ConvertError(f"script {name} has an invalid max-size: {raw}")
    if "argument" in params:
        argument = params["argument"]
        if argument[:1] in "{[":
            try:
                json.loads(argument)
            except json.JSONDecodeError as exc:
                raise ConvertError(f"script {name} argument is not valid JSON: {exc}") from exc
        script.argument = argument
    return script


def parse_hostnames(value: str) -> list[str]:
    """Split a Surge ``hostname =`` value.

    ``%APPEND%`` is a prefix token and may sit in the same comma field as the
    first host (``%APPEND% *.example.com, www.example.com``).
    """
    hosts: list[str] = []
    for piece in value.split(","):
        for token in piece.split():
            item = unquote(token.strip())
            if not item:
                continue
            if item.startswith("%") and item.endswith("%") and len(item) >= 2:
                if item.lower() == "%append%":
                    continue
                raise ConvertError(f"unsupported MITM modifier: {item}")
            hosts.append(item)
    if not hosts:
        raise ConvertError("empty MITM hostname")
    return hosts


def parse_metadata_line(line: str, lineno: int, meta: dict[str, str]) -> None:
    body = line[2:].strip()
    if "=" not in body:
        fail(lineno, f"metadata is missing '=': {line}")
    key, value = body.split("=", 1)
    key = key.strip().lower()
    if key not in KNOWN_META:
        fail(lineno, f"unsupported metadata key: {key}")
    if key in meta:
        fail(lineno, f"duplicate metadata key: {key}")
    meta[key] = value.strip()


def attach(pending: list[str], target: list[Entry]) -> None:
    for comment in pending:
        target.append(Comment(comment))
    pending.clear()


def parse_module(text: str) -> tuple[dict[str, str], Model]:
    """Parse a module whose placeholders have already been substituted.

    The returned Model's name/author fields are filled by the caller.
    """
    meta: dict[str, str] = {}
    section: str | None = None
    pending: list[str] = []
    mitm: list[str] = []
    url_rewrite: list[Entry] = []
    body_rewrite: list[Entry] = []
    mocks: list[Entry] = []
    scripts: list[Script] = []
    saw_mitm = False

    for lineno, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#!"):
            if section is not None:
                fail(lineno, "metadata line after a section started")
            if pending:
                fail(lineno, "comment was not attached to a rule")
            parse_metadata_line(line, lineno, meta)
            continue
        if line.startswith("[") and line.endswith("]"):
            if pending:
                fail(lineno, "comment was not attached to a rule")
            name = line[1:-1].strip().casefold()
            if name not in KNOWN_SECTIONS:
                fail(lineno, f"unsupported section: {line}")
            section = name
            if section == "mitm":
                saw_mitm = True
            continue
        if section is None:
            if is_comment_line(line):
                continue
            fail(lineno, f"unsupported line: {line}")
        if is_comment_line(line):
            if section in COMMENT_SECTIONS:
                try:
                    pending.append(normalize_comment(line))
                except ConvertError as exc:
                    fail(lineno, str(exc))
            continue
        try:
            if section == "url rewrite":
                attach(pending, url_rewrite)
                url_rewrite.append(Rule(convert_url_rewrite(line)))
            elif section == "map local":
                converted = convert_map_local(line)
                if isinstance(converted, Rule):
                    attach(pending, url_rewrite)
                    url_rewrite.append(converted)
                else:
                    attach(pending, mocks)
                    mocks.append(converted)
            elif section == "body rewrite":
                attach(pending, body_rewrite)
                body_rewrite.append(Rule(convert_body_rewrite(line)))
            elif section == "script":
                scripts.append(convert_script(line))
            elif section == "mitm":
                if "=" not in line:
                    fail(lineno, f"unsupported MITM line: {line}")
                key, value = line.split("=", 1)
                if key.strip().lower() != "hostname":
                    fail(lineno, f"unsupported MITM directive: {key.strip()}")
                mitm.extend(parse_hostnames(value))
            else:
                fail(lineno, f"internal: unhandled section {section}")
        except ConvertError as exc:
            message = str(exc)
            if message.startswith("line "):
                raise
            fail(lineno, message)
    if pending:
        raise ConvertError("comment was not attached to a rule")
    if saw_mitm and not mitm:
        raise ConvertError("[MITM] produced no hostnames")
    names = [script.name for script in scripts]
    if len(names) != len(set(names)):
        raise ConvertError("duplicate script name")
    if "name" not in meta or not meta["name"]:
        raise ConvertError("module is missing #!name")
    if "desc" not in meta or not meta["desc"]:
        raise ConvertError("module is missing #!desc")
    # Placeholder so type-checkers see a complete object. convert_sgmodule fills identity.
    model = Model(
        name=meta["name"],
        desc=meta["desc"],
        author=meta.get("author", ""),
        homepage=meta.get("homepage", ""),
        icon=meta.get("icon") or None,
        category=meta.get("category") or None,
        mitm=mitm,
        url_rewrite=url_rewrite,
        body_rewrite=body_rewrite,
        mocks=mocks,
        scripts=scripts,
        source_url="",
        license_spdx="",
        license_file="",
        extra_rules=[],
        extra_rules_comment=None,
    )
    return meta, model


def non_string_override_fields(loaded: object) -> str | None:
    """Return an error when a Stash string field was parsed as another type.

    Stash rejects an integer or boolean where it expects a string. ``12306``
    and ``on`` are the usual cases.
    """
    if not isinstance(loaded, dict):
        return "override root is not a mapping"
    for key in ("name", "desc", "author", "homepage", "icon", "category"):
        if key in loaded and not isinstance(loaded[key], str):
            return f"{key} is {type(loaded[key]).__name__}, expected a string"
    http = loaded.get("http") or {}
    if http and not isinstance(http, dict):
        return "http is not a mapping"
    if isinstance(http, dict):
        for host in http.get("mitm") or []:
            if not isinstance(host, str):
                return f"mitm host is {type(host).__name__}, expected a string: {host!r}"
        for section in ("url-rewrite", "body-rewrite"):
            for item in http.get(section) or []:
                if not isinstance(item, str):
                    return f"{section} entry is {type(item).__name__}, expected a string: {item!r}"
        for script in http.get("script") or []:
            if not isinstance(script, dict):
                return "script entry is not a mapping"
            for key in ("name", "match", "type", "argument"):
                if key in script and not isinstance(script[key], str):
                    return f"script {key} is {type(script[key]).__name__}, expected a string: {script.get(key)!r}"
    providers = loaded.get("script-providers") or {}
    if providers and not isinstance(providers, dict):
        return "script-providers is not a mapping"
    if isinstance(providers, dict):
        for key in providers:
            if not isinstance(key, str):
                return f"script-providers key is {type(key).__name__}, expected a string: {key!r}"
    for rule in loaded.get("rules") or []:
        if not isinstance(rule, str):
            return f"rule is {type(rule).__name__}, expected a string: {rule!r}"
    return None


def yaml_quote(text: str) -> str:
    if "\n" not in text and "\r" not in text and "'" not in text:
        return "'" + text + "'"
    escaped = (
        text.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\r", "\\r")
        .replace("\n", "\\n")
        .replace("\t", "\\t")
    )
    return '"' + escaped + '"'


def yaml_mapping_scalar(text: str) -> str:
    if "\n" in text or "\r" in text:
        return yaml_quote(text)
    probe = "k: " + text + "\n"
    try:
        loaded = yaml.safe_load(probe)
    except yaml.YAMLError:
        loaded = None
    if isinstance(loaded, dict) and list(loaded) == ["k"] and loaded.get("k") == text:
        return text
    return yaml_quote(text)


def yaml_list_scalar(text: str) -> str:
    if "\n" in text or "\r" in text:
        return yaml_quote(text)
    probe = "- " + text + "\n"
    try:
        loaded = yaml.safe_load(probe)
    except yaml.YAMLError:
        loaded = None
    if loaded == [text]:
        return text
    return yaml_quote(text)


def yaml_key(text: str) -> str:
    probe = text + ": 1\n"
    try:
        loaded = yaml.safe_load(probe)
    except yaml.YAMLError:
        loaded = None
    if loaded == {text: 1}:
        return text
    return yaml_quote(text)


def render_entries(entries: list[Entry]) -> list[str]:
    lines: list[str] = []
    for entry in entries:
        if isinstance(entry, Comment):
            lines.append("    " + entry.text)
            continue
        lines.append("    - " + yaml_list_scalar(entry.text))
    return lines


def render_script(script: Script) -> list[str]:
    rows: list[tuple[str, str]] = [
        ("match", yaml_mapping_scalar(script.match)),
        # Always quote. A bare name such as 12306 or on is an integer or bool.
        ("name", yaml_quote(script.name)),
        ("type", script.type),
    ]
    if script.require_body is not None:
        rows.append(("require-body", "true" if script.require_body else "false"))
    if script.binary_mode is not None:
        rows.append(("binary-mode", "true" if script.binary_mode else "false"))
    if script.max_size is not None:
        rows.append(("max-size", str(script.max_size)))
    if script.timeout is not None:
        rows.append(("timeout", str(script.timeout)))
    if script.argument is not None:
        rows.append(("argument", yaml_mapping_scalar(script.argument)))
    lines: list[str] = []
    for index, (key, value) in enumerate(rows):
        indent = "    - " if index == 0 else "      "
        lines.append(f"{indent}{key}: {value}")
    return lines


def render_mocks(entries: list[Entry]) -> list[str]:
    lines = ["  mock:"]
    for entry in entries:
        if isinstance(entry, Comment):
            lines.append("    " + entry.text)
            continue
        mock = entry
        if not isinstance(mock, Mock):
            raise ConvertError("internal: mock list contained a rewrite rule")
        lines.append("    - match: " + yaml_mapping_scalar(mock.match))
        if mock.status_code is not None:
            lines.append(f"      status-code: {mock.status_code}")
        if mock.text is not None:
            lines.append("      text: " + yaml_mapping_scalar(mock.text))
        if mock.base64 is not None:
            lines.append("      base64: " + yaml_mapping_scalar(mock.base64))
        if mock.headers:
            lines.append("      headers:")
            for key, value in mock.headers:
                lines.append(f"        {yaml_key(key)}: {yaml_mapping_scalar(value)}")
    return lines


def header_comments(model: Model) -> list[str]:
    license_url = LICENSE_URLS.get(model.license_spdx, "")
    license_text = model.license_spdx
    if license_url:
        license_text += f"（{license_url}）"
    return [
        "# 由上游配置自动转换为 Stash 覆写，本文件是改写后的结果，请勿手工修改。",
        f"# 上游：{model.source_url}",
        f"# 原作者：{model.author}（{model.homepage}）。著作权归原作者所有。",
        f"# 许可证：{license_text}，全文见 {model.license_file}。",
    ]


def rule_texts(entries: list[Entry]) -> list[str]:
    return [entry.text for entry in entries if isinstance(entry, Rule)]


def render_document(model: Model) -> str:
    lines = header_comments(model)
    lines.append("name: " + yaml_mapping_scalar(model.name))
    lines.append("desc: " + yaml_mapping_scalar(model.desc))
    lines.append("author: " + yaml_mapping_scalar(model.author))
    lines.append("homepage: " + yaml_mapping_scalar(model.homepage))
    if model.icon:
        lines.append("icon: " + yaml_mapping_scalar(model.icon))
    if model.category:
        lines.append("category: " + yaml_mapping_scalar(model.category))

    blocks: list[list[str]] = []
    if model.mitm:
        block = ["  mitm:"]
        for host in model.mitm:
            block.append("    - " + yaml_list_scalar(host))
        blocks.append(block)
    if model.url_rewrite:
        blocks.append(["  url-rewrite:", *render_entries(model.url_rewrite)])
    if model.body_rewrite:
        blocks.append(["  body-rewrite:", *render_entries(model.body_rewrite)])
    if model.mocks:
        blocks.append(render_mocks(model.mocks))
    if model.scripts:
        block = ["  script:"]
        for script in model.scripts:
            block.extend(render_script(script))
        blocks.append(block)
    if blocks:
        lines.append("")
        lines.append("http:")
        for index, block in enumerate(blocks):
            if index:
                lines.append("")
            lines.extend(block)
    if model.scripts:
        lines.append("")
        lines.append("script-providers:")
        for script in model.scripts:
            lines.append(f"  {yaml_quote(script.name)}:")
            lines.append("    url: " + yaml_mapping_scalar(script.script_path))
            lines.append(f"    interval: {SCRIPT_INTERVAL}")
    if model.extra_rules:
        lines.append("")
        if model.extra_rules_comment:
            lines.append("# " + model.extra_rules_comment)
        lines.append("rules:")
        for rule in model.extra_rules:
            lines.append("  - " + yaml_list_scalar(rule))
    text = "\n".join(lines) + "\n"
    validate_document(text, model)
    return text


def validate_document(text: str, model: Model) -> None:
    if "arguments:" in text.splitlines():
        raise ConvertError("generated override uses the Stash arguments field")
    if "{{{" in text or "}}}" in text:
        raise ConvertError("generated override still contains a placeholder")
    try:
        loaded = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ConvertError(f"generated override is not valid YAML: {exc}") from exc
    if not isinstance(loaded, dict):
        raise ConvertError("generated override is not a YAML mapping")
    coerced = non_string_override_fields(loaded)
    if coerced:
        raise ConvertError(f"generated override is not safe for Stash: {coerced}")
    if "arguments" in loaded:
        raise ConvertError("generated override uses the Stash arguments field")
    expected_top = {"name", "desc", "author", "homepage", "http", "rules"}
    if model.icon:
        expected_top.add("icon")
    if model.category:
        expected_top.add("category")
    if model.scripts:
        expected_top.add("script-providers")
    if not model.extra_rules:
        expected_top.discard("rules")
    http_expected = set()
    if model.mitm:
        http_expected.add("mitm")
    if model.url_rewrite:
        http_expected.add("url-rewrite")
    if model.body_rewrite:
        http_expected.add("body-rewrite")
    if model.mocks:
        http_expected.add("mock")
    if model.scripts:
        http_expected.add("script")
    if not http_expected:
        expected_top.discard("http")
    if set(loaded) != expected_top:
        raise ConvertError(f"generated keys {sorted(loaded)} != {sorted(expected_top)}")
    if loaded["name"] != model.name or loaded["desc"] != model.desc:
        raise ConvertError("YAML parse changed name or desc")
    if loaded["author"] != model.author or loaded["homepage"] != model.homepage:
        raise ConvertError("YAML parse changed author or homepage")
    if model.source_url not in text or model.author not in text:
        raise ConvertError("generated override is missing its upstream credit")
    if model.license_spdx not in text or model.license_file not in text:
        raise ConvertError("generated override is missing its license notice")
    http = loaded.get("http", {})
    if set(http) != http_expected:
        raise ConvertError(f"generated http keys {sorted(http)} != {sorted(http_expected)}")
    if model.mitm and http["mitm"] != model.mitm:
        raise ConvertError("YAML parse changed MITM hostnames")
    if rule_texts(model.url_rewrite) != http.get("url-rewrite", []):
        raise ConvertError("YAML parse changed url-rewrite rules")
    if rule_texts(model.body_rewrite) != http.get("body-rewrite", []):
        raise ConvertError("YAML parse changed body-rewrite rules")
    for entry in [*model.url_rewrite, *model.body_rewrite, *model.mocks]:
        if isinstance(entry, Comment) and entry.text not in text:
            raise ConvertError(f"missing comment {entry.text}")
    mocks = [entry for entry in model.mocks if isinstance(entry, Mock)]
    loaded_mocks = http.get("mock", [])
    if len(loaded_mocks) != len(mocks):
        raise ConvertError("YAML parse changed mock rules")
    for expected, actual in zip(mocks, loaded_mocks):
        if actual.get("match") != expected.match:
            raise ConvertError("YAML parse changed a mock match")
        if expected.status_code is None:
            if "status-code" in actual:
                raise ConvertError("unexpected mock status-code")
        elif actual.get("status-code") != expected.status_code:
            raise ConvertError("YAML parse changed a mock status-code")
        if expected.text is not None and actual.get("text") != expected.text:
            raise ConvertError("YAML parse changed mock text")
        if expected.base64 is not None and actual.get("base64") != expected.base64:
            raise ConvertError("YAML parse changed mock base64")
        if expected.headers:
            got = actual.get("headers") or {}
            if got != dict(expected.headers):
                raise ConvertError("YAML parse changed mock headers")
    loaded_scripts = http.get("script", [])
    if len(loaded_scripts) != len(model.scripts):
        raise ConvertError("YAML parse changed scripts")
    for expected, actual in zip(model.scripts, loaded_scripts):
        if actual.get("match") != expected.match or actual.get("name") != expected.name:
            raise ConvertError(f"YAML parse changed script {expected.name}")
        if actual.get("type") != expected.type:
            raise ConvertError(f"YAML parse changed script type for {expected.name}")
        for key, value in (
            ("require-body", expected.require_body),
            ("binary-mode", expected.binary_mode),
            ("timeout", expected.timeout),
            ("max-size", expected.max_size),
            ("argument", expected.argument),
        ):
            if value is None:
                if key in actual:
                    raise ConvertError(f"unexpected {key} on {expected.name}")
            elif actual.get(key) != value:
                raise ConvertError(f"YAML parse changed {key} on {expected.name}")
        if expected.argument and expected.argument[:1] in "{[":
            json.loads(actual["argument"])
    if model.scripts:
        providers = loaded["script-providers"]
        if set(providers) != {script.name for script in model.scripts}:
            raise ConvertError("script-providers do not match script names")
        for script in model.scripts:
            provider = providers[script.name]
            if provider.get("url") != script.script_path or provider.get("interval") != SCRIPT_INTERVAL:
                raise ConvertError(f"script provider mismatch for {script.name}")
    if model.extra_rules:
        if loaded.get("rules") != model.extra_rules:
            raise ConvertError("YAML parse changed the extra rules")
        if model.extra_rules_comment and model.extra_rules_comment not in text:
            raise ConvertError("missing extra-rules comment")


def convert_sgmodule(
    text: str,
    *,
    source_url: str,
    author: str,
    homepage: str,
    license_spdx: str,
    license_file: str,
    extra_rules: list[str] | None = None,
    extra_rules_comment: str | None = None,
) -> str:
    original = normalize(text)
    if not source_url.startswith("https://"):
        raise ConvertError(f"source URL must start with https://: {source_url}")
    if not author or not homepage:
        raise ConvertError("author and homepage are required")
    if not license_spdx or not license_file:
        raise ConvertError("license_spdx and license_file are required")
    defaults: dict[str, str] = {}
    for lineno, raw in enumerate(original.splitlines(), 1):
        line = raw.strip()
        if line.lower().startswith("#!arguments=") and not line.lower().startswith(
            "#!arguments-desc="
        ):
            defaults = parse_arguments(line.split("=", 1)[1], lineno)
            break
    substituted = substitute(original, defaults)
    _meta, model = parse_module(substituted)
    if not model.author:
        model.author = author
    if not model.homepage:
        model.homepage = homepage
    model.source_url = source_url
    model.license_spdx = license_spdx
    model.license_file = license_file
    model.extra_rules = list(extra_rules or [])
    model.extra_rules_comment = extra_rules_comment
    return render_document(model)


def self_check() -> None:
    """Exercise mappings the live YouTube module does not currently use."""
    sample = """
#!name=Example
#!desc=Example desc
#!arguments=屏蔽上传按钮:true,屏蔽Shorts按钮:false
#!category=Demo

[URL Rewrite]
# ======= 跳转 ======= #
^https://example\\.com/old https://example.com/new 302
^https://example\\.com/ad reject-200

[Map Local]
^https://example\\.com/empty data-type=text data=" " status-code=200
^https://example\\.com/obj data-type=text data="{}" status-code=200
^https://example\\.com/body data-type=text data='{"a":1}' status-code=201 header="Content-Type: application/json"

[Body Rewrite]
http-response-jq ^https://example\\.com/api '.a = 1'
http-response ^https://example\\.com/page foo bar baz qux

[Script]
demo = type=http-response,pattern=^https://example\\.com/,requires-body=1,binary-body-mode=0,timeout=30,max-size=1024,script-path=https://example.com/demo.js,argument="{"blockUpload":{{{屏蔽上传按钮}}},"blockShorts":{{{屏蔽Shorts按钮}}}}"
unlimited = type=http-response,pattern=^https://example\\.com/player,requires-body=1,binary-body-mode=1,max-size=-1,script-path=https://example.com/player.js
on = type=http-request,pattern=^https://example\\.com/on,requires-body=0,script-path=https://example.com/on.js

[MITM]
hostname = %APPEND% *.example.com, -exclude.example.com
"""
    rendered = convert_sgmodule(
        sample,
        source_url="https://example.com/example.sgmodule",
        author="Table Author",
        homepage="https://example.com/home",
        license_spdx="Apache-2.0",
        license_file="Stash/LICENSES/example.txt",
        extra_rules=["AND,((DOMAIN-SUFFIX,example.com),(PROTOCOL,QUIC)),REJECT"],
        extra_rules_comment="quic",
    )
    loaded = yaml.safe_load(rendered)
    assert loaded["author"] == "Table Author"
    assert loaded["category"] == "Demo"
    assert loaded["http"]["mitm"] == ["*.example.com", "-exclude.example.com"]
    assert "%APPEND%" not in rendered
    assert "# 跳转" in rendered and "=======" not in rendered
    rewrites = loaded["http"]["url-rewrite"]
    assert rewrites == [
        "^https://example\\.com/old https://example.com/new 302",
        "^https://example\\.com/ad - reject-200",
        "^https://example\\.com/empty - reject-200",
        "^https://example\\.com/obj - reject-dict",
    ]
    mock = loaded["http"]["mock"][0]
    assert mock["status-code"] == 201
    assert mock["text"] == '{"a":1}'
    assert mock["headers"]["Content-Type"] == "application/json"
    bodies = loaded["http"]["body-rewrite"]
    assert bodies[0] == "^https://example\\.com/api response-jq .a = 1"
    assert bodies[1] == "^https://example\\.com/page response-replace-regex foo bar baz qux"
    argument = json.loads(loaded["http"]["script"][0]["argument"])
    assert argument == {"blockUpload": True, "blockShorts": False}
    by_name = {item["name"]: item for item in loaded["http"]["script"]}
    assert by_name["demo"]["binary-mode"] is False
    assert by_name["demo"]["timeout"] == 30
    assert by_name["demo"]["max-size"] == 1024
    assert by_name["unlimited"]["binary-mode"] is True
    assert by_name["unlimited"]["max-size"] == STASH_MAX_SIZE_UNLIMITED
    assert "timeout" not in by_name["unlimited"]
    assert by_name["on"]["name"] == "on"
    assert "name: 'demo'" in rendered
    assert "name: 'on'" in rendered
    assert "\n  'demo':\n" in rendered
    assert "\n  'on':\n" in rendered
    assert "arguments" not in loaded

    broken = [
        sample.replace("[URL Rewrite]", "[Header Rewrite]"),
        sample.replace("{{{屏蔽Shorts按钮}}}", "{{{不存在}}}"),
        sample.replace("data-type=text data='{\"a\":1}'", "data-type=file data=a.js"),
        "#!name=Only\n#!desc=Only\n[Rule]\nDOMAIN,example.com,REJECT\n",
    ]
    for text in broken:
        try:
            convert_sgmodule(
                text,
                source_url="https://example.com/bad.sgmodule",
                author="A",
                homepage="https://example.com",
                license_spdx="Apache-2.0",
                license_file="Stash/LICENSES/example.txt",
            )
        except ConvertError:
            continue
        raise ConvertError("self-check expected an unmappable module to fail")


if __name__ == "__main__":
    self_check()
    print("self-check ok")

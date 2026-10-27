#!/usr/bin/env python3
"""Convert Quantumult X rewrite (.conf) to Stash override (.stoverride).

Fails loudly on lines that cannot be mapped. Skips comments and QX-disabled
lines that start with ';' (reports them). Designed for ddgksf2013-style
rewrite lists; also usable as a daily GitHub Actions converter.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
import urllib.request
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None

from surge_module_to_stash import ConvertError

VALID_URL_REWRITE_ACTIONS = {
    "reject",
    "reject-200",
    "reject-img",
    "reject-dict",
    "reject-array",
    "302",
    "307",
    "transparent",
}

USER_AGENT = "Quantumult%20X/1.5.0"


@dataclass
class Meta:
    name: str = ""
    desc: str = ""
    author: str = "ddgksf2013"
    homepage: str = "https://github.com/ddgksf2013/Rewrite"
    icon: str = ""
    category: str = "去广告"
    source: str = ""


@dataclass
class ConvertResult:
    meta: Meta
    mitm: list[str] = field(default_factory=list)
    url_rewrite: list[str] = field(default_factory=list)
    body_rewrite: list[str] = field(default_factory=list)
    scripts: list[dict] = field(default_factory=list)
    script_providers: OrderedDict = field(default_factory=OrderedDict)
    rules: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)  # comments about dropped lines
    notes: list[str] = field(default_factory=list)


class UnmappableLine(ConvertError):
    def __init__(self, lineno: int, line: str, reason: str):
        super().__init__(f"line {lineno}: {reason}\n  {line}")
        self.lineno = lineno
        self.line = line
        self.reason = reason


def fetch_text(source: str) -> str:
    path = Path(source)
    if path.exists():
        return path.read_text(encoding="utf-8")
    req = urllib.request.Request(
        source,
        headers={"User-Agent": USER_AGENT, "Accept": "text/plain,*/*"},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = resp.read()
    # Guard against HTML SPA landing pages
    head = data[:200].lstrip().lower()
    if head.startswith(b"<!doctype") or head.startswith(b"<html"):
        raise RuntimeError(
            f"Source returned HTML instead of a rewrite list: {source}"
        )
    return data.decode("utf-8", errors="replace")


def parse_userscript_meta(text: str) -> dict:
    out = {}
    m = re.search(r"//\s*==UserScript==([\s\S]*?)//\s*==/UserScript==", text)
    if not m:
        return out
    for line in m.group(1).splitlines():
        mm = re.match(r"//\s*@(\w+)\s+(.*)$", line.strip())
        if mm:
            out[mm.group(1)] = mm.group(2).strip()
    return out


def script_name_from_url(url: str, used: set[str]) -> str:
    path = urlparse(url).path
    base = Path(path).stem or "script"
    # keep it yaml/key-friendly
    base = re.sub(r"[^A-Za-z0-9_.-]+", "_", base).strip("._") or "script"
    name = base
    if name in used:
        # same URL should reuse; uniqueness handled by caller via URL key
        digest = hashlib.sha1(url.encode()).hexdigest()[:6]
        name = f"{base}_{digest}"
    used.add(name)
    return name


def split_hostname_list(raw: str) -> list[str]:
    hosts = []
    for part in raw.split(","):
        h = part.strip()
        if h:
            hosts.append(h)
    return hosts


def host_rule_to_stash(kind: str, host: str, policy: str) -> str:
    policy = policy.upper()
    if policy not in {"REJECT", "DIRECT", "PROXY"}:
        # QX uses lowercase reject/direct
        policy = {"reject": "REJECT", "direct": "DIRECT"}.get(policy.lower(), policy.upper())
    host = host.strip()
    kind = kind.lower().replace("_", "-")
    if kind in {"host", "domain"}:
        return f"DOMAIN,{host},{policy}"
    if kind in {"host-suffix", "domain-suffix"}:
        return f"DOMAIN-SUFFIX,{host},{policy}"
    if kind in {"host-keyword", "domain-keyword"}:
        return f"DOMAIN-KEYWORD,{host},{policy}"
    if kind in {"ip-cidr", "ipcidr"}:
        return f"IP-CIDR,{host},{policy}"
    raise ValueError(f"unsupported host rule kind: {kind}")


def parse_response_body_rewrite(rest: str) -> tuple[str, str]:
    """Parse `A response-body B` after the first `url response-body` / `url request-body`."""
    # Format: <search> response-body <replace>   OR   <search> request-body <replace>
    m = re.match(r"^(.*?)\s+(?:response-body|request-body)\s+(.*)$", rest)
    if not m:
        raise ValueError("cannot parse body rewrite pair")
    return m.group(1), m.group(2)


def yaml_quote_scalar(s: str) -> str:
    """Quote a scalar for a YAML flow/list item when needed."""
    if s == "":
        return "''"
    # Prefer single quotes; escape existing singles by doubling
    if any(ch in s for ch in ":#{}[]&*!|>%@`'\"\\") or s.strip() != s or s.startswith("-") or s.lower() in {"true", "false", "null", "yes", "no"}:
        return "'" + s.replace("'", "''") + "'"
    # also quote if starts with suspicious chars or contains spaces that confuse
    if re.search(r"[\s]", s) or s.startswith(("*", "&", "!", "%", "@", "`")):
        return "'" + s.replace("'", "''") + "'"
    return s


def convert(text: str, meta: Meta, *, strict: bool = True) -> ConvertResult:
    result = ConvertResult(meta=meta)
    url_to_script_name: dict[str, str] = {}
    used_names: set[str] = set()

    lines = text.splitlines()
    for lineno, raw in enumerate(lines, 1):
        line = raw.strip()
        if not line:
            continue
        # userscript / block comments
        if line.startswith("//"):
            continue
        # section / annotation comments
        if line.startswith("#"):
            continue
        # QX disabled rewrite (semicolon)
        if line.startswith(";"):
            result.skipped.append(f"L{lineno} disabled: {line[:140]}")
            continue

        # hostname = a, b
        hm = re.match(r"^hostname\s*=\s*(.+)$", line, re.I)
        if hm:
            result.mitm.extend(split_hostname_list(hm.group(1)))
            continue

        # host / host-suffix filter rules embedded in rewrite files
        hm2 = re.match(
            r"^(host(?:-suffix|-keyword)?|ip-cidr)\s*,\s*([^,]+)\s*,\s*(\w+)\s*$",
            line,
            re.I,
        )
        if hm2:
            try:
                result.rules.append(
                    host_rule_to_stash(hm2.group(1), hm2.group(2), hm2.group(3))
                )
            except ValueError as e:
                raise UnmappableLine(lineno, line, str(e)) from e
            continue

        # Must be a url rewrite line: <pattern> url <action> ...
        if " url " not in line:
            msg = "not a hostname/host-rule/url rewrite line"
            if strict:
                raise UnmappableLine(lineno, line, msg)
            result.skipped.append(f"L{lineno} skip: {msg}: {line[:140]}")
            continue

        # Split only on the first " url " that is the QX keyword.
        # Patterns themselves rarely contain " url " as a literal token with spaces.
        left, right = line.split(" url ", 1)
        pattern = left.strip()
        right = right.strip()
        if not pattern:
            raise UnmappableLine(lineno, line, "empty URL pattern")

        parts = right.split()
        if not parts:
            raise UnmappableLine(lineno, line, "missing action after url")
        action = parts[0]

        # ---- simple rejects ----
        if action in {
            "reject",
            "reject-200",
            "reject-img",
            "reject-dict",
            "reject-array",
        }:
            if len(parts) != 1:
                raise UnmappableLine(lineno, line, f"unexpected tokens after {action}")
            # Stash form: PATTERN - ACTION
            result.url_rewrite.append(f"{pattern} - {action}")
            continue

        # ---- redirect ----
        if action in {"302", "307"}:
            if len(parts) != 2:
                raise UnmappableLine(lineno, line, f"{action} needs a target URL")
            target = parts[1]
            result.url_rewrite.append(f"{pattern} {target} {action}")
            continue

        # ---- body text replace (QX response-body / request-body) ----
        if action in {"response-body", "request-body"}:
            # remainder after action keyword: "A response-body B"
            rest = right[len(action) :].strip()
            try:
                search, repl = parse_response_body_rewrite(rest)
            except ValueError as e:
                raise UnmappableLine(lineno, line, str(e)) from e
            direction = (
                "response-replace-regex"
                if action == "response-body"
                else "request-replace-regex"
            )
            # Keep search/replace intact; quote whole list item.
            item = f"{pattern} {direction} {search} {repl}"
            result.body_rewrite.append(item)
            continue

        # ---- jq ----
        if action == "jsonjq-response-body":
            # QX: url jsonjq-response-body '.expr'
            rest = right[len(action) :].strip()
            expr = rest
            if (expr.startswith("'") and expr.endswith("'")) or (
                expr.startswith('"') and expr.endswith('"')
            ):
                expr = expr[1:-1]
            if not expr:
                raise UnmappableLine(lineno, line, "empty jq expression")
            result.body_rewrite.append(f"{pattern} response-jq {expr}")
            continue

        if action == "jsonjq-request-body":
            rest = right[len(action) :].strip()
            expr = rest
            if (expr.startswith("'") and expr.endswith("'")) or (
                expr.startswith('"') and expr.endswith('"')
            ):
                expr = expr[1:-1]
            result.body_rewrite.append(f"{pattern} request-jq {expr}")
            continue

        # ---- scripts ----
        script_map = {
            "script-response-body": ("response", True),
            "script-response-header": ("response", False),
            "script-request-body": ("request", True),
            "script-request-header": ("request", False),
            # QX echo-from-request: script short-circuits with $done({response:...})
            "script-analyze-echo-response": ("request", True),
        }
        if action in script_map:
            if len(parts) < 2:
                raise UnmappableLine(lineno, line, f"{action} needs a script URL")
            script_url = parts[1]
            if not script_url.startswith("http"):
                raise UnmappableLine(lineno, line, f"bad script URL: {script_url}")
            if len(parts) > 2:
                # ignore trailing junk? fail loud
                raise UnmappableLine(
                    lineno, line, f"unexpected tokens after script URL: {parts[2:]}"
                )
            stype, require_body = script_map[action]
            if script_url not in url_to_script_name:
                name = script_name_from_url(script_url, used_names)
                url_to_script_name[script_url] = name
                result.script_providers[name] = {
                    "url": script_url,
                    "interval": 86400,
                }
            else:
                name = url_to_script_name[script_url]
            entry = {
                "match": pattern,
                "name": name,
                "type": stype,
                "require-body": require_body,
                "timeout": 30,
            }
            if action == "script-analyze-echo-response":
                result.notes.append(
                    f"L{lineno} script-analyze-echo-response -> request script "
                    f"(expects $done({{response:...}}); 12306.js has Surge branch)"
                )
            result.scripts.append(entry)
            continue

        # ---- echo-response (static mock from URL) ----
        if action == "echo-response":
            # QX: url echo-response <content-type> echo-response <url>
            # Stash mock can't fetch remote body; fail loud unless we can reject-200.
            raise UnmappableLine(
                lineno,
                line,
                "echo-response cannot be auto-mapped to Stash mock without embedding body",
            )

        raise UnmappableLine(lineno, line, f"unmapped QX action: {action}")

    # de-dupe mitm preserving order
    seen = set()
    mitm = []
    for h in result.mitm:
        if h not in seen:
            seen.add(h)
            mitm.append(h)
    result.mitm = mitm
    return result


def render_stoverride(result: ConvertResult) -> str:
    m = result.meta
    lines: list[str] = []
    lines.append(f"name: {yaml_quote_scalar(m.name)}")
    if m.desc:
        # multi-line friendly
        if "\n" in m.desc:
            lines.append("desc: |")
            for dl in m.desc.splitlines():
                lines.append(f"  {dl}")
        else:
            lines.append(f"desc: {yaml_quote_scalar(m.desc)}")
    if m.author:
        lines.append(f"author: {yaml_quote_scalar(m.author)}")
    if m.homepage:
        lines.append(f"homepage: {yaml_quote_scalar(m.homepage)}")
    if m.icon:
        lines.append(f"icon: {yaml_quote_scalar(m.icon)}")
    if m.category:
        lines.append(f"category: {yaml_quote_scalar(m.category)}")
    if m.source:
        lines.append(f"# source: {m.source}")
    lines.append("")
    lines.append("http:")

    if result.mitm:
        lines.append("  mitm:")
        for h in result.mitm:
            lines.append(f"    - {yaml_quote_scalar(h)}")
        lines.append("")

    if result.url_rewrite:
        lines.append("  url-rewrite:")
        for item in result.url_rewrite:
            # Always quote: patterns are heavy with YAML-special chars
            lines.append(f"    - {yaml_quote_scalar(item)}")
        lines.append("")

    if result.body_rewrite:
        lines.append("  body-rewrite:")
        for item in result.body_rewrite:
            lines.append(f"    - {yaml_quote_scalar(item)}")
        lines.append("")

    if result.scripts:
        lines.append("  script:")
        for sc in result.scripts:
            lines.append(f"    - match: {yaml_quote_scalar(sc['match'])}")
            lines.append(f"      name: {yaml_quote_scalar(sc['name'])}")
            lines.append(f"      type: {sc['type']}")
            lines.append(f"      require-body: {'true' if sc['require-body'] else 'false'}")
            lines.append(f"      timeout: {sc['timeout']}")
        lines.append("")

    if result.script_providers:
        lines.append("script-providers:")
        for name, prov in result.script_providers.items():
            lines.append(f"  {yaml_quote_scalar(name)}:")
            lines.append(f"    url: {prov['url']}")
            lines.append(f"    interval: {prov['interval']}")
        lines.append("")

    if result.rules:
        lines.append("rules:")
        for r in result.rules:
            lines.append(f"  - {yaml_quote_scalar(r)}")
        lines.append("")

    if result.skipped:
        lines.append("# --- skipped QX lines ---")
        for s in result.skipped:
            lines.append(f"# {s}")
        lines.append("")

    if result.notes:
        lines.append("# --- conversion notes ---")
        for n in result.notes:
            lines.append(f"# {n}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def validate_output(text: str) -> None:
    if yaml is None:
        raise RuntimeError("PyYAML is required for validation")
    data = yaml.safe_load(text)
    if not isinstance(data, dict):
        raise ValueError("override root must be a mapping")
    http = data.get("http") or {}
    for item in http.get("url-rewrite") or []:
        if not isinstance(item, str):
            raise ValueError(f"url-rewrite item not a string: {item!r}")
        toks = item.split()
        if len(toks) < 2:
            raise ValueError(f"url-rewrite too short: {item!r}")
        action = toks[-1]
        if action not in VALID_URL_REWRITE_ACTIONS:
            raise ValueError(f"invalid Stash url-rewrite action {action!r} in: {item!r}")
    for item in http.get("body-rewrite") or []:
        if not isinstance(item, str):
            raise ValueError(f"body-rewrite item not a string: {item!r}")
        # expect: PATTERN ACTION ...
        # action is the first token that starts with request-/response-
        if not re.search(r"\s(request|response)-(replace-regex|jq|json-\w+)\s", f" {item} "):
            # looser check
            if not any(
                a in item
                for a in (
                    "response-replace-regex",
                    "request-replace-regex",
                    "response-jq",
                    "request-jq",
                    "response-json-",
                    "request-json-",
                )
            ):
                raise ValueError(f"body-rewrite missing known action: {item!r}")
    for sc in http.get("script") or []:
        if not isinstance(sc, dict):
            raise ValueError(f"script entry not a mapping: {sc!r}")
        for key in ("match", "name", "type"):
            if key not in sc:
                raise ValueError(f"script missing {key}: {sc!r}")
        if sc["type"] not in {"request", "response"}:
            raise ValueError(f"bad script type: {sc!r}")
    providers = data.get("script-providers") or {}
    for sc in http.get("script") or []:
        if sc["name"] not in providers:
            raise ValueError(f"script {sc['name']!r} has no script-providers entry")


def build_meta_from_source(text: str, source: str, overrides: dict) -> Meta:
    us = parse_userscript_meta(text)
    name = overrides.get("name") or us.get("ScriptName") or Path(urlparse(source).path).stem or "QX Rewrite"
    desc_bits = []
    if us.get("Desc"):
        desc_bits.append(us["Desc"])
    if us.get("Function"):
        desc_bits.append(us["Function"])
    desc_bits.append(f"从圈叉重写自动转换为 Stash 覆写。源: {source}")
    if us.get("Attention"):
        desc_bits.append(us["Attention"])
    desc = overrides.get("desc") or "\n".join(desc_bits)
    author = overrides.get("author") or us.get("Author") or "ddgksf2013"
    homepage = overrides.get("homepage") or us.get("ScriptURL") or "https://github.com/ddgksf2013/Rewrite"
    icon = overrides.get("icon") or ""
    category = overrides.get("category") or "去广告"
    return Meta(
        name=name,
        desc=desc,
        author=author,
        homepage=homepage,
        icon=icon,
        category=category,
        source=source,
    )


def convert_document(text: str, source: str, overrides: dict, *, strict: bool = True) -> tuple[ConvertResult, str]:
    """Convert rewrite text that was already fetched. Raises ConvertError."""
    if "-----BEGIN" in text and "ENCRYPTED" in text:
        raise ConvertError(f"Source looks encrypted, refusing to convert: {source}")
    head = text.lstrip()[:200].lower()
    if head.startswith("<!doctype") or head.startswith("<html"):
        raise ConvertError(f"Source returned HTML instead of a rewrite list: {source}")
    meta = build_meta_from_source(text, source, overrides)
    result = convert(text, meta, strict=strict)
    rendered = render_stoverride(result)
    validate_output(rendered)
    return result, rendered


def convert_qx_text(text: str, source_url: str, *, name: str | None = None) -> str:
    """Render a Stash override. ``name`` overrides the userscript title only."""
    overrides: dict[str, str] = {}
    if name:
        overrides["name"] = name
    _result, rendered = convert_document(text, source_url, overrides)
    return rendered


def convert_file(
    source: str,
    output: Path,
    *,
    strict: bool = True,
    **meta_overrides,
) -> ConvertResult:
    text = fetch_text(source)
    result, rendered = convert_document(text, source, meta_overrides, strict=strict)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(rendered, encoding="utf-8")
    return result


def self_check() -> None:
    """Offline conversion of a small rewrite, including a loud failure."""
    text = "\n".join(
        [
            "// ==UserScript==",
            "// @ScriptName        Demo",
            "// @Author            @ddgksf2013",
            "// @ScriptURL         https://example.com/demo.conf",
            "// @Desc              hello",
            "// @Attention         如需引用请注明出处",
            "// ==/UserScript==",
            "hostname = *.example.com, example.com",
            r"^https://example\.com/ad url reject-200",
            r"^https://example\.com/api url script-response-body https://example.com/demo.js",
            r";^https://example\.com/old url reject",
            "",
        ]
    )
    rendered = convert_qx_text(text, "https://example.com/demo.conf", name="演示")
    for snippet in (
        "name: 演示",
        "author: '@ddgksf2013'",
        "homepage: 'https://example.com/demo.conf'",
        "# source: https://example.com/demo.conf",
        "如需引用请注明出处",
        "从圈叉重写自动转换为 Stash 覆写。源: https://example.com/demo.conf",
        "'*.example.com'",
        "https://example.com/demo.js",
        "# --- skipped QX lines ---",
    ):
        if snippet not in rendered:
            raise ConvertError(f"qx self-check output missing {snippet!r}")
    validate_output(rendered)
    bad = text + "\n" + r"^https://example\.com/x url echo-response text/plain echo-response https://example.com/body" + "\n"
    try:
        convert_qx_text(bad, "https://example.com/demo.conf", name="演示")
    except UnmappableLine:
        return
    raise ConvertError("echo-response should fail the QX converter")


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--input", "-i", required=True, help="QX rewrite URL or local path")
    p.add_argument("--output", "-o", required=True, help="Output .stoverride path")
    p.add_argument("--name")
    p.add_argument("--desc")
    p.add_argument("--author")
    p.add_argument("--homepage")
    p.add_argument("--icon")
    p.add_argument("--category")
    p.add_argument(
        "--no-strict",
        action="store_true",
        help="Skip unmappable non-comment lines instead of failing",
    )
    args = p.parse_args(argv)

    try:
        result = convert_file(
            args.input,
            Path(args.output),
            strict=not args.no_strict,
            name=args.name,
            desc=args.desc,
            author=args.author,
            homepage=args.homepage,
            icon=args.icon,
            category=args.category,
        )
    except UnmappableLine as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    print(
        f"OK {args.output}: mitm={len(result.mitm)} "
        f"url-rewrite={len(result.url_rewrite)} "
        f"body-rewrite={len(result.body_rewrite)} "
        f"scripts={len(result.scripts)} "
        f"providers={len(result.script_providers)} "
        f"rules={len(result.rules)} "
        f"skipped={len(result.skipped)}"
    )
    for n in result.notes:
        print(f"NOTE: {n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Convert a Surge rule-set list into a mihomo classical rule-provider.

The converter is stdlib-only. Rule lines inside the provider never carry a
policy name. Types mihomo cannot load in a classical provider are dropped
and printed. Surge-only flags (extended-matching, pre-matching, and the
notification / update-interval parameters) are removed from rules that are
kept. ``no-resolve`` is kept on IP rules.
"""

from __future__ import annotations

import argparse
import ipaddress
import os
import re
import sys
from dataclasses import dataclass, field


# mihomo classical providers accept every routing rule except these.
# See rules/provider/classical_strategy.go (MATCH, RULE-SET, SUB-RULE).
CLASSICAL_REJECTED = {"MATCH", "RULE-SET", "SUB-RULE"}

# Types emitted unchanged when the payload is valid for mihomo.
DIRECT_TYPES = {
    "DOMAIN",
    "DOMAIN-SUFFIX",
    "DOMAIN-KEYWORD",
    "DOMAIN-WILDCARD",
    "DOMAIN-REGEX",
    "GEOSITE",
    "GEOIP",
    "SRC-GEOIP",
    "IP-ASN",
    "SRC-IP-ASN",
    "IP-CIDR",
    "IP-CIDR6",
    "SRC-IP-CIDR",
    "IP-SUFFIX",
    "SRC-IP-SUFFIX",
    "SRC-PORT",
    "DST-PORT",
    "IN-PORT",
    "DSCP",
    "PROCESS-NAME",
    "PROCESS-PATH",
    "PROCESS-NAME-REGEX",
    "PROCESS-PATH-REGEX",
    "PROCESS-NAME-WILDCARD",
    "PROCESS-PATH-WILDCARD",
    "NETWORK",
    "UID",
    "IN-TYPE",
    "IN-USER",
    "IN-NAME",
    "REMATCH-NAME",
}

# no-resolve is meaningful for destination-IP rules. mihomo also accepts it
# on the SRC-* IP aliases (those aliases already imply no-resolve).
NO_RESOLVE_TYPES = {
    "IP-CIDR",
    "IP-CIDR6",
    "GEOIP",
    "IP-ASN",
    "IP-SUFFIX",
    "SRC-IP-CIDR",
    "SRC-GEOIP",
    "SRC-IP-ASN",
    "SRC-IP-SUFFIX",
}

# ``src`` flips a destination-IP rule to the source address.
SRC_PARAM_TYPES = {
    "IP-CIDR",
    "IP-CIDR6",
    "GEOIP",
    "IP-ASN",
    "IP-SUFFIX",
}

IP_TYPES = {
    "IP-CIDR",
    "IP-CIDR6",
    "SRC-IP-CIDR",
    "IP-SUFFIX",
    "SRC-IP-SUFFIX",
}

ASN_TYPES = {"IP-ASN", "SRC-IP-ASN"}
GEOIP_TYPES = {"GEOIP", "SRC-GEOIP"}
PORT_TYPES = {"SRC-PORT", "DST-PORT", "IN-PORT"}
COMMA_PAYLOAD_TYPES = {
    "DOMAIN-REGEX",
    "PROCESS-NAME-REGEX",
    "PROCESS-PATH-REGEX",
    "AND",
    "OR",
    "NOT",
}

SURGE_FLAG_PARAMS = {
    "extended-matching",
    "pre-matching",
    "dns-failed",
    "requires-resolve",
}
SURGE_KV_PREFIXES = (
    "notification-text=",
    "notification-interval=",
    "update-interval=",
    "always-capture=",
)

DROP_REASONS = {
    "USER-AGENT": "USER-AGENT is Surge-only and is not a mihomo rule",
    "URL-REGEX": "URL-REGEX is Surge-only and is not a mihomo rule",
    "DOMAIN-SET": "DOMAIN-SET points at another list and cannot be inlined",
    "RULE-SET": "RULE-SET is not supported inside a mihomo classical provider",
    "SCRIPT": "SCRIPT is Surge-only",
    "FINAL": "FINAL is not allowed in a classical rule-provider",
    "MATCH": "MATCH is not allowed in a classical rule-provider",
    "SUB-RULE": "SUB-RULE is not supported inside a mihomo classical provider",
    "DEVICE-NAME": "DEVICE-NAME is Surge-only",
    "MAC-ADDRESS": "MAC-ADDRESS is Surge-only",
    "SUBNET": "SUBNET is Surge-only",
    "CELLULAR-RADIO": "CELLULAR-RADIO is Surge-only",
    "CELLULAR-CARRIER": "CELLULAR-CARRIER is Surge-only",
    "HOSTNAME-TYPE": "HOSTNAME-TYPE is Surge-only",
}

LOGIC_TYPES = {"AND", "OR", "NOT"}
MAX_LOGIC_DEPTH = 10


class Drop(Exception):
    """A rule (or a sub-rule) cannot be represented and must be omitted."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass
class Converted:
    line: str
    stripped: list[str] = field(default_factory=list)
    removed_policy: bool = False


@dataclass
class FileReport:
    label: str
    source_rules: int = 0
    output_rules: int = 0
    dropped: list[tuple[int, str, str]] = field(default_factory=list)
    stripped: list[tuple[int, str, list[str], str]] = field(default_factory=list)
    policies_removed: int = 0

    def print(self, out: object) -> None:
        print(
            f"summary: {self.label} "
            f"source_rules={self.source_rules} "
            f"output_rules={self.output_rules} "
            f"dropped={len(self.dropped)} "
            f"params_stripped_lines={len(self.stripped)} "
            f"policies_removed_lines={self.policies_removed}",
            file=out,
        )
        for lineno, original, reason in self.dropped:
            print(
                f"DROPPED {self.label}:{lineno}: {original} :: {reason}",
                file=out,
            )
        for lineno, original, params, output in self.stripped:
            joined = ", ".join(params)
            print(
                f"STRIPPED {self.label}:{lineno}: removed [{joined}] "
                f":: {original} => {output}",
                file=out,
            )


def is_banner_edge(line: str) -> bool:
    text = line.strip()
    if text.startswith("//"):
        body = text[2:].strip()
    elif text.startswith("#") or text.startswith(";"):
        body = text[1:].strip()
    else:
        return False
    return len(body) >= 3 and set(body) <= {"="}


def leading_banner_prefix_len(lines: list[str]) -> int:
    """Length of the decorative Surge header, so later line numbers stay aligned."""
    index = 0
    while index < len(lines) and not lines[index].strip():
        index += 1
    if index >= len(lines) or not is_banner_edge(lines[index]):
        return 0
    index += 1
    while index < len(lines):
        if is_banner_edge(lines[index]):
            return index + 1
        index += 1
    return len(lines)


def split_inline_comment(line: str) -> tuple[str, str]:
    """Split a trailing ``#``, ``//``, or ``;`` comment that is not quoted."""
    quote: str | None = None
    index = 0
    while index < len(line):
        char = line[index]
        if quote:
            if char == quote and line[index - 1] != "\\":
                quote = None
            index += 1
            continue
        if char in "\"'":
            quote = char
            index += 1
            continue
        previous_space = index == 0 or line[index - 1].isspace()
        if previous_space and char in "#;":
            return line[:index].rstrip(), line[index:].strip()
        if (
            previous_space
            and char == "/"
            and index + 1 < len(line)
            and line[index + 1] == "/"
        ):
            return line[:index].rstrip(), line[index:].strip()
        index += 1
    return line.strip(), ""


def comment_text(raw: str) -> str:
    text = raw.strip()
    if text.startswith("//"):
        body = text[2:].strip()
        return f"# {body}" if body else "#"
    if text.startswith(";"):
        body = text[1:].strip()
        return f"# {body}" if body else "#"
    if text.startswith("#"):
        return text
    return f"# {text}"


def yaml_inline_comment(raw: str) -> str:
    text = comment_text(raw)
    return text if text != "#" else ""


def split_commas(text: str) -> list[str]:
    """Split on commas that are outside quotes and parentheses."""
    fields: list[str] = []
    buf: list[str] = []
    quote: str | None = None
    depth = 0
    for char in text:
        if quote:
            buf.append(char)
            if char == quote:
                quote = None
            continue
        if char in "\"'":
            quote = char
            buf.append(char)
            continue
        if char == "(":
            depth += 1
            buf.append(char)
            continue
        if char == ")":
            depth = max(0, depth - 1)
            buf.append(char)
            continue
        if char == "," and depth == 0:
            fields.append("".join(buf).strip())
            buf = []
            continue
        buf.append(char)
    fields.append("".join(buf).strip())
    return fields


def unquote(value: str) -> str:
    text = value.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        return text[1:-1]
    return text


def match_paren(text: str, start: int) -> int:
    if start >= len(text) or text[start] != "(":
        raise Drop("logical rule payload is missing '('")
    depth = 0
    quote: str | None = None
    for index in range(start, len(text)):
        char = text[index]
        if quote:
            if char == quote and text[index - 1] != "\\":
                quote = None
            continue
        if char in "\"'":
            quote = char
            continue
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return index
    raise Drop("logical rule payload is missing ')'")


def split_logic_groups(payload: str) -> list[str]:
    """Return the inner text of each top-level ``(...)`` group."""
    text = payload.strip()
    if not (text.startswith("(") and text.endswith(")")):
        raise Drop("logical rule payload must be wrapped in parentheses")
    if match_paren(text, 0) != len(text) - 1:
        raise Drop("logical rule payload has extra trailing text")
    inner = text[1:-1].strip()
    groups: list[str] = []
    index = 0
    while index < len(inner):
        while index < len(inner) and inner[index] in " \t,":
            index += 1
        if index >= len(inner):
            break
        if inner[index] != "(":
            raise Drop("logical sub-rule is not wrapped in parentheses")
        end = match_paren(inner, index)
        groups.append(inner[index + 1 : end].strip())
        index = end + 1
    return groups


def is_surge_param(token: str) -> bool:
    low = token.lower()
    if low in SURGE_FLAG_PARAMS:
        return True
    return any(low.startswith(prefix) for prefix in SURGE_KV_PREFIXES)


def classify_extras(tokens: list[str]) -> tuple[list[str], list[str], list[str]]:
    """Return kept params, stripped Surge params, and removed policy names.

    Rule-set lines omit the policy. A token that is not a known flag is a
    policy name and is removed. It is not counted as a stripped parameter.
    """
    kept: list[str] = []
    stripped: list[str] = []
    policies: list[str] = []
    for token in tokens:
        if not token:
            continue
        low = token.lower()
        if low in {"no-resolve", "src"}:
            kept.append(low)
            continue
        if is_surge_param(token) or ("=" in token and not token.startswith("=")):
            stripped.append(token)
            continue
        policies.append(token)
    return kept, stripped, policies


def parse_rule_body(body: str) -> tuple[str, str, list[str]]:
    """Return ``(TYPE, payload, extra tokens)`` from one rule body."""
    if "," not in body:
        raise Drop("not a Surge rule (missing a comma)")
    rule_type, _, rest = body.partition(",")
    rule_type = rule_type.strip().upper()
    rest = rest.strip()
    if not rule_type:
        raise Drop("missing rule type")
    if rule_type in LOGIC_TYPES:
        if not rest.startswith("("):
            raise Drop("logical rule payload must start with '('")
        end = match_paren(rest, 0)
        payload = rest[: end + 1].strip()
        tail = rest[end + 1 :].strip()
        if tail.startswith(","):
            tail = tail[1:]
        extras = [part for part in split_commas(tail) if part] if tail.strip() else []
        return rule_type, payload, extras
    fields = split_commas(rest)
    if not fields or not fields[0]:
        raise Drop("missing rule payload")
    payload = unquote(fields[0])
    extras = [unquote(part) for part in fields[1:] if part]
    return rule_type, payload, extras


def normalize_cidr(payload: str) -> str:
    text = payload.strip()
    if not text:
        raise Drop("empty IP payload")
    if "/" not in text:
        try:
            address = ipaddress.ip_address(text)
        except ValueError as exc:
            raise Drop(f"invalid IP address: {payload}") from exc
        prefix = 32 if address.version == 4 else 128
        return f"{text}/{prefix}"
    try:
        ipaddress.ip_network(text, strict=False)
    except ValueError as exc:
        raise Drop(f"invalid CIDR: {payload}") from exc
    return text


def normalize_port(payload: str) -> str:
    text = payload.strip()
    if not text or any(char in text for char in "<>="):
        raise Drop(
            f"port expression is not valid in mihomo "
            f"(want a port, start-end, or a/b list): {payload}"
        )
    parts = text.split("/")
    if len(parts) > 28:
        raise Drop("port expression has more than 28 ranges")
    for part in parts:
        if not part:
            raise Drop(f"invalid port expression: {payload}")
        bounds = part.split("-")
        if len(bounds) > 2:
            raise Drop(f"invalid port expression: {payload}")
        numbers: list[int] = []
        for bound in bounds:
            if not bound.isdigit():
                raise Drop(f"invalid port expression: {payload}")
            value = int(bound)
            if value > 65535:
                raise Drop(f"port out of range: {payload}")
            numbers.append(value)
        if len(numbers) == 2 and numbers[0] > numbers[1]:
            raise Drop(f"port range is reversed: {payload}")
    return text


def normalize_asn(payload: str) -> str:
    text = payload.strip()
    if text.lower().startswith("as"):
        text = text[2:]
    if not text.isdigit():
        raise Drop(f"IP-ASN value is not a numeric ASN mihomo can match: {payload}")
    return str(int(text))


def filter_params(
    rule_type: str, kept: list[str], stripped: list[str]
) -> tuple[list[str], list[str]]:
    """Drop flags the output type does not use, preserving relative order."""
    accepted: list[str] = []
    extra_stripped = list(stripped)
    seen: set[str] = set()
    for param in kept:
        if param in seen:
            extra_stripped.append(param)
            continue
        seen.add(param)
        if param == "no-resolve" and rule_type in NO_RESOLVE_TYPES:
            accepted.append(param)
        elif param == "src" and rule_type in SRC_PARAM_TYPES:
            accepted.append(param)
        else:
            extra_stripped.append(param)
    return accepted, extra_stripped


def validate_direct(rule_type: str, payload: str) -> str:
    if rule_type in IP_TYPES:
        return normalize_cidr(payload)
    if rule_type in ASN_TYPES:
        return normalize_asn(payload)
    if rule_type in GEOIP_TYPES:
        if not re.fullmatch(r"[A-Za-z]{2}", payload.strip()):
            raise Drop(f"GEOIP value is not a two-letter country code: {payload}")
        return payload.strip().upper()
    if rule_type in PORT_TYPES:
        return normalize_port(payload)
    if rule_type == "NETWORK":
        value = payload.strip().lower()
        if value not in {"tcp", "udp"}:
            raise Drop(f"NETWORK only accepts tcp or udp, got: {payload}")
        return value
    if rule_type == "DSCP":
        if not payload.strip().isdigit() or not 0 <= int(payload.strip()) <= 63:
            raise Drop(f"DSCP must be an integer from 0 to 63: {payload}")
        return str(int(payload.strip()))
    if rule_type == "UID":
        if not payload.strip().isdigit():
            raise Drop(f"UID must be an integer: {payload}")
        return str(int(payload.strip()))
    if not payload.strip():
        raise Drop("empty payload")
    if "," in payload and rule_type not in COMMA_PAYLOAD_TYPES:
        raise Drop("payload contains a comma and cannot be expressed as one classical rule")
    return payload.strip()


def map_surge_type(rule_type: str, payload: str) -> tuple[str, str]:
    """Rename Surge types onto the mihomo classical names, or reject them."""
    if rule_type == "DEST-PORT":
        return "DST-PORT", payload
    if rule_type == "SRC-IP":
        return "SRC-IP-CIDR", payload
    if rule_type == "PROTOCOL":
        value = payload.strip().lower()
        if value in {"tcp", "udp"}:
            return "NETWORK", value
        raise Drop(
            "PROTOCOL is Surge-only; only TCP and UDP can be mapped to NETWORK"
        )
    if rule_type in DROP_REASONS or rule_type in CLASSICAL_REJECTED:
        raise Drop(DROP_REASONS.get(rule_type, f"{rule_type} is not supported in a classical provider"))
    if rule_type not in DIRECT_TYPES and rule_type not in LOGIC_TYPES:
        raise Drop(f"{rule_type} is not a mihomo classical rule type")
    return rule_type, payload


def join_rule(rule_type: str, payload: str, params: list[str]) -> str:
    parts = [rule_type, payload, *params]
    return ",".join(parts)


def convert_rule(body: str, depth: int = 0) -> Converted:
    rule_type, payload, extras = parse_rule_body(body)
    kept, stripped, policies = classify_extras(extras)
    removed_policy = bool(policies)
    if rule_type in LOGIC_TYPES:
        if depth >= MAX_LOGIC_DEPTH:
            raise Drop("logical rule is nested more than 10 levels")
        # mihomo treats everything after the type as the payload, so an
        # outer flag cannot be attached. Flags belong on the sub-rules.
        outer_stripped = list(stripped)
        for param in kept:
            outer_stripped.append(param)
        groups = split_logic_groups(payload)
        if rule_type == "NOT" and len(groups) != 1:
            raise Drop("NOT must contain exactly one sub-rule")
        if rule_type in {"AND", "OR"} and not groups:
            raise Drop(f"{rule_type} must contain at least one sub-rule")
        children: list[str] = []
        for group in groups:
            try:
                child = convert_rule(group, depth + 1)
            except Drop as exc:
                raise Drop(f"sub-rule ({group}) cannot be converted: {exc.reason}") from exc
            children.append(child.line)
            outer_stripped.extend(child.stripped)
            removed_policy = removed_policy or child.removed_policy
        rendered = f"{rule_type},(" + ",".join(f"({child})" for child in children) + ")"
        return Converted(rendered, outer_stripped, removed_policy)

    mapped_type, mapped_payload = map_surge_type(rule_type, payload)
    normalized = validate_direct(mapped_type, mapped_payload)
    params, stripped = filter_params(mapped_type, kept, stripped)
    return Converted(
        join_rule(mapped_type, normalized, params),
        stripped,
        removed_policy,
    )


def needs_yaml_quote(text: str) -> bool:
    if not text:
        return True
    if text[0] in "-?:,[]{}#&*!|>'\"%@`":
        return True
    if text != text.strip():
        return True
    if any(char in text for char in "#\"'`\t{}[]"):
        return True
    if ": " in text or text.endswith(":"):
        return True
    return False


def yaml_scalar(text: str) -> str:
    if not needs_yaml_quote(text):
        return text
    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def render_document(source_url: str, body_lines: list[str]) -> str:
    header = [
        f"# 来源：{source_url}",
        "# 由 Surge 规则自动转换生成，请勿手工修改。",
        "payload:",
    ]
    return "\n".join(header + body_lines) + "\n"


def convert_lines(
    lines: list[str], label: str, first_lineno: int = 1
) -> tuple[list[str], FileReport]:
    report = FileReport(label=label)
    body: list[str] = []
    want_blank = False
    for offset, raw in enumerate(lines):
        lineno = first_lineno + offset
        stripped = raw.strip()
        if not stripped:
            if body:
                want_blank = True
            continue
        if stripped.startswith("#") or stripped.startswith("//") or stripped.startswith(";"):
            if want_blank:
                body.append("")
                want_blank = False
            body.append("  " + comment_text(stripped))
            continue
        rule_body, inline = split_inline_comment(stripped)
        if not rule_body:
            continue
        report.source_rules += 1
        try:
            converted = convert_rule(rule_body)
        except Drop as exc:
            report.dropped.append((lineno, rule_body, exc.reason))
            continue
        if want_blank:
            body.append("")
            want_blank = False
        item = f"  - {yaml_scalar(converted.line)}"
        note = yaml_inline_comment(inline) if inline else ""
        if note:
            item = f"{item}  {note}"
        body.append(item)
        report.output_rules += 1
        if converted.stripped:
            report.stripped.append((lineno, rule_body, converted.stripped, converted.line))
        if converted.removed_policy:
            report.policies_removed += 1
    return body, report


def convert_text(text: str, source_url: str, label: str) -> tuple[str, FileReport]:
    raw_lines = text.splitlines()
    prefix = leading_banner_prefix_len(raw_lines)
    body, report = convert_lines(raw_lines[prefix:], label, prefix + 1)
    return render_document(source_url, body), report


def write_output(path: str, content: str) -> None:
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    temporary = path + ".tmp"
    with open(temporary, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(content)
    os.replace(temporary, path)


def convert_file(input_path: str, output_path: str, source_url: str) -> FileReport:
    with open(input_path, encoding="utf-8") as handle:
        text = handle.read()
    label = os.path.basename(input_path)
    content, report = convert_text(text, source_url, label)
    write_output(output_path, content)
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Convert a Surge .list rule set to a mihomo classical YAML provider."
    )
    parser.add_argument("input", help="Surge .list file")
    parser.add_argument("output", help="output YAML path, for example Ruleset/Global.yaml")
    parser.add_argument(
        "--source",
        required=True,
        help="public source URL written into the generated header",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = convert_file(args.input, args.output, args.source)
    except OSError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    report.print(sys.stdout)
    return 0


if __name__ == "__main__":
    sys.exit(main())

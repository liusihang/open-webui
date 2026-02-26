#!/usr/bin/env python3
"""
Batch governance CLI for Open WebUI models.

Operations:
- list (with filters)
- capabilities (set/remove capability flags)
- system-prompt (set/clear params.system)
- access (public/private visibility)
- icon (set uniform icon or rule-based icon)
- normalize-name (standardize model display names)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib import error, parse, request


DEFAULT_BASE_URL = "http://localhost:8080"
DEFAULT_TIMEOUT = 30
PUBLIC_GRANT_KEY = ("user", "*", "read")
KNOWN_PROVIDER_DISPLAY = {
    "openai": "OpenAI",
    "anthropic": "Anthropic",
    "google": "Google",
    "gemini": "Gemini",
    "qwen": "Qwen",
    "deepseek": "DeepSeek",
    "xai": "xAI",
    "grok": "Grok",
    "meta": "Meta",
    "llama": "Llama",
    "mistral": "Mistral",
    "moonshot": "Moonshot",
    "kimi": "Kimi",
    "volcengine": "Volcengine",
    "doubao": "Doubao",
    "azure": "Azure",
    "ollama": "Ollama",
    "baidu": "Baidu",
    "tencent": "Tencent",
    "alibaba": "Alibaba",
}


class ApiError(RuntimeError):
    def __init__(self, status: int, message: str, body: Optional[str] = None):
        super().__init__(message)
        self.status = status
        self.body = body


@dataclass
class Summary:
    selected: int = 0
    success: int = 0
    failed: int = 0
    skipped: int = 0
    planned: int = 0


class OpenWebUIClient:
    def __init__(self, base_url: str, token: str, timeout: int = DEFAULT_TIMEOUT):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout

    def _request(
        self,
        method: str,
        path: str,
        *,
        query: Optional[Dict[str, Any]] = None,
        payload: Optional[Dict[str, Any]] = None,
    ) -> Any:
        url = f"{self.base_url}{path}"
        if query:
            qp = {k: v for k, v in query.items() if v is not None}
            url = f"{url}?{parse.urlencode(qp)}"

        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self.token}",
        }

        data = None
        if payload is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(payload).encode("utf-8")

        req = request.Request(url=url, method=method.upper(), data=data, headers=headers)
        try:
            with request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8")
                if not raw:
                    return None
                content_type = resp.headers.get("Content-Type", "")
                if "application/json" in content_type:
                    return json.loads(raw)
                try:
                    return json.loads(raw)
                except json.JSONDecodeError:
                    return raw
        except error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            detail = raw
            try:
                data = json.loads(raw)
                detail = data.get("detail", raw)
            except json.JSONDecodeError:
                pass
            raise ApiError(exc.code, str(detail), body=raw) from exc
        except error.URLError as exc:
            raise ApiError(0, f"Network error: {exc.reason}") from exc

    def list_models(self) -> List[Dict[str, Any]]:
        items: List[Dict[str, Any]] = []
        page = 1
        total = None

        while True:
            data = self._request("GET", "/api/v1/models/list", query={"page": page})
            page_items = data.get("items", []) if isinstance(data, dict) else []
            total = data.get("total", total) if isinstance(data, dict) else total

            if not page_items:
                break

            items.extend(page_items)
            if isinstance(total, int) and len(items) >= total:
                break
            page += 1

        return items

    def get_model(self, model_id: str) -> Dict[str, Any]:
        return self._request("GET", "/api/v1/models/model", query={"id": model_id})

    def update_capabilities(self, model_id: str, capabilities_patch: Dict[str, Any]) -> Dict[str, Any]:
        return self._request(
            "POST",
            "/api/v1/models/model/capabilities/update",
            payload={"id": model_id, "capabilities": capabilities_patch},
        )

    def update_system_prompt(self, model_id: str, system_prompt: Optional[str]) -> Dict[str, Any]:
        return self._request(
            "POST",
            "/api/v1/models/model/prompt/system/update",
            payload={"id": model_id, "system": system_prompt},
        )

    def update_access_grants(self, model_id: str, access_grants: List[Dict[str, Any]]) -> Dict[str, Any]:
        return self._request(
            "POST",
            "/api/v1/models/model/access/update",
            payload={"id": model_id, "access_grants": access_grants},
        )

    def update_icon(self, model_id: str, profile_image_url: str) -> Dict[str, Any]:
        return self._request(
            "POST",
            "/api/v1/models/model/icon/update",
            payload={"id": model_id, "profile_image_url": profile_image_url},
        )

    def update_name(self, model: Dict[str, Any], new_name: str) -> Dict[str, Any]:
        payload = {
            "id": model["id"],
            "base_model_id": model.get("base_model_id"),
            "name": new_name,
            "meta": model.get("meta") or {},
            "params": model.get("params") or {},
            "access_grants": model.get("access_grants") or [],
            "is_active": bool(model.get("is_active", True)),
        }
        return self._request("POST", "/api/v1/models/model/update", payload=payload)


def load_json_file(path: str) -> Any:
    p = Path(path)
    if not p.exists():
        raise ValueError(f"File does not exist: {path}")
    return json.loads(p.read_text(encoding="utf-8"))


def load_ids_from_file(path: str) -> List[str]:
    p = Path(path)
    if not p.exists():
        raise ValueError(f"ID file does not exist: {path}")

    ids = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        ids.append(line)
    return ids


def derive_provider(model_id: str) -> str:
    model_id = (model_id or "").strip()
    if not model_id:
        return ""

    namespace = ""
    if "/" in model_id:
        namespace = model_id.split("/", 1)[0]

    if namespace:
        ns = namespace.lower()
        if ns.startswith("openai_native_files_manifold_pipe."):
            return ns.split(".")[-1]
        if ns.startswith("paper_rewrite.pw__"):
            tail = ns.split("paper_rewrite.pw__", 1)[1]
            return tail.split("-", 1)[0]
        if "." in ns:
            return ns.split(".")[-1]
        return ns

    head = model_id.split(".", 1)[0].lower()
    if head in KNOWN_PROVIDER_DISPLAY:
        return head
    return ""


def derive_model_segment(model_id: str) -> str:
    model_id = (model_id or "").strip()
    if "/" in model_id:
        return model_id.split("/", 1)[1]

    provider = derive_provider(model_id)
    if provider and model_id.lower().startswith(provider + "."):
        return model_id[len(provider) + 1 :]

    return model_id


def titleize_token(token: str) -> str:
    token = token.strip()
    if not token:
        return token

    lower = token.lower()
    if lower in KNOWN_PROVIDER_DISPLAY:
        return KNOWN_PROVIDER_DISPLAY[lower]

    if re.fullmatch(r"[a-z]+\d+", lower):
        return lower

    return token[:1].upper() + token[1:]


def titleize_phrase(raw: str) -> str:
    raw = (raw or "").strip()
    if not raw:
        return ""

    pieces = re.split(r"[\s._\-/]+", raw)
    titled = [titleize_token(piece) for piece in pieces if piece]
    return " ".join(titled)


def has_public_read(grants: Iterable[Dict[str, Any]]) -> bool:
    for grant in grants:
        if (
            grant.get("principal_type") == PUBLIC_GRANT_KEY[0]
            and grant.get("principal_id") == PUBLIC_GRANT_KEY[1]
            and grant.get("permission") == PUBLIC_GRANT_KEY[2]
        ):
            return True
    return False


def normalize_grants(grants: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    dedup: Dict[Tuple[str, str, str], Dict[str, Any]] = {}

    for grant in grants or []:
        if not isinstance(grant, dict):
            continue

        principal_type = grant.get("principal_type")
        principal_id = grant.get("principal_id")
        permission = grant.get("permission")

        if principal_type not in ("user", "group"):
            continue
        if permission not in ("read", "write"):
            continue
        if not isinstance(principal_id, str) or not principal_id:
            continue

        key = (principal_type, principal_id, permission)
        dedup[key] = {
            "id": grant.get("id") if isinstance(grant.get("id"), str) and grant.get("id") else str(uuid.uuid4()),
            "principal_type": principal_type,
            "principal_id": principal_id,
            "permission": permission,
        }

    return list(dedup.values())


def set_public(grants: Iterable[Dict[str, Any]], is_public: bool) -> List[Dict[str, Any]]:
    normalized = normalize_grants(grants)

    if is_public:
        if not has_public_read(normalized):
            normalized.append(
                {
                    "id": str(uuid.uuid4()),
                    "principal_type": "user",
                    "principal_id": "*",
                    "permission": "read",
                }
            )
        return normalized

    return [
        grant
        for grant in normalized
        if not (
            grant.get("principal_type") == PUBLIC_GRANT_KEY[0]
            and grant.get("principal_id") == PUBLIC_GRANT_KEY[1]
            and grant.get("permission") == PUBLIC_GRANT_KEY[2]
        )
    ]


def ensure_selector(args: argparse.Namespace) -> None:
    has_selector = bool(
        args.all
        or args.ids
        or args.id_file
        or args.id_regex
        or args.name_regex
        or args.provider
        or args.contains
        or args.active_only
        or args.inactive_only
    )
    if not has_selector:
        raise ValueError(
            "No selector provided. Use --all or at least one selector (--id/--id-file/--id-regex/--name-regex/--provider/--contains)."
        )


def apply_selectors(models: List[Dict[str, Any]], args: argparse.Namespace) -> List[Dict[str, Any]]:
    ensure_selector(args)

    ids = list(args.ids or [])
    if args.id_file:
        ids.extend(load_ids_from_file(args.id_file))
    id_set = {i.strip() for i in ids if i and i.strip()}

    id_pattern = re.compile(args.id_regex) if args.id_regex else None
    name_pattern = re.compile(args.name_regex) if args.name_regex else None
    providers = {p.strip().lower() for p in (args.provider or []) if p.strip()}
    contains = [q.strip().lower() for q in (args.contains or []) if q.strip()]

    selected = []
    for model in models:
        model_id = str(model.get("id", ""))
        name = str(model.get("name", ""))
        provider = derive_provider(model_id)
        active = bool(model.get("is_active", False))

        if id_set and model_id not in id_set:
            continue

        if id_pattern and not id_pattern.search(model_id):
            continue

        if name_pattern and not name_pattern.search(name):
            continue

        if providers and provider not in providers:
            continue

        if contains:
            content = f"{model_id}\n{name}".lower()
            if not all(c in content for c in contains):
                continue

        if args.active_only and not active:
            continue

        if args.inactive_only and active:
            continue

        selected.append(model)

    if args.limit and args.limit > 0:
        selected = selected[: args.limit]

    return selected


def parse_key_value(entry: str) -> Tuple[str, Any]:
    if "=" not in entry:
        raise ValueError(f"Invalid key=value input: {entry}")
    key, raw = entry.split("=", 1)
    key = key.strip()
    raw = raw.strip()
    if not key:
        raise ValueError(f"Invalid key in input: {entry}")

    lowered = raw.lower()
    if lowered == "true":
        return key, True
    if lowered == "false":
        return key, False
    if lowered == "null":
        return key, None

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        parsed = raw

    return key, parsed


def resolve_system_prompt(args: argparse.Namespace) -> Optional[str]:
    if args.clear:
        return None
    if args.text is not None:
        return args.text
    if args.file:
        return Path(args.file).read_text(encoding="utf-8")
    raise ValueError("Provide --text, --file, or --clear for system-prompt")


def choose_icon_url(
    model: Dict[str, Any],
    args: argparse.Namespace,
    rules_doc: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    if args.url:
        return args.url

    if rules_doc is None:
        loaded = load_json_file(args.map_file)
        if not isinstance(loaded, dict):
            raise ValueError("Icon map file must be a JSON object")
        rules_doc = loaded

    rules = rules_doc.get("rules", [])
    fallback_url = args.fallback_url if args.fallback_url is not None else rules_doc.get("fallback_url")

    model_id = str(model.get("id", ""))
    provider = derive_provider(model_id)
    model_name = str(model.get("name", ""))

    for rule in rules:
        if not isinstance(rule, dict):
            continue
        url = rule.get("url")
        if not isinstance(url, str) or not url.strip():
            continue

        if not rule_matches(rule, model_id=model_id, provider=provider, name=model_name):
            continue

        return url.strip()

    if isinstance(fallback_url, str) and fallback_url.strip():
        return fallback_url.strip()
    return None


def rule_matches(rule: Dict[str, Any], *, model_id: str, provider: str, name: str) -> bool:
    if "id_prefix" in rule:
        id_prefix = str(rule.get("id_prefix", ""))
        if not model_id.startswith(id_prefix):
            return False

    if "id_regex" in rule:
        pattern = str(rule.get("id_regex", ""))
        if not re.search(pattern, model_id):
            return False

    if "provider" in rule:
        wanted = str(rule.get("provider", "")).strip().lower()
        if provider != wanted:
            return False

    if "contains" in rule:
        needle = str(rule.get("contains", "")).strip().lower()
        content = f"{model_id}\n{name}".lower()
        if needle not in content:
            return False

    return True


def resolve_provider_title(provider: str, alias_map: Optional[Dict[str, str]] = None) -> str:
    provider = (provider or "").strip().lower()
    if alias_map and provider in alias_map:
        return alias_map[provider]
    if provider in KNOWN_PROVIDER_DISPLAY:
        return KNOWN_PROVIDER_DISPLAY[provider]
    return titleize_phrase(provider) if provider else "Model"


def format_normalized_name(model: Dict[str, Any], template: str, alias_map: Optional[Dict[str, str]]) -> str:
    model_id = str(model.get("id", ""))
    provider = derive_provider(model_id)
    segment = derive_model_segment(model_id)

    context = {
        "id": model_id,
        "name": str(model.get("name", "")),
        "provider": provider,
        "provider_title": resolve_provider_title(provider, alias_map),
        "model": segment,
        "model_title": titleize_phrase(segment),
    }

    try:
        value = template.format(**context).strip()
    except KeyError as exc:
        raise ValueError(f"Unknown template key: {exc}") from exc

    return value


def print_models(models: List[Dict[str, Any]], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(models, ensure_ascii=False, indent=2))
        return

    print(f"Selected models: {len(models)}")
    for model in models:
        model_id = str(model.get("id", ""))
        provider = derive_provider(model_id)
        name = str(model.get("name", ""))
        active = bool(model.get("is_active", False))
        public = has_public_read(model.get("access_grants") or [])
        visibility = "public" if public else "private"
        status = "active" if active else "inactive"
        print(f"- {model_id} | {name} | provider={provider or '-'} | {status} | {visibility}")


def print_summary(summary: Summary, *, as_json: bool) -> None:
    data = {
        "selected": summary.selected,
        "success": summary.success,
        "failed": summary.failed,
        "skipped": summary.skipped,
        "planned": summary.planned,
    }
    if as_json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return

    print("Summary:")
    print(f"- selected: {summary.selected}")
    print(f"- success: {summary.success}")
    print(f"- failed: {summary.failed}")
    print(f"- skipped: {summary.skipped}")
    if summary.planned:
        print(f"- planned (dry-run): {summary.planned}")


def run_capabilities(
    client: OpenWebUIClient,
    models: List[Dict[str, Any]],
    args: argparse.Namespace,
) -> Summary:
    patch: Dict[str, Any] = {}

    for item in args.set or []:
        key, value = parse_key_value(item)
        patch[key] = value

    for key in args.remove or []:
        patch[key] = None

    if args.json_file:
        json_patch = load_json_file(args.json_file)
        if not isinstance(json_patch, dict):
            raise ValueError("--json-file must contain a JSON object")
        patch.update(json_patch)

    if not patch:
        raise ValueError("No capability patch provided. Use --set/--remove/--json-file")

    summary = Summary(selected=len(models))
    for model in models:
        model_id = model["id"]
        if args.dry_run:
            print(f"[DRY-RUN] capabilities -> {model_id}: {json.dumps(patch, ensure_ascii=False)}")
            summary.planned += 1
            continue

        try:
            client.update_capabilities(model_id, patch)
            print(f"[OK] capabilities updated: {model_id}")
            summary.success += 1
        except ApiError as exc:
            print(f"[ERR] capabilities failed: {model_id} ({exc.status}) {exc}", file=sys.stderr)
            summary.failed += 1

    return summary


def run_system_prompt(
    client: OpenWebUIClient,
    models: List[Dict[str, Any]],
    args: argparse.Namespace,
) -> Summary:
    prompt = resolve_system_prompt(args)
    preview = "<clear>" if prompt is None else (prompt[:80] + ("..." if len(prompt) > 80 else ""))

    summary = Summary(selected=len(models))
    for model in models:
        model_id = model["id"]
        if args.dry_run:
            print(f"[DRY-RUN] system-prompt -> {model_id}: {preview}")
            summary.planned += 1
            continue

        try:
            client.update_system_prompt(model_id, prompt)
            print(f"[OK] system prompt updated: {model_id}")
            summary.success += 1
        except ApiError as exc:
            print(f"[ERR] system prompt failed: {model_id} ({exc.status}) {exc}", file=sys.stderr)
            summary.failed += 1

    return summary


def run_access(
    client: OpenWebUIClient,
    models: List[Dict[str, Any]],
    args: argparse.Namespace,
) -> Summary:
    is_public = args.public
    summary = Summary(selected=len(models))

    for model in models:
        model_id = model["id"]
        existing = model.get("access_grants") or []
        next_grants = set_public(existing, is_public=is_public)

        if normalize_grants(existing) == normalize_grants(next_grants):
            print(f"[SKIP] access unchanged: {model_id}")
            summary.skipped += 1
            continue

        if args.dry_run:
            mode = "public" if is_public else "private"
            print(f"[DRY-RUN] access -> {model_id}: {mode}")
            summary.planned += 1
            continue

        try:
            client.update_access_grants(model_id, next_grants)
            mode = "public" if is_public else "private"
            print(f"[OK] access updated: {model_id} -> {mode}")
            summary.success += 1
        except ApiError as exc:
            print(f"[ERR] access failed: {model_id} ({exc.status}) {exc}", file=sys.stderr)
            summary.failed += 1

    return summary


def run_icon(
    client: OpenWebUIClient,
    models: List[Dict[str, Any]],
    args: argparse.Namespace,
) -> Summary:
    summary = Summary(selected=len(models))
    rules_doc = None
    if args.map_file:
        loaded = load_json_file(args.map_file)
        if not isinstance(loaded, dict):
            raise ValueError("Icon map file must be a JSON object")
        rules_doc = loaded

    for model in models:
        model_id = model["id"]
        try:
            url = choose_icon_url(model, args, rules_doc=rules_doc)
        except ValueError as exc:
            raise ValueError(f"Icon rule parse failed: {exc}") from exc

        if not url:
            print(f"[SKIP] no icon rule matched: {model_id}")
            summary.skipped += 1
            continue

        if args.dry_run:
            print(f"[DRY-RUN] icon -> {model_id}: {url}")
            summary.planned += 1
            continue

        try:
            client.update_icon(model_id, url)
            print(f"[OK] icon updated: {model_id}")
            summary.success += 1
        except ApiError as exc:
            print(f"[ERR] icon failed: {model_id} ({exc.status}) {exc}", file=sys.stderr)
            summary.failed += 1

    return summary


def run_normalize_name(
    client: OpenWebUIClient,
    models: List[Dict[str, Any]],
    args: argparse.Namespace,
) -> Summary:
    alias_map = None
    if args.provider_alias_file:
        doc = load_json_file(args.provider_alias_file)
        if not isinstance(doc, dict):
            raise ValueError("--provider-alias-file must contain a JSON object")
        alias_map = {str(k).strip().lower(): str(v).strip() for k, v in doc.items()}

    summary = Summary(selected=len(models))
    for model in models:
        model_id = model["id"]

        try:
            new_name = format_normalized_name(model, args.template, alias_map)
        except ValueError as exc:
            raise ValueError(f"Name template error: {exc}") from exc

        old_name = str(model.get("name", ""))
        if not new_name:
            print(f"[SKIP] empty normalized name: {model_id}")
            summary.skipped += 1
            continue

        if old_name == new_name:
            print(f"[SKIP] name already normalized: {model_id}")
            summary.skipped += 1
            continue

        if args.dry_run:
            print(f"[DRY-RUN] rename -> {model_id}: {old_name!r} -> {new_name!r}")
            summary.planned += 1
            continue

        try:
            client.update_name(model, new_name)
            print(f"[OK] renamed: {model_id} -> {new_name}")
            summary.success += 1
        except ApiError as exc:
            print(f"[ERR] rename failed: {model_id} ({exc.status}) {exc}", file=sys.stderr)
            summary.failed += 1

    return summary


def add_selector_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--all", action="store_true", help="Select all models (can be combined with other filters)")
    parser.add_argument("--id", dest="ids", action="append", default=[], help="Select exact model ID (repeatable)")
    parser.add_argument("--id-file", help="Path to newline-separated model IDs")
    parser.add_argument("--id-regex", help="Regex to match model id")
    parser.add_argument("--name-regex", help="Regex to match model display name")
    parser.add_argument("--provider", action="append", default=[], help="Match derived provider (repeatable)")
    parser.add_argument("--contains", action="append", default=[], help="Substring that must appear in id/name (repeatable)")

    active_group = parser.add_mutually_exclusive_group()
    active_group.add_argument("--active-only", action="store_true", help="Only active models")
    active_group.add_argument("--inactive-only", action="store_true", help="Only inactive models")

    parser.add_argument("--limit", type=int, default=0, help="Limit selected models after filtering")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Open WebUI model governance CLI")
    parser.add_argument("--base-url", default=os.environ.get("OPEN_WEBUI_BASE_URL", DEFAULT_BASE_URL))
    parser.add_argument("--token", default=os.environ.get("OPEN_WEBUI_API_TOKEN"), help="Open WebUI Bearer token")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    parser.add_argument("--json-output", action="store_true", help="Print list/summary in JSON")

    sub = parser.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list", help="List models with optional selectors")
    add_selector_args(p_list)

    p_cap = sub.add_parser("capabilities", help="Batch update model capabilities")
    add_selector_args(p_cap)
    p_cap.add_argument("--set", action="append", default=[], metavar="KEY=VALUE", help="Set capability key (repeatable)")
    p_cap.add_argument("--remove", action="append", default=[], metavar="KEY", help="Remove capability key (repeatable)")
    p_cap.add_argument("--json-file", help="JSON file with capability patch object")
    p_cap.add_argument("--dry-run", action="store_true")

    p_prompt = sub.add_parser("system-prompt", help="Batch set/clear model system prompt")
    add_selector_args(p_prompt)
    prompt_group = p_prompt.add_mutually_exclusive_group(required=True)
    prompt_group.add_argument("--text", help="Set system prompt text")
    prompt_group.add_argument("--file", help="Read system prompt from file")
    prompt_group.add_argument("--clear", action="store_true", help="Clear system prompt")
    p_prompt.add_argument("--dry-run", action="store_true")

    p_access = sub.add_parser("access", help="Batch switch model visibility")
    add_selector_args(p_access)
    access_group = p_access.add_mutually_exclusive_group(required=True)
    access_group.add_argument("--public", action="store_true", help="Enable public read")
    access_group.add_argument("--private", action="store_true", help="Disable public read")
    p_access.add_argument("--dry-run", action="store_true")

    p_icon = sub.add_parser("icon", help="Batch set model icon")
    add_selector_args(p_icon)
    icon_group = p_icon.add_mutually_exclusive_group(required=True)
    icon_group.add_argument("--url", help="Uniform icon URL for all selected models")
    icon_group.add_argument("--map-file", help="JSON file with icon mapping rules")
    p_icon.add_argument("--fallback-url", help="Fallback URL used with --map-file")
    p_icon.add_argument("--dry-run", action="store_true")

    p_name = sub.add_parser("normalize-name", help="Batch normalize display names")
    add_selector_args(p_name)
    p_name.add_argument(
        "--template",
        default="{provider_title} · {model_title}",
        help="Name template. Keys: {id},{name},{provider},{provider_title},{model},{model_title}",
    )
    p_name.add_argument("--provider-alias-file", help="JSON file mapping provider->display name")
    p_name.add_argument("--dry-run", action="store_true")

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if not args.token:
        print("Missing token. Set --token or OPEN_WEBUI_API_TOKEN", file=sys.stderr)
        return 2

    client = OpenWebUIClient(args.base_url, args.token, timeout=args.timeout)

    try:
        all_models = client.list_models()
    except ApiError as exc:
        print(f"Failed to list models: ({exc.status}) {exc}", file=sys.stderr)
        return 1

    try:
        selected = apply_selectors(all_models, args)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    if args.command == "list":
        print_models(selected, as_json=args.json_output)
        return 0

    if not selected:
        print("No models matched selectors.")
        return 0

    if args.command == "capabilities":
        try:
            summary = run_capabilities(client, selected, args)
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 2
    elif args.command == "system-prompt":
        try:
            summary = run_system_prompt(client, selected, args)
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 2
    elif args.command == "access":
        summary = run_access(client, selected, args)
    elif args.command == "icon":
        try:
            summary = run_icon(client, selected, args)
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 2
    elif args.command == "normalize-name":
        try:
            summary = run_normalize_name(client, selected, args)
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 2
    else:
        print(f"Unsupported command: {args.command}", file=sys.stderr)
        return 2

    print_summary(summary, as_json=args.json_output)
    return 1 if summary.failed > 0 else 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class CustomerScope:
    """Stable identity boundaries for one customer and one WeCom sales account."""

    sales_contact_key: str
    global_customer_key: str
    corp_id: str
    wechat: str
    external_userid: str
    customer_id: str
    customer_add_wechat_id: str
    user_id: str
    persistence_allowed: bool
    missing: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def customer_scope_from_state(state: Mapping[str, Any]) -> CustomerScope:
    request_context = state.get("request_context") if isinstance(state.get("request_context"), Mapping) else {}
    return build_customer_scope(
        corp_id=request_context.get("corp_id") or state.get("corp_id"),
        wechat=request_context.get("wechat") or state.get("wechat"),
        external_userid=request_context.get("external_userid") or state.get("external_userid"),
        customer_id=request_context.get("customer_id") or state.get("customer_id"),
        customer_add_wechat_id=request_context.get("customer_add_wechat_id") or state.get("customer_add_wechat_id"),
        user_id=request_context.get("user_id") or state.get("user_id"),
    )


def customer_scope_from_identity(identity: Mapping[str, Any]) -> CustomerScope:
    return build_customer_scope(
        corp_id=identity.get("corp_id"),
        wechat=identity.get("wechat"),
        external_userid=identity.get("external_userid"),
        customer_id=identity.get("customer_id"),
        customer_add_wechat_id=identity.get("customer_add_wechat_id"),
        user_id=identity.get("user_id"),
    )


def build_customer_scope(
    *,
    corp_id: Any = "",
    wechat: Any = "",
    external_userid: Any = "",
    customer_id: Any = "",
    customer_add_wechat_id: Any = "",
    user_id: Any = "",
) -> CustomerScope:
    corp = _clean(corp_id)
    account = _clean(wechat)
    external = _clean(external_userid)
    customer = _clean(customer_id)
    relation = _clean(customer_add_wechat_id)
    operator = _clean(user_id)
    customer_identity = external or customer

    missing = tuple(
        name
        for name, value in (("corp_id", corp), ("wechat", account), ("customer_identity", customer_identity))
        if not value
    )
    persistence_allowed = not missing
    global_key = _digest_key("customer", corp, customer_identity) if corp and customer_identity else ""
    sales_key = _digest_key("sales_contact", corp, account, customer_identity) if persistence_allowed else ""
    return CustomerScope(
        sales_contact_key=sales_key,
        global_customer_key=global_key,
        corp_id=corp,
        wechat=account,
        external_userid=external,
        customer_id=customer,
        customer_add_wechat_id=relation,
        user_id=operator,
        persistence_allowed=persistence_allowed,
        missing=missing,
    )


def _digest_key(kind: str, *parts: str) -> str:
    canonical = "\x1f".join([kind, *parts])
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"{kind}:v2:{digest}"


def _clean(value: Any) -> str:
    return str(value or "").strip()

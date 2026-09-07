from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping


class IdentityContractError(ValueError):
    pass


@dataclass(frozen=True)
class CustomerIdentity:
    corp_id: str
    wechat: str
    external_userid: str
    platform_customer_id: str
    platform_user_id: str
    customer_add_wechat_id: str
    platform_customer_id_source: str

    def as_dict(self) -> dict[str, str]:
        return asdict(self)

    def as_legacy_dict(self) -> dict[str, str]:
        values = self.as_dict()
        values["customer_id"] = self.platform_customer_id
        values["user_id"] = self.platform_user_id
        return values


def clean_identifier(value: Any) -> str:
    return str(value or "").strip()


def looks_like_external_userid(value: Any) -> bool:
    return clean_identifier(value).lower().startswith("wm")


def canonical_platform_customer_id(
    *,
    platform_customer_id: Any = "",
    legacy_customer_id: Any = "",
    external_userid: Any = "",
    allow_empty: bool = False,
    allow_synthetic: bool = False,
) -> tuple[str, str]:
    explicit = clean_identifier(platform_customer_id)
    legacy = clean_identifier(legacy_customer_id)
    external = clean_identifier(external_userid)
    if explicit and legacy and explicit != legacy:
        raise IdentityContractError("platform_customer_id conflicts with legacy customer_id")
    candidate = explicit or legacy
    source = "platform_customer_id" if explicit else "legacy_customer_id" if legacy else ""
    synthetic = allow_synthetic and candidate.lower().startswith("sim_")
    if candidate and not synthetic and (looks_like_external_userid(candidate) or (external and candidate == external)):
        raise IdentityContractError("platform customer ID must not contain an external_userid")
    if not candidate and not allow_empty:
        raise IdentityContractError("platform_customer_id is required")
    return candidate, source


def customer_identity_from_mapping(
    values: Mapping[str, Any],
    *,
    allow_empty_platform_customer_id: bool = False,
    allow_synthetic: bool = False,
) -> CustomerIdentity:
    external = clean_identifier(
        values.get("external_userid")
        or values.get("customer_wechat_id")
        or values.get("customerWechatId")
        or values.get("customerWechat")
    )
    platform_customer_id, source = canonical_platform_customer_id(
        platform_customer_id=values.get("platform_customer_id") or values.get("platformCustomerId"),
        legacy_customer_id=values.get("customer_id") or values.get("customerId"),
        external_userid=external,
        allow_empty=allow_empty_platform_customer_id,
        allow_synthetic=allow_synthetic,
    )
    return CustomerIdentity(
        corp_id=clean_identifier(values.get("corp_id") or values.get("corpId") or values.get("wecomCorpId")),
        wechat=clean_identifier(values.get("wechat") or values.get("user_wechat") or values.get("userWechat")),
        external_userid=external,
        platform_customer_id=platform_customer_id,
        platform_user_id=clean_identifier(
            values.get("platform_user_id")
            or values.get("user_id")
            or values.get("user_wechat_id")
            or values.get("userWechatId")
        ),
        customer_add_wechat_id=clean_identifier(
            values.get("customer_add_wechat_id")
            or values.get("customerAddWechatId")
            or values.get("customerWechatRelationId")
        ),
        platform_customer_id_source=source,
    )


def missing_managed_identity_fields(identity: CustomerIdentity) -> list[str]:
    required = {
        "corp_id": identity.corp_id,
        "wechat": identity.wechat,
        "external_userid": identity.external_userid,
        "platform_customer_id": identity.platform_customer_id,
        "platform_user_id": identity.platform_user_id,
    }
    return [name for name, value in required.items() if not value]

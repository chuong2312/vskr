"""Visibility rules shared by catalog, file access, and retrieval."""


def is_visible(meta: dict, viewer_id=None, *, shared_only=False, admin=False) -> bool:
    if admin:
        return True
    owner = meta.get("owner") or meta.get("owner_id")
    visibility = meta.get("visibility", "private")
    if visibility == "public":
        return True
    if shared_only:
        return False
    return owner is not None and owner in {viewer_id, f"u:{viewer_id}"}

"""Quy tắc quyền xem duy nhất, dùng chung cho thư viện, mở file và tìm kiếm."""


def is_visible(meta: dict, viewer_id=None, *, shared_only=False, admin=False) -> bool:
    """Người xem có được thấy học liệu này không.

    Admin thấy tất cả; học liệu ``public`` ai cũng thấy; còn lại chỉ chủ sở hữu thấy, và không ai thấy
    khi ``shared_only`` (chỉ xem phần dùng chung). Metadata thiếu ``visibility`` được coi là ``private``.
    """
    if admin:
        return True
    owner = meta.get("owner") or meta.get("owner_id")
    visibility = meta.get("visibility", "private")
    if visibility == "public":
        return True
    if shared_only:
        return False
    return owner is not None and owner in {viewer_id, f"u:{viewer_id}"}

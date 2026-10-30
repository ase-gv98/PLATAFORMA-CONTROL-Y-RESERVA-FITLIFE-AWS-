from rest_framework.permissions import BasePermission, SAFE_METHODS


class AdminOrReadOnly(BasePermission):
    def has_permission(self, request, view):
        return request.user and request.user.is_authenticated and (
            request.method in SAFE_METHODS or request.user.is_staff
        )


class AdminOnly(BasePermission):
    def has_permission(self, request, view):
        return bool(request.user and request.user.is_staff)


class AdminOrOwner(BasePermission):
    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated)

    def has_object_permission(self, request, view, obj):
        if request.user.is_staff:
            return True
        member = getattr(obj, 'member', obj)
        return getattr(member, 'user_id', None) == request.user.id


class AdminWriteOwnRead(AdminOrOwner):
    def has_permission(self, request, view):
        return bool(
            request.user
            and request.user.is_authenticated
            and (request.method in SAFE_METHODS or request.user.is_staff)
        )


class BookingPermission(AdminOrOwner):
    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated)

from django.urls import include, path
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework.routers import DefaultRouter
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response
from rest_framework import serializers as drf_serializers
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView
from rest_framework_simplejwt.views import (
    TokenBlacklistView,
    TokenObtainPairView,
    TokenRefreshView,
)
from .views import (AccessLogViewSet, BookingViewSet, FacilityEntryViewSet, GroupClassViewSet,
                    MemberProfileViewSet, MembershipViewSet, PaymentViewSet, PlanViewSet, RegisterViewSet,
                    health)

router = DefaultRouter()
router.register('plans', PlanViewSet)
router.register('members', MemberProfileViewSet)
router.register('memberships', MembershipViewSet)
router.register('payments', PaymentViewSet)
router.register('classes', GroupClassViewSet)
router.register('bookings', BookingViewSet)
router.register('access-logs', AccessLogViewSet)
router.register('entries', FacilityEntryViewSet)
router.register('auth', RegisterViewSet, basename='auth')

@extend_schema(responses=inline_serializer(
    name='ApiRootResponse',
    fields={
        'plans': drf_serializers.URLField(),
        'members': drf_serializers.URLField(),
        'memberships': drf_serializers.URLField(),
        'payments': drf_serializers.URLField(),
        'classes': drf_serializers.URLField(),
        'bookings': drf_serializers.URLField(),
        'access_logs': drf_serializers.URLField(),
        'entries': drf_serializers.URLField(),
    },
))
@api_view(['GET'])
@permission_classes([IsAdminUser])
def api_root(request):
    return Response({
        'plans': request.build_absolute_uri('plans/'),
        'members': request.build_absolute_uri('members/'),
        'memberships': request.build_absolute_uri('memberships/'),
        'payments': request.build_absolute_uri('payments/'),
        'classes': request.build_absolute_uri('classes/'),
        'bookings': request.build_absolute_uri('bookings/'),
        'access_logs': request.build_absolute_uri('access-logs/'),
        'entries': request.build_absolute_uri('entries/'),
    })

urlpatterns = [
    path('token/', TokenObtainPairView.as_view(), name='token_obtain_pair'),
    path('token/refresh/', TokenRefreshView.as_view(), name='token_refresh'),
    path('token/blacklist/', TokenBlacklistView.as_view(), name='token_blacklist'),
    path('health/', health, name='api-health'),
    path('schema/', SpectacularAPIView.as_view(), name='schema'),
    path('docs/', SpectacularSwaggerView.as_view(url_name='schema'), name='swagger-ui'),
    path('', api_root, name='api-root'),
    path('', include(router.urls)),
]

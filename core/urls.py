from django.urls import include, path
from rest_framework.routers import DefaultRouter
from rest_framework.authtoken.views import obtain_auth_token
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response
from .views import (AccessLogViewSet, BookingViewSet, FacilityEntryViewSet, GroupClassViewSet,
                    MemberProfileViewSet, MembershipViewSet, PaymentViewSet, PlanViewSet, RegisterViewSet)

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
    path('token/', obtain_auth_token, name='api-token'),
    path('', api_root, name='api-root'),
    path('', include(router.urls)),
]

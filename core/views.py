from datetime import timedelta
from functools import wraps
from uuid import uuid4

from django.contrib.auth.decorators import login_required
from django.contrib.admin.models import CHANGE, DELETION, LogEntry
from django.contrib.contenttypes.models import ContentType
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models.deletion import ProtectedError
from django.shortcuts import get_object_or_404, redirect, render
from django.core.paginator import Paginator
from django.db.models import Q
from django.utils import timezone
from rest_framework import decorators, permissions, response, status, viewsets
from .models import AccessLog, Booking, FacilityEntry, GroupClass, MemberProfile, Membership, Payment, Plan
from .forms import (GroupClassForm, MemberCreateForm, MembershipForm, PaymentForm, PlanForm, QRScanForm,
                    StaffMembershipForm, StaffPaymentForm)
from .serializers import (AccessLogSerializer, BookingSerializer, FacilityEntrySerializer, GroupClassSerializer,
                          MemberProfileSerializer, MembershipSerializer, PaymentSerializer, PlanSerializer,
                          UserRegistrationSerializer)
from .services import book_class, cancel_booking, register_exit, validate_qr_access
from .permissions import AdminOnly, AdminOrOwner, AdminOrReadOnly, AdminWriteOwnRead, BookingPermission


def is_admin(user):
    return user.is_authenticated and (user.is_staff or user.is_superuser)


def home(request):
    if request.user.is_authenticated:
        return redirect('login-redirect')
    return render(request, 'core/home.html')


def forbidden(request, exception=None):
    return render(request, '403.html', status=403)


def bad_request(request, exception=None):
    return render(request, '400.html', status=400)


def not_found(request, exception=None):
    return render(request, '404.html', status=404)


def server_error(request):
    return render(request, '500.html', status=500)


def admin_required(view):
    @wraps(view)
    @login_required
    def wrapped(request, *args, **kwargs):
        if not is_admin(request.user):
            raise PermissionDenied
        return view(request, *args, **kwargs)
    return wrapped


@login_required
def login_redirect(request):
    return redirect('dashboard' if is_admin(request.user) else 'member-dashboard')


@login_required
def dashboard(request):
    if not is_admin(request.user):
        return redirect('member-dashboard')
    today = timezone.localdate()
    context = {
        'occupancy': FacilityEntry.objects.filter(entered_at__date=today, exited_at__isnull=True).count(),
        'authorized_today': AccessLog.objects.filter(scanned_at__date=today, allowed=True).count(),
        'memberships': Membership.objects.filter(estado=Membership.Status.ACTIVE, end_date__gte=timezone.localdate()).count(),
        'classes': GroupClass.objects.filter(active=True, starts_at__gte=timezone.now()).order_by('starts_at')[:5],
    }
    return render(request, 'core/dashboard.html', context)


@login_required
def member_dashboard(request):
    try:
        member = request.user.member_profile
    except MemberProfile.DoesNotExist:
        messages.error(request, 'Tu usuario no tiene un perfil de socio configurado.')
        return redirect('logout')
    membership = member.memberships.select_related('plan').order_by('-end_date').first()
    payments = member.payments.select_related('membership__plan').order_by('-paid_at')[:10]
    return render(request, 'core/member_dashboard.html', {'member': member, 'membership': membership, 'payments': payments})


@login_required
def member_qr_page(request):
    if is_admin(request.user):
        return redirect('access-qr')
    return render(request, 'core/member_qr.html', {'member': request.user.member_profile})


@login_required
def plans_page(request):
    return render(request, 'core/plans.html', {'plans': Plan.objects.filter(active=True)})


@login_required
def memberships_page(request):
    if is_admin(request.user):
        memberships = Membership.objects.select_related('member__user', 'plan').all()
    else:
        memberships = request.user.member_profile.memberships.select_related('plan')
    return render(request, 'core/memberships.html', {'memberships': memberships})


@login_required
def classes_page(request):
    classes = GroupClass.objects.filter(active=True, ends_at__gte=timezone.now()).prefetch_related('bookings')
    return render(request, 'core/classes.html', {'classes': classes})


@login_required
def bookings_page(request):
    bookings = Booking.objects.select_related('group_class', 'member__user').all()
    if not is_admin(request.user):
        bookings = bookings.filter(member=request.user.member_profile)
    return render(request, 'core/bookings.html', {'bookings': bookings})


@login_required
def book_class_page(request, class_id):
    if request.method != 'POST':
        return redirect('classes')
    if is_admin(request.user):
        messages.error(request, 'Las reservas deben realizarse desde una cuenta de socio.')
        return redirect('classes')
    group_class = get_object_or_404(GroupClass, pk=class_id, active=True)
    try:
        booking = book_class(request.user.member_profile, group_class)
    except (AttributeError, ValueError) as error:
        messages.error(request, str(error))
    else:
        messages.success(request, f'Reserva registrada: {booking.get_status_display()}.')
    return redirect('classes')


@login_required
def cancel_booking_page(request, booking_id):
    if request.method != 'POST':
        return redirect('bookings')
    filters = {'pk': booking_id}
    if not is_admin(request.user):
        filters['member'] = request.user.member_profile
    booking = get_object_or_404(Booking, **filters)
    try:
        cancel_booking(booking)
    except ValueError as error:
        messages.error(request, str(error))
    else:
        messages.success(request, 'La reserva fue cancelada correctamente.')
    return redirect('bookings')


@login_required
def access_qr_page(request):
    if not is_admin(request.user):
        raise PermissionDenied
    form = QRScanForm(request.POST or None)
    result = None
    if request.method == 'POST' and form.is_valid():
        identifier = form.cleaned_data['qr_token'].strip()
        member = MemberProfile.objects.select_related('user').filter(qr_token=identifier).first()
        if member is None:
            member = MemberProfile.objects.select_related('user').filter(rut__iexact=identifier).first()
        result = validate_qr_access(member.qr_token) if member else validate_qr_access(identifier)
        if result.allowed:
            messages.success(request, f'Acceso autorizado para {result.member}.')
        else:
            messages.error(request, f'Acceso rechazado: {result.reason}')
        form = QRScanForm()
    logs = AccessLog.objects.select_related('member__user')[:10]
    return render(request, 'core/access_qr.html', {'form': form, 'logs': logs, 'result': result})


def _admin_log(request, obj, message, action=CHANGE):
    LogEntry.objects.log_action(
        user_id=request.user.pk,
        content_type_id=ContentType.objects.get_for_model(obj).pk,
        object_id=obj.pk,
        object_repr=str(obj),
        action_flag=action,
        change_message=message,
    )


@admin_required
def admin_payments_panel(request):
    payments = Payment.objects.select_related('member__user', 'membership__plan').all()
    query = request.GET.get('q', '').strip()
    if query:
        payments = payments.filter(
            Q(member__user__first_name__icontains=query)
            | Q(member__user__last_name__icontains=query)
            | Q(member__user__username__icontains=query)
            | Q(member__rut__icontains=query)
        )
    for field in ('status', 'payment_method'):
        value = request.GET.get(field, '').strip()
        if value:
            payments = payments.filter(**{field: value})
    for param, lookup in (('date_from', 'paid_at__date__gte'), ('date_to', 'paid_at__date__lte')):
        value = request.GET.get(param, '').strip()
        if value:
            payments = payments.filter(**{lookup: value})
    page_obj = Paginator(payments.order_by('-paid_at', '-id'), 10).get_page(request.GET.get('page'))
    return render(request, 'core/admin_payments.html', {
        'page_obj': page_obj,
        'query': query,
        'status_filter': request.GET.get('status', ''),
        'method_filter': request.GET.get('payment_method', ''),
        'date_from': request.GET.get('date_from', ''),
        'date_to': request.GET.get('date_to', ''),
        'payment_statuses': Payment.Status.choices,
        'payment_methods': Payment.Method.choices,
    })


@admin_required
def admin_payment_panel_create(request):
    form = StaffPaymentForm(request.POST or None)
    if form.is_valid():
        payment = form.save()
        _admin_log(request, payment, 'Pago creado desde el panel FitLife.')
        messages.success(request, 'Pago creado correctamente.')
        return redirect('admin-payments-panel')
    return render(request, 'core/panel_form.html', {'form': form, 'title': 'Nuevo pago', 'back_url': 'admin-payments-panel'})


@admin_required
def admin_payment_panel_edit(request, payment_id):
    payment = get_object_or_404(Payment, pk=payment_id)
    form = StaffPaymentForm(request.POST or None, instance=payment)
    if form.is_valid():
        payment = form.save()
        _admin_log(request, payment, 'Pago actualizado desde el panel FitLife.')
        messages.success(request, 'Pago actualizado correctamente.')
        return redirect('admin-payments-panel')
    return render(request, 'core/panel_form.html', {'form': form, 'title': 'Editar pago', 'back_url': 'admin-payments-panel'})


@admin_required
def admin_payment_panel_delete(request, payment_id):
    payment = get_object_or_404(Payment, pk=payment_id)
    if request.method == 'POST':
        _admin_log(request, payment, 'Pago eliminado desde el panel FitLife.', DELETION)
        payment.delete()
        messages.success(request, 'Pago eliminado correctamente.')
        return redirect('admin-payments-panel')
    return render(request, 'core/panel_confirm_delete.html', {'object': payment, 'title': 'Eliminar pago', 'back_url': 'admin-payments-panel'})


@admin_required
def admin_memberships_panel(request):
    memberships = Membership.objects.select_related('member__user', 'plan').all()
    query = request.GET.get('q', '').strip()
    if query:
        memberships = memberships.filter(
            Q(member__user__first_name__icontains=query)
            | Q(member__user__last_name__icontains=query)
            | Q(member__user__username__icontains=query)
            | Q(member__rut__icontains=query)
        )
    estado = request.GET.get('estado', '').strip()
    if estado:
        memberships = memberships.filter(estado=estado)
    page_obj = Paginator(memberships.order_by('-start_date', '-id'), 10).get_page(request.GET.get('page'))
    return render(request, 'core/admin_memberships.html', {
        'page_obj': page_obj,
        'query': query,
        'estado_filter': estado,
        'membership_statuses': Membership.Status.choices,
    })


@admin_required
def admin_membership_panel_create(request):
    form = StaffMembershipForm(request.POST or None)
    if form.is_valid():
        membership = form.save()
        _admin_log(request, membership, f'Membresía creada con estado {membership.get_estado_display()}.')
        messages.success(request, 'Membresía creada correctamente.')
        return redirect('admin-memberships-panel')
    return render(request, 'core/panel_form.html', {'form': form, 'title': 'Nueva membresía', 'back_url': 'admin-memberships-panel'})


@admin_required
def admin_membership_panel_edit(request, membership_id):
    membership = get_object_or_404(Membership, pk=membership_id)
    previous_state = membership.estado
    form = StaffMembershipForm(request.POST or None, instance=membership)
    if form.is_valid():
        membership = form.save()
        if previous_state != membership.estado:
            _admin_log(request, membership, f'Estado cambiado a {membership.get_estado_display()} desde el panel FitLife.')
        messages.success(request, 'Membresía actualizada correctamente.')
        return redirect('admin-memberships-panel')
    return render(request, 'core/panel_form.html', {'form': form, 'title': 'Editar membresía', 'back_url': 'admin-memberships-panel'})


@admin_required
def admin_membership_panel_state(request, membership_id, estado):
    if request.method != 'POST':
        return redirect('admin-memberships-panel')
    membership = get_object_or_404(Membership, pk=membership_id)
    membership.estado = estado
    try:
        membership.save()
    except ValidationError as error:
        messages.error(request, error.messages[0] if hasattr(error, 'messages') else str(error))
    else:
        _admin_log(request, membership, f'Estado cambiado a {membership.get_estado_display()} desde el panel FitLife.')
        messages.success(request, 'Estado de membresía actualizado correctamente.')
    return redirect('admin-memberships-panel')


@admin_required
def admin_membership_panel_delete(request, membership_id):
    membership = get_object_or_404(Membership, pk=membership_id)
    if request.method == 'POST':
        _admin_log(request, membership, 'Membresía eliminada desde el panel FitLife.', DELETION)
        membership.delete()
        messages.success(request, 'Membresía eliminada correctamente.')
        return redirect('admin-memberships-panel')
    return render(request, 'core/panel_confirm_delete.html', {'object': membership, 'title': 'Eliminar membresía', 'back_url': 'admin-memberships-panel'})


@admin_required
def admin_plans(request):
    return render(request, 'core/admin_plans.html', {'plans': Plan.objects.all()})


@admin_required
def admin_plan_create(request):
    form = PlanForm(request.POST or None)
    if form.is_valid():
        form.save()
        messages.success(request, 'Plan creado correctamente.')
        return redirect('admin-plans')
    return render(request, 'core/form.html', {'form': form, 'title': 'Crear plan', 'back_url': 'admin-plans'})


@admin_required
def admin_plan_edit(request, plan_id):
    plan = get_object_or_404(Plan, pk=plan_id)
    form = PlanForm(request.POST or None, instance=plan)
    if form.is_valid():
        form.save()
        messages.success(request, 'Plan actualizado correctamente.')
        return redirect('admin-plans')
    return render(request, 'core/form.html', {'form': form, 'title': 'Editar plan', 'back_url': 'admin-plans'})


@admin_required
def admin_plan_delete(request, plan_id):
    if request.method == 'POST':
        plan = get_object_or_404(Plan, pk=plan_id)
        try:
            plan.delete()
        except ProtectedError:
            messages.error(request, 'No se puede eliminar un plan que tiene membresías asociadas. Desactívalo en su lugar.')
        else:
            messages.success(request, 'Plan eliminado correctamente.')
    return redirect('admin-plans')


@admin_required
def admin_members(request):
    members = MemberProfile.objects.select_related('user').prefetch_related('memberships__plan', 'payments')
    return render(request, 'core/admin_members.html', {'members': members})


@admin_required
def admin_member_create(request):
    form = MemberCreateForm(request.POST or None)
    if form.is_valid():
        with transaction.atomic():
            user = form.save()
            user.save()
            member = MemberProfile.objects.create(user=user, rut=form.cleaned_data['rut'], phone=form.cleaned_data['phone'])
            plan = form.cleaned_data['plan']
            if plan:
                Membership.objects.create(member=member, plan=plan, start_date=form.cleaned_data['start_date'],
                                          end_date=form.cleaned_data['end_date'])
        messages.success(request, 'Socio creado y plan asignado correctamente.')
        return redirect('admin-members')
    return render(request, 'core/form.html', {'form': form, 'title': 'Crear socio', 'back_url': 'admin-members'})


@admin_required
def admin_membership_edit(request, member_id):
    member = get_object_or_404(MemberProfile, pk=member_id)
    membership = member.memberships.order_by('-end_date').first()
    form = MembershipForm(request.POST or None, instance=membership)
    if form.is_valid():
        saved = form.save(commit=False)
        saved.member = member
        saved.save()
        messages.success(request, 'Membresía actualizada correctamente.')
        return redirect('admin-members')
    return render(request, 'core/form.html', {'form': form, 'title': f'Asignar plan a {member}', 'back_url': 'admin-members'})


@admin_required
def admin_payment_create(request, member_id):
    member = get_object_or_404(MemberProfile, pk=member_id)
    form = PaymentForm(request.POST or None)
    form.fields['membership'].queryset = member.memberships.select_related('plan')
    if form.is_valid():
        payment = form.save(commit=False)
        payment.member = member
        payment.save()
        messages.success(request, 'Pago registrado correctamente.')
        return redirect('admin-members')
    return render(request, 'core/form.html', {'form': form, 'title': f'Registrar pago de {member}', 'back_url': 'admin-members'})


@login_required
def member_pay(request):
    if request.method != 'POST' or is_admin(request.user):
        raise PermissionDenied('Solo un administrador puede gestionar pagos y estados de membresía.')
    raise PermissionDenied('Solo un administrador puede gestionar pagos y estados de membresía.')


@admin_required
def admin_classes(request):
    classes = GroupClass.objects.prefetch_related('bookings').all()
    return render(request, 'core/admin_classes.html', {'classes': classes})


@admin_required
def admin_class_create(request):
    form = GroupClassForm(request.POST or None)
    if form.is_valid():
        form.save()
        messages.success(request, 'Clase creada correctamente.')
        return redirect('admin-classes')
    return render(request, 'core/form.html', {'form': form, 'title': 'Crear clase grupal', 'back_url': 'admin-classes'})


@admin_required
def admin_class_edit(request, class_id):
    group_class = get_object_or_404(GroupClass, pk=class_id)
    form = GroupClassForm(request.POST or None, instance=group_class)
    if form.is_valid():
        form.save()
        messages.success(request, 'Clase actualizada correctamente.')
        return redirect('admin-classes')
    return render(request, 'core/form.html', {'form': form, 'title': 'Editar clase grupal', 'back_url': 'admin-classes'})


@admin_required
def admin_class_delete(request, class_id):
    if request.method == 'POST':
        get_object_or_404(GroupClass, pk=class_id).delete()
        messages.success(request, 'Clase eliminada correctamente.')
    return redirect('admin-classes')


class BaseModelViewSet(viewsets.ModelViewSet):
    permission_classes = [permissions.IsAuthenticated]


class PlanViewSet(BaseModelViewSet):
    queryset = Plan.objects.all()
    serializer_class = PlanSerializer
    permission_classes = [AdminOrReadOnly]


class MemberProfileViewSet(BaseModelViewSet):
    queryset = MemberProfile.objects.select_related('user').order_by('user__username')
    serializer_class = MemberProfileSerializer
    permission_classes = [AdminWriteOwnRead]

    def get_queryset(self):
        queryset = super().get_queryset()
        return queryset if self.request.user.is_staff else queryset.filter(user=self.request.user)


class MembershipViewSet(BaseModelViewSet):
    queryset = Membership.objects.select_related('member', 'plan').all()
    serializer_class = MembershipSerializer
    permission_classes = [AdminWriteOwnRead]

    def get_queryset(self):
        queryset = super().get_queryset()
        return queryset if self.request.user.is_staff else queryset.filter(member__user=self.request.user)


class PaymentViewSet(BaseModelViewSet):
    queryset = Payment.objects.select_related('member', 'membership').all()
    serializer_class = PaymentSerializer
    permission_classes = [AdminWriteOwnRead]

    def get_queryset(self):
        queryset = super().get_queryset()
        return queryset if self.request.user.is_staff else queryset.filter(member__user=self.request.user)

    def perform_create(self, serializer):
        if not self.request.user.is_staff:
            raise PermissionDenied('Solo un administrador puede registrar pagos.')
        serializer.save()


class GroupClassViewSet(BaseModelViewSet):
    queryset = GroupClass.objects.all()
    serializer_class = GroupClassSerializer
    permission_classes = [AdminOrReadOnly]


class BookingViewSet(BaseModelViewSet):
    queryset = Booking.objects.select_related('member', 'group_class').all()
    serializer_class = BookingSerializer
    permission_classes = [BookingPermission]

    def get_queryset(self):
        queryset = super().get_queryset()
        return queryset if self.request.user.is_staff else queryset.filter(member__user=self.request.user)

    def perform_create(self, serializer):
        serializer.instance = book_class(self.request.user.member_profile, serializer.validated_data['group_class'])

    @decorators.action(detail=True, methods=['post'])
    def cancel(self, request, pk=None):
        booking = cancel_booking(self.get_object())
        return response.Response(self.get_serializer(booking).data)


class AccessLogViewSet(BaseModelViewSet):
    queryset = AccessLog.objects.select_related('member').all()
    serializer_class = AccessLogSerializer
    permission_classes = [AdminOnly]

    @decorators.action(detail=False, methods=['post'], url_path='scan-qr')
    def scan_qr(self, request):
        result = validate_qr_access(request.data.get('qr_token', ''))
        return response.Response({'allowed': result.allowed, 'reason': result.reason, 'member_id': result.member.id if result.member else None},
                                 status=status.HTTP_200_OK if result.allowed else status.HTTP_403_FORBIDDEN)


class FacilityEntryViewSet(BaseModelViewSet):
    queryset = FacilityEntry.objects.select_related('member').all()
    serializer_class = FacilityEntrySerializer
    permission_classes = [AdminOnly]

    def get_queryset(self):
        queryset = super().get_queryset()
        return queryset if self.request.user.is_staff else queryset.filter(member__user=self.request.user)

    @decorators.action(detail=False, methods=['get'])
    def occupancy(self, request):
        if not request.user.is_staff:
            raise PermissionDenied('Solo un administrador puede consultar el aforo global.')
        return response.Response({'current_occupancy': FacilityEntry.objects.filter(exited_at__isnull=True).count()})

    @decorators.action(detail=True, methods=['post'])
    def exit(self, request, pk=None):
        entry = self.get_object()
        register_exit(entry.member)
        return response.Response(self.get_serializer(self.get_object()).data)


class RegisterViewSet(viewsets.GenericViewSet):
    permission_classes = [permissions.AllowAny]
    serializer_class = UserRegistrationSerializer

    @decorators.action(detail=False, methods=['post'])
    def register(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        return response.Response({'id': user.id, 'username': user.username}, status=status.HTTP_201_CREATED)

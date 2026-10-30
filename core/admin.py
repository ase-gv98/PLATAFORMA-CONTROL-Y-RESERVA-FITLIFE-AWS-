from django import forms
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.admin import UserAdmin
from django.contrib.admin.models import LogEntry, CHANGE
from django.contrib.contenttypes.models import ContentType
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils.html import format_html
from .models import AccessLog, Booking, FacilityEntry, GroupClass, MemberProfile, Membership, Payment, Plan, validate_chilean_rut


User = get_user_model()

admin.site.site_header = 'FitLife'
admin.site.site_title = 'FitLife'
admin.site.index_title = 'Administración de FitLife'


class FitLifeUserAdmin(UserAdmin):
    list_display = ['username', 'first_name', 'last_name', 'email', 'is_staff', 'is_active', 'date_joined']
    list_filter = ['is_staff', 'is_superuser', 'is_active', 'groups']
    search_fields = ['username', 'first_name', 'last_name', 'email']


admin.site.unregister(User)
admin.site.register(User, FitLifeUserAdmin)


@admin.register(Plan)
class PlanAdmin(admin.ModelAdmin):
    list_display = ['name', 'monthly_price', 'active', 'membership_count']
    list_filter = ['active']
    search_fields = ['name']

    @admin.display(description='Membresías')
    def membership_count(self, obj):
        return obj.memberships.count()


@admin.register(MemberProfile)
class MemberProfileAdmin(admin.ModelAdmin):
    class MemberProfileForm(forms.ModelForm):
        class Meta:
            model = MemberProfile
            fields = '__all__'

        def clean_rut(self):
            rut = self.cleaned_data.get('rut')
            if not rut:
                return rut
            normalized = validate_chilean_rut(rut)
            if MemberProfile.objects.filter(rut=normalized).exclude(pk=self.instance.pk).exists():
                raise forms.ValidationError('Ya existe un socio con este RUT')
            return normalized

    form = MemberProfileForm
    list_display = ['user', 'rut', 'phone', 'qr_token']
    list_filter = ['user__is_active']
    search_fields = ['user__username', 'user__first_name', 'user__last_name', 'rut', 'qr_token']

    def has_module_permission(self, request):
        return request.user.is_staff

    def has_view_permission(self, request, obj=None):
        return request.user.is_staff

    def has_add_permission(self, request):
        return request.user.is_staff

    def has_change_permission(self, request, obj=None):
        return request.user.is_staff

    def has_delete_permission(self, request, obj=None):
        return request.user.is_staff


@admin.register(Membership)
class MembershipAdmin(admin.ModelAdmin):
    list_display = ['member', 'plan', 'estado', 'estado_badge', 'start_date', 'end_date']
    list_editable = ['estado']
    list_filter = ['estado', 'plan', 'start_date', 'end_date']
    search_fields = ['member__user__username', 'member__user__first_name', 'member__user__last_name',
                     'member__rut', 'member__user__email', 'plan__name']
    date_hierarchy = 'start_date'
    ordering = ['-start_date', '-id']
    actions = ['mark_active', 'mark_expired', 'mark_frozen']

    def has_module_permission(self, request):
        return request.user.is_staff

    def has_view_permission(self, request, obj=None):
        return request.user.is_staff

    def has_add_permission(self, request):
        return request.user.is_staff

    def has_change_permission(self, request, obj=None):
        return request.user.is_staff

    def has_delete_permission(self, request, obj=None):
        return request.user.is_staff

    @admin.display(description='Estado', ordering='estado')
    def estado_badge(self, obj):
        colors = {
            Membership.Status.ACTIVE: '#22C55E',
            Membership.Status.EXPIRED: '#DC2626',
            Membership.Status.FROZEN: '#38BDF8',
        }
        return format_html(
            '<span style="background:{};color:#fff;padding:.25rem .55rem;border-radius:999px;font-weight:700">{}</span>',
            colors[obj.estado], obj.get_estado_display(),
        )

    def save_model(self, request, obj, form, change):
        try:
            super().save_model(request, obj, form, change)
        except IntegrityError:
            self.message_user(request, 'El socio ya tiene una membresía activa.', level='error')
            return
        LogEntry.objects.log_action(
            user_id=request.user.pk,
            content_type_id=ContentType.objects.get_for_model(obj).pk,
            object_id=obj.pk,
            object_repr=str(obj),
            action_flag=CHANGE,
            change_message=f'Estado actualizado a {obj.get_estado_display()}.',
        )

    def _mark_state(self, request, queryset, state):
        changed = 0
        conflicts = 0
        for membership in queryset.select_related('member'):
            if state == Membership.Status.ACTIVE and Membership.objects.filter(
                member=membership.member, estado=Membership.Status.ACTIVE,
            ).exclude(pk=membership.pk).exists():
                conflicts += 1
                continue
            membership.estado = state
            try:
                membership.save()
            except IntegrityError:
                conflicts += 1
                continue
            LogEntry.objects.log_action(
                user_id=request.user.pk,
                content_type_id=ContentType.objects.get_for_model(membership).pk,
                object_id=membership.pk,
                object_repr=str(membership),
                action_flag=CHANGE,
                change_message=f'Acción masiva: estado actualizado a {membership.get_estado_display()}.',
            )
            changed += 1
        message = f'{changed} membresía(s) actualizada(s) a {dict(Membership.Status.choices).get(state)}.'
        if conflicts:
            message += f' {conflicts} no se activaron porque el socio ya tiene una membresía activa.'
        self.message_user(request, message, level='warning' if conflicts else 'success')

    @admin.action(description='Marcar como activa')
    def mark_active(self, request, queryset):
        self._mark_state(request, queryset, Membership.Status.ACTIVE)

    @admin.action(description='Marcar como vencida')
    def mark_expired(self, request, queryset):
        self._mark_state(request, queryset, Membership.Status.EXPIRED)

    @admin.action(description='Marcar como congelada')
    def mark_frozen(self, request, queryset):
        self._mark_state(request, queryset, Membership.Status.FROZEN)


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ['member', 'membership_plan', 'amount_clp', 'paid_at', 'payment_method', 'status']
    list_filter = ['status', 'membership__plan', 'payment_method', 'paid_at']
    search_fields = ['reference', 'member__user__username', 'member__user__first_name',
                     'member__user__last_name', 'member__rut', 'member__user__email']
    date_hierarchy = 'paid_at'
    ordering = ['-paid_at', '-id']

    def has_module_permission(self, request):
        return request.user.is_staff

    def has_view_permission(self, request, obj=None):
        return request.user.is_staff

    def has_add_permission(self, request):
        return request.user.is_staff

    def has_change_permission(self, request, obj=None):
        return request.user.is_staff

    def has_delete_permission(self, request, obj=None):
        return request.user.is_staff

    @admin.display(description='Plan')
    def membership_plan(self, obj):
        return obj.membership.plan

    @admin.display(description='Monto')
    def amount_clp(self, obj):
        return f'${int(obj.amount):,}'.replace(',', '.')


@admin.register(GroupClass)
class GroupClassAdmin(admin.ModelAdmin):
    list_display = ['name', 'instructor', 'starts_at', 'capacity', 'confirmed_count', 'available_spots']
    list_filter = ['active', 'instructor', 'starts_at']
    search_fields = ['name', 'instructor']


@admin.register(Booking)
class BookingAdmin(admin.ModelAdmin):
    list_display = ['member', 'group_class', 'status', 'created_at']
    list_filter = ['status', 'group_class']
    search_fields = ['member__user__username', 'group_class__name']


@admin.register(AccessLog)
class AccessLogAdmin(admin.ModelAdmin):
    list_display = ['member', 'scanned_at', 'allowed', 'reason']
    list_filter = ['allowed', 'scanned_at']
    search_fields = ['member__user__username', 'reason']


@admin.register(FacilityEntry)
class FacilityEntryAdmin(admin.ModelAdmin):
    list_display = ['member', 'entered_at', 'exited_at', 'is_inside']
    list_filter = ['entered_at', 'exited_at']
    search_fields = ['member__user__username']

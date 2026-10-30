from dataclasses import dataclass
from django.db import transaction
from django.utils import timezone
from .models import AccessLog, Booking, FacilityEntry, GroupClass, MemberProfile


@dataclass
class AccessResult:
    allowed: bool
    reason: str
    member: MemberProfile | None = None


@transaction.atomic
def validate_qr_access(qr_token):
    try:
        member = MemberProfile.objects.select_related('user').get(qr_token=qr_token)
    except MemberProfile.DoesNotExist:
        return AccessResult(False, 'Código QR no registrado.')
    membership = member.memberships.order_by('-end_date').first()
    if not membership or not membership.is_valid_for_access:
        result = AccessResult(False, 'Membresía vencida, congelada o inexistente.', member)
    else:
        result = AccessResult(True, 'Acceso autorizado.', member)
        FacilityEntry.objects.create(member=member)
    AccessLog.objects.create(member=member, allowed=result.allowed, reason=result.reason)
    return result


@transaction.atomic
def register_exit(member):
    entry = FacilityEntry.objects.select_for_update().filter(member=member, exited_at__isnull=True).first()
    if not entry:
        raise ValueError('El socio no tiene un ingreso activo.')
    entry.exited_at = timezone.now()
    entry.save(update_fields=['exited_at'])
    return entry


@transaction.atomic
def book_class(member, group_class):
    membership = member.memberships.filter(estado='ACTIVE').order_by('-end_date').first()
    if not membership or not membership.is_valid_for_access:
        raise ValueError('Necesitas una membresía activa para reservar clases.')
    group_class = GroupClass.objects.select_for_update().get(pk=group_class.pk)
    existing = Booking.objects.filter(member=member, group_class=group_class).first()
    if existing and existing.status != Booking.Status.CANCELLED:
        raise ValueError('El socio ya tiene una reserva para esta clase.')
    status = Booking.Status.CONFIRMED if group_class.available_spots > 0 else Booking.Status.WAITLIST
    if existing:
        existing.status = status
        existing.created_at = timezone.now()
        existing.save(update_fields=['status', 'created_at'])
        return existing
    return Booking.objects.create(member=member, group_class=group_class, status=status)


@transaction.atomic
def cancel_booking(booking):
    booking = Booking.objects.select_for_update().select_related('group_class').get(pk=booking.pk)
    if booking.status == Booking.Status.CANCELLED:
        return booking
    was_confirmed = booking.status == Booking.Status.CONFIRMED
    booking.status = Booking.Status.CANCELLED
    booking.save(update_fields=['status'])
    if was_confirmed:
        next_booking = Booking.objects.filter(
            group_class=booking.group_class, status=Booking.Status.WAITLIST
        ).order_by('created_at').first()
        if next_booking:
            next_booking.status = Booking.Status.CONFIRMED
            next_booking.save(update_fields=['status'])
    return booking

import secrets
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils import timezone


def normalize_rut(value):
    return value.replace('.', '').replace('-', '').replace(' ', '').upper()


def validate_chilean_rut(value):
    rut = normalize_rut(value)
    if len(rut) < 2 or not rut[:-1].isdigit() or rut[-1] not in '0123456789K':
        raise ValidationError('Ingresa un RUT chileno válido con dígito verificador.')
    body, verifier = rut[:-1], rut[-1]
    total = 0
    multiplier = 2
    for digit in reversed(body):
        total += int(digit) * multiplier
        multiplier = 2 if multiplier == 7 else multiplier + 1
    remainder = 11 - (total % 11)
    expected = '0' if remainder == 11 else 'K' if remainder == 10 else str(remainder)
    if verifier != expected:
        raise ValidationError('Ingresa un RUT chileno válido con dígito verificador.')
    return f'{body}-{verifier}'


class Plan(models.Model):
    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True)
    monthly_price = models.DecimalField(max_digits=10, decimal_places=2)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


def new_qr_token():
    return secrets.token_urlsafe(32)


class MemberProfile(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='member_profile')
    rut = models.CharField(max_length=12, unique=True, null=True, blank=True)
    phone = models.CharField(max_length=30, blank=True)
    qr_token = models.CharField(max_length=64, unique=True, default=new_qr_token)

    def clean(self):
        if self.rut:
            self.rut = validate_chilean_rut(self.rut)
            if MemberProfile.objects.filter(rut=self.rut).exclude(pk=self.pk).exists():
                raise ValidationError({'rut': 'Ya existe un socio con este RUT'})

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return self.user.get_full_name() or self.user.username


class Membership(models.Model):
    class Status(models.TextChoices):
        ACTIVE = 'ACTIVE', 'Activa'
        EXPIRED = 'EXPIRED', 'Vencida'
        FROZEN = 'FROZEN', 'Congelada'

    member = models.ForeignKey(MemberProfile, on_delete=models.CASCADE, related_name='memberships')
    plan = models.ForeignKey(Plan, on_delete=models.PROTECT, related_name='memberships')
    start_date = models.DateField()
    end_date = models.DateField()
    estado = models.CharField(max_length=10, choices=Status.choices, default=Status.ACTIVE)

    class Meta:
        ordering = ['-end_date']
        constraints = [
            models.UniqueConstraint(
                fields=['member'],
                condition=Q(estado='ACTIVE'),
                name='unique_active_membership_per_member',
            ),
        ]

    def clean(self):
        if self.end_date < self.start_date:
            raise ValidationError({'end_date': 'La fecha de término no puede ser anterior al inicio.'})
        if self.estado == self.Status.ACTIVE and Membership.objects.filter(
            member=self.member,
            estado=self.Status.ACTIVE,
        ).exclude(pk=self.pk).exists():
            raise ValidationError('El socio ya tiene una membresía activa.')

    def save(self, *args, **kwargs):
        if self.estado == self.Status.ACTIVE and self.end_date < timezone.localdate():
            self.estado = self.Status.EXPIRED
        self.full_clean()
        return super().save(*args, **kwargs)

    @property
    def is_valid_for_access(self):
        return self.estado == self.Status.ACTIVE and self.start_date <= timezone.localdate() <= self.end_date


class Payment(models.Model):
    class Status(models.TextChoices):
        PENDING = 'PENDING', 'Pendiente'
        PAID = 'PAID', 'Pagado'
        REFUNDED = 'REFUNDED', 'Reembolsado'

    class Method(models.TextChoices):
        CASH = 'CASH', 'Efectivo'
        CARD = 'CARD', 'Tarjeta'
        TRANSFER = 'TRANSFER', 'Transferencia'

    member = models.ForeignKey(MemberProfile, on_delete=models.PROTECT, related_name='payments')
    membership = models.ForeignKey(Membership, on_delete=models.PROTECT, related_name='payments')
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    paid_at = models.DateTimeField(default=timezone.now)
    reference = models.CharField(max_length=100, unique=True)
    payment_method = models.CharField(max_length=10, choices=Method.choices, default=Method.TRANSFER)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PAID)

    class Meta:
        ordering = ['-paid_at']

    def clean(self):
        if self.amount <= 0:
            raise ValidationError({'amount': 'El monto debe ser mayor que cero.'})
        if self.membership_id and self.member_id and self.membership.member_id != self.member_id:
            raise ValidationError({'membership': 'La membresía no pertenece al socio seleccionado.'})

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)


class GroupClass(models.Model):
    name = models.CharField(max_length=120)
    instructor = models.CharField(max_length=120)
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField()
    capacity = models.PositiveIntegerField()
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ['starts_at']

    def clean(self):
        if self.ends_at <= self.starts_at:
            raise ValidationError({'ends_at': 'El término debe ser posterior al inicio.'})
        if self.capacity < 1:
            raise ValidationError({'capacity': 'El cupo debe ser mayor a cero.'})

    @property
    def confirmed_count(self):
        return self.bookings.filter(status=Booking.Status.CONFIRMED).count()

    @property
    def available_spots(self):
        return max(0, self.capacity - self.confirmed_count)


class Booking(models.Model):
    class Status(models.TextChoices):
        CONFIRMED = 'CONFIRMED', 'Confirmada'
        WAITLIST = 'WAITLIST', 'Lista de espera'
        CANCELLED = 'CANCELLED', 'Cancelada'

    member = models.ForeignKey(MemberProfile, on_delete=models.CASCADE, related_name='bookings')
    group_class = models.ForeignKey(GroupClass, on_delete=models.CASCADE, related_name='bookings')
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.CONFIRMED)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['member', 'group_class'], name='unique_member_class_booking')]
        ordering = ['created_at']


class AccessLog(models.Model):
    member = models.ForeignKey(MemberProfile, on_delete=models.PROTECT, related_name='access_logs')
    scanned_at = models.DateTimeField(default=timezone.now)
    allowed = models.BooleanField()
    reason = models.CharField(max_length=255)

    class Meta:
        ordering = ['-scanned_at']


class FacilityEntry(models.Model):
    member = models.ForeignKey(MemberProfile, on_delete=models.PROTECT, related_name='facility_entries')
    entered_at = models.DateTimeField(default=timezone.now)
    exited_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-entered_at']

    @property
    def is_inside(self):
        return self.exited_at is None

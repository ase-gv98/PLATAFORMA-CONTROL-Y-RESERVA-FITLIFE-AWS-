from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers
from rest_framework.validators import UniqueValidator
from .models import (AccessLog, Booking, FacilityEntry, GroupClass, MemberProfile, Membership, Payment, Plan,
                     validate_chilean_rut)


class PlanSerializer(serializers.ModelSerializer):
    class Meta:
        model = Plan
        fields = '__all__'

    def validate_monthly_price(self, value):
        if value <= 0:
            raise serializers.ValidationError('El precio mensual debe ser mayor que cero.')
        return value


class MemberProfileSerializer(serializers.ModelSerializer):
    username = serializers.CharField(source='user.username', read_only=True)
    rut = serializers.CharField(
        required=False,
        allow_blank=True,
        validators=[
            UniqueValidator(queryset=MemberProfile.objects.all(), message='Ya existe un socio con este RUT'),
        ],
    )

    class Meta:
        model = MemberProfile
        fields = ['id', 'username', 'rut', 'phone', 'qr_token']

    def validate_rut(self, value):
        if not value:
            return None
        try:
            normalized = validate_chilean_rut(value)
            current_id = getattr(self.instance, 'pk', None)
            if MemberProfile.objects.filter(rut=normalized).exclude(pk=current_id).exists():
                raise serializers.ValidationError('Ya existe un socio con este RUT')
            return normalized
        except DjangoValidationError as error:
            raise serializers.ValidationError(error.messages)


class MembershipSerializer(serializers.ModelSerializer):
    class Meta:
        model = Membership
        fields = '__all__'

    def validate(self, attrs):
        member = attrs.get('member', getattr(self.instance, 'member', None))
        start_date = attrs.get('start_date', getattr(self.instance, 'start_date', None))
        end_date = attrs.get('end_date', getattr(self.instance, 'end_date', None))
        estado = attrs.get('estado', getattr(self.instance, 'estado', Membership.Status.ACTIVE))
        current_id = getattr(self.instance, 'pk', None)
        if start_date and end_date and end_date < start_date:
            raise serializers.ValidationError({
                'end_date': 'La fecha de término no puede ser anterior al inicio.',
            })
        if estado == Membership.Status.ACTIVE and member and Membership.objects.filter(
            member=member,
            estado=Membership.Status.ACTIVE,
        ).exclude(pk=current_id).exists():
            raise serializers.ValidationError({'member': 'El socio ya tiene una membresía activa.'})
        return attrs


class PaymentSerializer(serializers.ModelSerializer):
    receipt_url = serializers.SerializerMethodField()

    class Meta:
        model = Payment
        fields = '__all__'
        read_only_fields = ['receipt_url']

    def get_receipt_url(self, payment) -> str | None:
        if not payment.receipt:
            return None
        return payment.receipt.url

    def validate(self, attrs):
        amount = attrs.get('amount', getattr(self.instance, 'amount', None))
        if amount is not None and amount <= 0:
            raise serializers.ValidationError({'amount': 'El monto debe ser mayor que cero.'})
        member = attrs.get('member', getattr(self.instance, 'member', None))
        membership = attrs.get('membership', getattr(self.instance, 'membership', None))
        if member and membership and membership.member_id != member.id:
            raise serializers.ValidationError({'membership': 'La membresía no pertenece al socio seleccionado.'})
        return attrs


class GroupClassSerializer(serializers.ModelSerializer):
    confirmed_count = serializers.IntegerField(read_only=True)
    available_spots = serializers.IntegerField(read_only=True)

    class Meta:
        model = GroupClass
        fields = '__all__'
        read_only_fields = ['confirmed_count', 'available_spots']

    def validate(self, attrs):
        starts_at = attrs.get('starts_at', getattr(self.instance, 'starts_at', None))
        ends_at = attrs.get('ends_at', getattr(self.instance, 'ends_at', None))
        if starts_at and ends_at and ends_at <= starts_at:
            raise serializers.ValidationError({
                'ends_at': 'El término debe ser posterior al inicio.',
            })
        if attrs.get('capacity', getattr(self.instance, 'capacity', 1)) < 1:
            raise serializers.ValidationError({'capacity': 'El cupo debe ser mayor a cero.'})
        return attrs


class BookingSerializer(serializers.ModelSerializer):
    class Meta:
        model = Booking
        fields = '__all__'
        read_only_fields = ['status', 'member', 'created_at']


class AccessLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = AccessLog
        fields = '__all__'


class FacilityEntrySerializer(serializers.ModelSerializer):
    is_inside = serializers.BooleanField(read_only=True)

    class Meta:
        model = FacilityEntry
        fields = '__all__'


class UserRegistrationSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, min_length=8)
    phone = serializers.CharField(write_only=True, required=False, allow_blank=True)
    rut = serializers.CharField(write_only=True, required=False, allow_blank=True)

    class Meta:
        model = get_user_model()
        fields = ['username', 'password', 'first_name', 'last_name', 'email', 'phone', 'rut']

    def create(self, validated_data):
        phone = validated_data.pop('phone', '')
        rut = validated_data.pop('rut', '')
        user = get_user_model().objects.create_user(**validated_data)
        MemberProfile.objects.create(user=user, phone=phone, rut=rut or None)
        return user

    def validate_rut(self, value):
        try:
            normalized = validate_chilean_rut(value)
        except DjangoValidationError as error:
            raise serializers.ValidationError(error.messages)
        if MemberProfile.objects.filter(rut=normalized).exists():
            raise serializers.ValidationError('Ya existe un socio con este RUT')
        return normalized

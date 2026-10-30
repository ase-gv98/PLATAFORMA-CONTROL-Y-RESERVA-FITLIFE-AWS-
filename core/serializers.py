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
        try:
            try:
                normalized = validate_chilean_rut(value)
            except DjangoValidationError as error:
                raise serializers.ValidationError(error.messages)
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
        estado = attrs.get('estado', getattr(self.instance, 'estado', Membership.Status.ACTIVE))
        current_id = getattr(self.instance, 'pk', None)
        if estado == Membership.Status.ACTIVE and member and Membership.objects.filter(
            member=member,
            estado=Membership.Status.ACTIVE,
        ).exclude(pk=current_id).exists():
            raise serializers.ValidationError({'member': 'El socio ya tiene una membresía activa.'})
        return attrs


class PaymentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Payment
        fields = '__all__'

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

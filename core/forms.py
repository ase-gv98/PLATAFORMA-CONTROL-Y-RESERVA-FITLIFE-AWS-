from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth import password_validation
from .models import GroupClass, Membership, Payment, Plan, MemberProfile, validate_chilean_rut


class QRScanForm(forms.Form):
    qr_token = forms.CharField(
        label='RUT o código QR',
        max_length=64,
        widget=forms.TextInput(
            attrs={
                'class': 'form-control form-control-lg',
                'placeholder': 'Pega o escanea el código del socio',
                'autocomplete': 'off',
            }
        ),
    )


class BootstrapModelForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.setdefault('class', 'form-control')


class PlanForm(BootstrapModelForm):
    class Meta:
        model = Plan
        fields = ['name', 'description', 'monthly_price', 'active']
        widgets = {
            'description': forms.Textarea(attrs={'rows': 3}),
            'monthly_price': forms.NumberInput(attrs={'step': '0.01'}),
        }


class MembershipForm(BootstrapModelForm):
    class Meta:
        model = Membership
        fields = ['plan', 'start_date', 'end_date', 'estado']
        widgets = {
            'start_date': forms.DateInput(attrs={'type': 'date'}),
            'end_date': forms.DateInput(attrs={'type': 'date'}),
        }


class PaymentForm(BootstrapModelForm):
    class Meta:
        model = Payment
        fields = ['membership', 'amount', 'reference', 'payment_method', 'status']
        widgets = {'amount': forms.NumberInput(attrs={'step': '0.01'})}


class StaffPaymentForm(PaymentForm):
    class Meta(PaymentForm.Meta):
        fields = ['member', 'membership', 'amount', 'reference', 'payment_method', 'status']

    member = forms.ModelChoiceField(queryset=MemberProfile.objects.select_related('user').order_by('user__last_name', 'user__first_name'))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        member_id = self.data.get('member') or getattr(self.instance, 'member_id', None)
        if member_id:
            self.fields['membership'].queryset = Membership.objects.filter(member_id=member_id).select_related('plan')


class StaffMembershipForm(MembershipForm):
    class Meta(MembershipForm.Meta):
        fields = ['member', 'plan', 'start_date', 'end_date', 'estado']

    member = forms.ModelChoiceField(queryset=MemberProfile.objects.select_related('user').order_by('user__last_name', 'user__first_name'))


class GroupClassForm(BootstrapModelForm):
    class Meta:
        model = GroupClass
        fields = ['name', 'instructor', 'starts_at', 'ends_at', 'capacity', 'active']
        widgets = {
            'starts_at': forms.DateTimeInput(attrs={'type': 'datetime-local'}),
            'ends_at': forms.DateTimeInput(attrs={'type': 'datetime-local'}),
        }


class MemberCreateForm(forms.Form):
    username = forms.CharField(max_length=150)
    email = forms.EmailField(required=False)
    first_name = forms.CharField(max_length=150, required=False)
    last_name = forms.CharField(max_length=150, required=False)
    rut = forms.CharField(max_length=12, required=False)
    phone = forms.CharField(max_length=30, required=False)
    password1 = forms.CharField(label='Contraseña', widget=forms.PasswordInput)
    password2 = forms.CharField(label='Confirmar contraseña', widget=forms.PasswordInput)
    plan = forms.ModelChoiceField(queryset=Plan.objects.filter(active=True), required=False)
    start_date = forms.DateField(widget=forms.DateInput(attrs={'type': 'date'}))
    end_date = forms.DateField(widget=forms.DateInput(attrs={'type': 'date'}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.setdefault('class', 'form-control')

    def clean_username(self):
        username = self.cleaned_data['username']
        if get_user_model().objects.filter(username=username).exists():
            raise forms.ValidationError('Ese nombre de usuario ya está registrado.')
        return username

    def clean_rut(self):
        rut = self.cleaned_data.get('rut')
        if not rut:
            return rut
        normalized = validate_chilean_rut(rut)
        if MemberProfile.objects.filter(rut=normalized).exists():
            raise forms.ValidationError('Ya existe un socio con este RUT')
        return normalized

    def clean(self):
        cleaned_data = super().clean()
        if cleaned_data.get('password1') != cleaned_data.get('password2'):
            raise forms.ValidationError('Las contraseñas no coinciden.')
        if cleaned_data.get('password1'):
            password_validation.validate_password(cleaned_data['password1'])
        return cleaned_data

    def save(self):
        return get_user_model().objects.create_user(
            username=self.cleaned_data['username'],
            password=self.cleaned_data['password1'],
            email=self.cleaned_data['email'],
            first_name=self.cleaned_data['first_name'],
            last_name=self.cleaned_data['last_name'],
        )

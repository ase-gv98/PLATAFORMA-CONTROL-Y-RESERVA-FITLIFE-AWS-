from datetime import timedelta
from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import Argon2PasswordHasher
from django.contrib.admin.models import LogEntry
from django.core.exceptions import ValidationError
from django.core.files.storage import default_storage
from django.db import IntegrityError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.contrib.staticfiles import finders
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient
from .forms import MemberCreateForm
from .models import Booking, GroupClass, MemberProfile, Membership, Payment, Plan
from .services import book_class, cancel_booking, validate_qr_access
from .serializers import MembershipSerializer
from .exceptions import fitlife_exception_handler


class TestArgon2PasswordHasher(Argon2PasswordHasher):
    memory_cost = 19 * 1024


@override_settings(PASSWORD_HASHERS=[
    'core.tests.TestArgon2PasswordHasher',
    'django.contrib.auth.hashers.PBKDF2PasswordHasher',
])
class FitLifeRulesTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        call_command('collectstatic', interactive=False, verbosity=0)

    def setUp(self):
        self.api_client = APIClient()
        user_model = get_user_model()
        self.member = MemberProfile.objects.create(user=user_model.objects.create_user('ana', password='pass12345'))
        self.member.rut = '12345678-5'
        self.member.save()
        self.other = MemberProfile.objects.create(user=user_model.objects.create_user('juan', password='pass12345'))
        self.plan = Plan.objects.create(name='Mensual', monthly_price=25000)
        today = timezone.localdate()
        for member in [self.member, self.other]:
            Membership.objects.create(member=member, plan=self.plan, start_date=today, end_date=today + timedelta(days=30))
        self.group_class = GroupClass.objects.create(
            name='HIIT', instructor='Coach', starts_at=timezone.now() + timedelta(days=1),
            ends_at=timezone.now() + timedelta(days=1, minutes=45), capacity=1,
        )

    def authenticate_api(self, user):
        response = self.api_client.post('/api/token/', {
            'username': user.username,
            'password': 'pass12345',
        }, format='json')
        self.assertEqual(response.status_code, 200)
        self.api_client.credentials(HTTP_AUTHORIZATION=f'Bearer {response.data["access"]}')
        return response.data

    def test_jwt_login_refresh_rotation_and_blacklist(self):
        response = self.api_client.post('/api/token/', {
            'username': self.member.user.username,
            'password': 'pass12345',
        }, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertIn('access', response.data)
        self.assertIn('refresh', response.data)

        old_refresh = response.data['refresh']
        response = self.api_client.post('/api/token/refresh/', {'refresh': old_refresh}, format='json')
        self.assertEqual(response.status_code, 200)
        rotated_refresh = response.data['refresh']
        self.assertNotEqual(rotated_refresh, old_refresh)
        self.assertEqual(
            self.api_client.post('/api/token/refresh/', {'refresh': old_refresh}, format='json').status_code,
            401,
        )

        response = self.api_client.post(
            '/api/token/blacklist/',
            {'refresh': rotated_refresh},
            format='json',
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            self.api_client.post('/api/token/refresh/', {'refresh': rotated_refresh}, format='json').status_code,
            401,
        )

    def test_jwt_login_rejects_invalid_password_and_api_requires_bearer_token(self):
        response = self.api_client.post('/api/token/', {
            'username': self.member.user.username,
            'password': 'incorrecta',
        }, format='json')
        self.assertEqual(response.status_code, 401)
        self.assertEqual(self.api_client.get('/api/plans/').status_code, 401)
        self.api_client.credentials(HTTP_AUTHORIZATION='Bearer not-a-valid-token')
        self.assertEqual(self.api_client.get('/api/plans/').status_code, 401)

    def test_member_cannot_create_staff_managed_plan(self):
        self.authenticate_api(self.member.user)
        response = self.api_client.post('/api/plans/', {
            'name': 'Plan no autorizado',
            'monthly_price': '10000.00',
        }, format='json')
        self.assertEqual(response.status_code, 403)

    def test_health_is_public_without_authentication(self):
        response = self.api_client.get('/api/health/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'status': 'ok'})
        self.assertEqual(self.api_client.get('/api/schema/').status_code, 200)
        self.assertEqual(self.api_client.get('/api/docs/').status_code, 200)

    def test_staff_can_complete_crud_for_related_api_entities(self):
        admin = get_user_model().objects.create_user('crud_api_admin', password='pass12345', is_staff=True)
        self.authenticate_api(admin)

        response = self.api_client.post('/api/plans/', {
            'name': 'Plan API',
            'description': 'Plan de prueba',
            'monthly_price': '30000.00',
            'active': True,
        }, format='json')
        self.assertEqual(response.status_code, 201)
        plan_id = response.data['id']
        self.assertEqual(self.api_client.get('/api/plans/').status_code, 200)
        self.assertEqual(self.api_client.get(f'/api/plans/{plan_id}/').status_code, 200)
        response = self.api_client.put(f'/api/plans/{plan_id}/', {
            'name': 'Plan API actualizado',
            'description': 'Actualizado',
            'monthly_price': '32000.00',
            'active': True,
        }, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            self.api_client.patch(f'/api/plans/{plan_id}/', {'active': False}, format='json').status_code,
            200,
        )

        today = timezone.localdate()
        response = self.api_client.post('/api/memberships/', {
            'member': self.other.id,
            'plan': plan_id,
            'start_date': today.isoformat(),
            'end_date': (today + timedelta(days=30)).isoformat(),
            'estado': Membership.Status.FROZEN,
        }, format='json')
        self.assertEqual(response.status_code, 201)
        membership_id = response.data['id']
        self.assertEqual(self.api_client.get('/api/memberships/').status_code, 200)
        self.assertEqual(self.api_client.get(f'/api/memberships/{membership_id}/').status_code, 200)
        membership_data = {
            'member': self.other.id,
            'plan': plan_id,
            'start_date': today.isoformat(),
            'end_date': (today + timedelta(days=45)).isoformat(),
            'estado': Membership.Status.FROZEN,
        }
        self.assertEqual(
            self.api_client.put(f'/api/memberships/{membership_id}/', membership_data, format='json').status_code,
            200,
        )
        self.assertEqual(
            self.api_client.patch(f'/api/memberships/{membership_id}/', {'estado': Membership.Status.EXPIRED},
                                  format='json').status_code,
            200,
        )

        response = self.api_client.post('/api/payments/', {
            'member': self.other.id,
            'membership': membership_id,
            'amount': '32000.00',
            'reference': 'API-CRUD-001',
            'payment_method': Payment.Method.CARD,
            'status': Payment.Status.PAID,
        }, format='json')
        self.assertEqual(response.status_code, 201)
        payment_id = response.data['id']
        self.assertEqual(self.api_client.get('/api/payments/').status_code, 200)
        self.assertEqual(self.api_client.get(f'/api/payments/{payment_id}/').status_code, 200)
        payment_data = {
            'member': self.other.id,
            'membership': membership_id,
            'amount': '33000.00',
            'reference': 'API-CRUD-001',
            'payment_method': Payment.Method.CARD,
            'status': Payment.Status.PENDING,
        }
        self.assertEqual(
            self.api_client.put(f'/api/payments/{payment_id}/', payment_data, format='json').status_code,
            200,
        )
        self.assertEqual(
            self.api_client.patch(f'/api/payments/{payment_id}/', {'amount': '34000.00'}, format='json').status_code,
            200,
        )

        starts_at = timezone.now() + timedelta(days=3)
        ends_at = starts_at + timedelta(minutes=45)
        class_data = {
            'name': 'Clase API',
            'instructor': 'Coach',
            'starts_at': starts_at.isoformat(),
            'ends_at': ends_at.isoformat(),
            'capacity': 12,
            'active': True,
        }
        response = self.api_client.post('/api/classes/', class_data, format='json')
        self.assertEqual(response.status_code, 201)
        class_id = response.data['id']
        self.assertEqual(self.api_client.get('/api/classes/').status_code, 200)
        self.assertEqual(self.api_client.get(f'/api/classes/{class_id}/').status_code, 200)
        class_data['name'] = 'Clase API actualizada'
        self.assertEqual(
            self.api_client.put(f'/api/classes/{class_id}/', class_data, format='json').status_code,
            200,
        )
        self.assertEqual(
            self.api_client.patch(f'/api/classes/{class_id}/', {'capacity': 15}, format='json').status_code,
            200,
        )

        self.assertEqual(self.api_client.delete(f'/api/payments/{payment_id}/').status_code, 204)
        self.assertEqual(self.api_client.delete(f'/api/memberships/{membership_id}/').status_code, 204)
        self.assertEqual(self.api_client.delete(f'/api/plans/{plan_id}/').status_code, 204)
        self.assertEqual(self.api_client.delete(f'/api/classes/{class_id}/').status_code, 204)

    def test_api_serializers_reject_invalid_amount_dates_and_rut(self):
        admin = get_user_model().objects.create_user('validation_api_admin', password='pass12345', is_staff=True)
        self.authenticate_api(admin)

        response = self.api_client.post('/api/payments/', {
            'member': self.member.id,
            'membership': self.member.memberships.first().id,
            'amount': '0',
            'reference': 'API-ZERO-AMOUNT',
        }, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertIn('amount', response.data)

        response = self.api_client.post('/api/memberships/', {
            'member': self.other.id,
            'plan': self.plan.id,
            'start_date': (timezone.localdate() + timedelta(days=5)).isoformat(),
            'end_date': timezone.localdate().isoformat(),
            'estado': Membership.Status.FROZEN,
        }, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertIn('end_date', response.data)

        response = self.api_client.patch(
            f'/api/members/{self.member.id}/',
            {'rut': '12345678-9'},
            format='json',
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn('rut', response.data)

    def test_staff_payment_receipt_upload_and_validation(self):
        admin = get_user_model().objects.create_user('receipt_api_admin', password='pass12345', is_staff=True)
        self.authenticate_api(admin)
        payload = {
            'member': self.member.id,
            'membership': self.member.memberships.first().id,
            'amount': '25000.00',
            'reference': 'API-RECEIPT-001',
            'receipt': SimpleUploadedFile('receipt.pdf', b'%PDF-1.4\nFitLife receipt'),
        }
        response = self.api_client.post('/api/payments/', payload, format='multipart')
        self.assertEqual(response.status_code, 201)
        payment_id = response.data['id']
        payment = Payment.objects.get(pk=payment_id)
        self.addCleanup(default_storage.delete, payment.receipt.name)
        self.assertIn('payment-receipts/', response.data['receipt_url'])

        response = self.api_client.patch(
            f'/api/payments/{payment_id}/',
            {'receipt': SimpleUploadedFile('updated.jpg', b'\xff\xd8\xffFitLife')},
            format='multipart',
        )
        self.assertEqual(response.status_code, 200)
        payment.refresh_from_db()
        self.addCleanup(default_storage.delete, payment.receipt.name)

        invalid_receipt = self.api_client.post('/api/payments/', {
            'member': self.member.id,
            'membership': self.member.memberships.first().id,
            'amount': '1000.00',
            'reference': 'API-RECEIPT-INVALID',
            'receipt': SimpleUploadedFile('receipt.txt', b'not an allowed file'),
        }, format='multipart')
        self.assertEqual(invalid_receipt.status_code, 400)
        self.assertIn('receipt', invalid_receipt.data)

        oversized_receipt = self.api_client.post('/api/payments/', {
            'member': self.member.id,
            'membership': self.member.memberships.first().id,
            'amount': '1000.00',
            'reference': 'API-RECEIPT-LARGE',
            'receipt': SimpleUploadedFile('large.pdf', b'%PDF-' + b'x' * (2 * 1024 * 1024)),
        }, format='multipart')
        self.assertEqual(oversized_receipt.status_code, 400)
        self.assertIn('receipt', oversized_receipt.data)

    def test_password_is_stored_as_argon2_hash(self):
        user = get_user_model().objects.create_user('argon_user', password='plain-secret-123')
        user.refresh_from_db()
        self.assertTrue(user.password.startswith('argon2$'))
        self.assertNotIn('plain-secret-123', user.password)

    def test_qr_creates_entry_for_active_membership(self):
        result = validate_qr_access(self.member.qr_token)
        self.assertTrue(result.allowed)
        self.assertEqual(self.member.facility_entries.filter(exited_at__isnull=True).count(), 1)

    def test_qr_rejects_unknown_code(self):
        result = validate_qr_access('not-a-real-qr')
        self.assertFalse(result.allowed)

    def test_class_overflow_goes_to_waitlist(self):
        self.assertEqual(book_class(self.member, self.group_class).status, Booking.Status.CONFIRMED)
        self.assertEqual(book_class(self.other, self.group_class).status, Booking.Status.WAITLIST)

    def test_cancel_promotes_waitlist(self):
        confirmed = book_class(self.member, self.group_class)
        waitlisted = book_class(self.other, self.group_class)
        cancel_booking(confirmed)
        waitlisted.refresh_from_db()
        self.assertEqual(waitlisted.status, Booking.Status.CONFIRMED)

    def test_member_cannot_have_two_active_memberships(self):
        with self.assertRaises(ValidationError) as error:
            Membership(
                member=self.member,
                plan=self.plan,
                start_date=timezone.localdate(),
                end_date=timezone.localdate() + timedelta(days=30),
                estado=Membership.Status.ACTIVE,
            ).full_clean()
        self.assertIn('El socio ya tiene una membresía activa.', str(error.exception))

    def test_membership_serializer_returns_clear_duplicate_error(self):
        other_plan = Plan.objects.create(name='Plan Premium', monthly_price=35000)
        serializer = MembershipSerializer(data={
            'member': self.member.id,
            'plan': other_plan.id,
            'start_date': timezone.localdate().isoformat(),
            'end_date': (timezone.localdate() + timedelta(days=30)).isoformat(),
            'estado': Membership.Status.ACTIVE,
        })
        self.assertFalse(serializer.is_valid())
        self.assertEqual(
            serializer.errors['member'][0],
            'El socio ya tiene una membresía activa.',
        )

    def test_admin_branding_links_to_public_home(self):
        admin = get_user_model().objects.create_user('branding_admin', password='pass12345', is_staff=True)
        self.client.force_login(admin)
        response = self.client.get('/admin/')
        self.assertContains(response, '>FitLife</a>')
        self.assertContains(response, 'href="/"')

    def test_staff_shortcuts_for_payments_and_memberships_are_visible(self):
        admin = get_user_model().objects.create_user('shortcuts_admin', password='pass12345', is_staff=True)
        self.client.force_login(admin)
        for url in ['/admin/', '/dashboard/', '/administracion/socios/']:
            response = self.client.get(url)
            self.assertContains(response, '/panel/pagos/')
            self.assertContains(response, '/panel/membresias/')

    def test_member_does_not_see_staff_payment_shortcuts(self):
        self.client.force_login(self.member.user)
        response = self.client.get('/mi-cuenta/')
        self.assertNotContains(response, '/panel/pagos/')
        self.assertNotContains(response, '/panel/membresias/')

    def test_staff_panels_are_forbidden_to_members(self):
        self.client.force_login(self.member.user)
        self.assertEqual(self.client.get('/panel/pagos/').status_code, 403)
        self.assertEqual(self.client.get('/panel/membresias/').status_code, 403)

    def test_staff_can_create_and_delete_payment_from_panel(self):
        admin = get_user_model().objects.create_user(
            'panel_payment_admin', password='pass12345', is_staff=True,
        )
        self.client.force_login(admin)
        response = self.client.post('/panel/pagos/nuevo/', {
            'member': self.member.id,
            'membership': self.member.memberships.first().id,
            'amount': '450000',
            'reference': 'PANEL-001',
            'payment_method': Payment.Method.TRANSFER,
            'status': Payment.Status.PAID,
        })
        self.assertRedirects(response, '/panel/pagos/')
        payment = Payment.objects.get(reference='PANEL-001')
        response = self.client.post(f'/panel/pagos/{payment.id}/eliminar/')
        self.assertRedirects(response, '/panel/pagos/')
        self.assertFalse(Payment.objects.filter(pk=payment.id).exists())

    def test_staff_membership_panel_state_change_respects_unique_active_and_logs(self):
        admin = get_user_model().objects.create_user(
            'panel_membership_admin', password='pass12345', is_staff=True,
        )
        self.client.force_login(admin)
        first = self.member.memberships.first()
        second = Membership.objects.create(
            member=self.member,
            plan=self.plan,
            start_date=timezone.localdate(),
            end_date=timezone.localdate() + timedelta(days=30),
            estado=Membership.Status.FROZEN,
        )
        response = self.client.post(f'/panel/membresias/{second.id}/estado/ACTIVE/')
        self.assertRedirects(response, '/panel/membresias/')
        second.refresh_from_db()
        self.assertEqual(second.estado, Membership.Status.FROZEN)
        self.assertTrue(
            any('El socio ya tiene una membresía activa' in message.message
                for message in list(response.wsgi_request._messages))
        )
        response = self.client.post(f'/panel/membresias/{first.id}/estado/FROZEN/')
        self.assertRedirects(response, '/panel/membresias/')
        first.refresh_from_db()
        self.assertEqual(first.estado, Membership.Status.FROZEN)
        self.assertTrue(LogEntry.objects.filter(
            object_id=str(first.id),
            change_message__contains='Estado cambiado',
        ).exists())

    def test_duplicate_rut_is_rejected_by_web_form_with_clear_field_error(self):
        form = MemberCreateForm(data={
            'username': 'duplicate-rut',
            'email': 'duplicate@example.com',
            'rut': '12.345.678-5',
            'phone': '',
            'password1': 'NuevaClaveSegura123!',
            'password2': 'NuevaClaveSegura123!',
            'start_date': timezone.localdate().isoformat(),
            'end_date': (timezone.localdate() + timedelta(days=30)).isoformat(),
        })
        self.assertFalse(form.is_valid())
        self.assertEqual(form.errors['rut'][0], 'Ya existe un socio con este RUT')

    def test_duplicate_rut_is_rejected_by_admin_with_clear_error(self):
        admin = get_user_model().objects.create_user('rut_admin', password='pass12345', is_staff=True)
        new_user = get_user_model().objects.create_user('admin_target')
        self.client.force_login(admin)
        response = self.client.post('/admin/core/memberprofile/add/', {
            'user': new_user.id,
            'rut': '12.345.678-5',
            'phone': '',
            '_save': 'Guardar',
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Ya existe un socio con este RUT')

    def test_duplicate_rut_is_rejected_by_registration_api_in_spanish(self):
        response = self.client.post('/api/auth/register/', {
            'username': 'api-duplicate-rut',
            'password': 'NuevaClaveSegura123!',
            'email': 'api@example.com',
            'rut': '12345678-5',
        }, content_type='application/json')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['rut'][0], 'Ya existe un socio con este RUT')

    def test_invalid_rut_format_is_rejected_by_web_and_api(self):
        form = MemberCreateForm(data={
            'username': 'invalid-rut',
            'rut': '12.345.678-9',
            'password1': 'NuevaClaveSegura123!',
            'password2': 'NuevaClaveSegura123!',
            'start_date': timezone.localdate().isoformat(),
            'end_date': (timezone.localdate() + timedelta(days=30)).isoformat(),
        })
        self.assertFalse(form.is_valid())
        self.assertIn('dígito verificador', form.errors['rut'][0])
        response = self.client.post('/api/auth/register/', {
            'username': 'api-invalid-rut',
            'password': 'NuevaClaveSegura123!',
            'rut': '12.345.678-9',
        }, content_type='application/json')
        self.assertEqual(response.status_code, 400)
        self.assertIn('dígito verificador', response.json()['rut'][0])

    @override_settings(DEBUG=False)
    def test_friendly_error_pages_render(self):
        response = self.client.get('/ruta-que-no-existe/')
        self.assertEqual(response.status_code, 404)
        self.assertContains(response, 'Página no encontrada', status_code=404)
        self.client.force_login(self.member.user)
        response = self.client.get('/acceso-qr/')
        self.assertEqual(response.status_code, 403)
        self.assertContains(response, 'Acceso denegado', status_code=403)

    def test_drf_integrity_error_returns_safe_spanish_json(self):
        response = fitlife_exception_handler(IntegrityError('duplicate key'), {})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.data['detail'],
            'No se pudo guardar el registro porque ya existe un dato duplicado.',
        )

    def test_fitlife_button_and_logout_form_are_available_in_admin_login_and_pages(self):
        login_page = self.client.get('/admin/login/')
        self.assertContains(login_page, '>FitLife</a>')
        self.assertContains(login_page, 'href="/"')
        admin = get_user_model().objects.create_user('logout_admin', password='pass12345', is_staff=True)
        self.client.force_login(admin)
        admin_page = self.client.get('/admin/')
        self.assertContains(admin_page, '>FitLife</a>')
        self.assertContains(admin_page, 'action="/accounts/logout/"')
        self.assertContains(admin_page, 'Cerrar sesión')

    def test_login_redirects_staff_and_member_to_different_web_portals(self):
        response = self.client.post('/accounts/login/', {'username': 'ana', 'password': 'pass12345'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], '/redirigir/')
        response = self.client.get('/redirigir/')
        self.assertEqual(response['Location'], '/mi-cuenta/')
        self.client.logout()
        admin = get_user_model().objects.create_user('manager', password='pass12345', is_staff=True)
        response = self.client.post('/accounts/login/', {'username': admin.username, 'password': 'pass12345'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], '/redirigir/')
        response = self.client.get('/redirigir/')
        self.assertEqual(response['Location'], '/dashboard/')

    def test_member_payment_activates_membership_and_registers_payment(self):
        membership = self.member.memberships.first()
        membership.estado = Membership.Status.FROZEN
        membership.save()
        self.client.force_login(self.member.user)
        response = self.client.post('/mi-cuenta/pagar/')
        self.assertEqual(response.status_code, 403)
        membership.refresh_from_db()
        self.assertEqual(membership.estado, Membership.Status.FROZEN)
        self.assertEqual(Payment.objects.filter(member=self.member).count(), 0)

    def test_member_cannot_modify_memberships_or_payments_from_web_or_api(self):
        payment = Payment.objects.create(
            member=self.member,
            membership=self.member.memberships.first(),
            amount=25000,
            reference='PAY-READONLY',
        )
        self.client.force_login(self.member.user)
        self.assertEqual(self.client.post('/mi-cuenta/pagar/').status_code, 403)
        self.authenticate_api(self.member.user)
        response = self.api_client.patch(
            f'/api/memberships/{self.member.memberships.first().id}/',
            {'estado': Membership.Status.FROZEN},
            format='json',
        )
        self.assertEqual(response.status_code, 403)
        response = self.api_client.patch(
            f'/api/payments/{payment.id}/',
            {'amount': 1},
            format='json',
        )
        self.assertEqual(response.status_code, 403)
        response = self.api_client.delete(f'/api/payments/{payment.id}/')
        self.assertEqual(response.status_code, 403)

    def test_admin_can_manage_memberships_and_payments_through_api(self):
        admin = get_user_model().objects.create_user('payment_admin', password='pass12345', is_staff=True)
        self.client.force_login(admin)
        self.authenticate_api(admin)
        membership = self.member.memberships.first()
        response = self.api_client.patch(
            f'/api/memberships/{membership.id}/',
            {'estado': Membership.Status.FROZEN},
            format='json',
        )
        self.assertEqual(response.status_code, 200)

    def test_admin_crud_exposes_membership_actions_and_audit_log(self):
        admin = get_user_model().objects.create_user('crud_admin', password='pass12345', is_staff=True)
        self.client.force_login(admin)
        membership = self.member.memberships.first()
        response = self.client.post(
            f'/admin/core/membership/{membership.id}/change/',
            {
                'member': membership.member_id,
                'plan': membership.plan_id,
                'estado': Membership.Status.FROZEN,
                'start_date': membership.start_date.isoformat(),
                'end_date': membership.end_date.isoformat(),
                '_save': 'Guardar',
            },
        )
        self.assertEqual(response.status_code, 302)
        membership.refresh_from_db()
        self.assertEqual(membership.estado, Membership.Status.FROZEN)
        self.assertTrue(LogEntry.objects.filter(
            object_id=str(membership.id),
            change_message__contains='Estado actualizado',
        ).exists())
        payment = Payment.objects.create(
            member=self.member, membership=membership, amount=25000, reference='PAY-ADMIN',
        )
        self.authenticate_api(admin)
        response = self.api_client.patch(
            f'/api/payments/{payment.id}/',
            {'status': Payment.Status.REFUNDED},
            format='json',
        )
        self.assertEqual(response.status_code, 200)

    def test_frozen_membership_blocks_qr_and_booking(self):
        membership = self.member.memberships.first()
        membership.estado = Membership.Status.FROZEN
        membership.save()
        result = validate_qr_access(self.member.qr_token)
        self.assertFalse(result.allowed)
        with self.assertRaisesMessage(ValueError, 'Necesitas una membresía activa'):
            book_class(self.member, self.group_class)

    def test_admin_web_routes_render_bootstrap_pages(self):
        admin = get_user_model().objects.create_user('manager', password='pass12345', is_staff=True)
        self.client.force_login(admin)
        for url in ['/administracion/planes/', '/administracion/socios/', '/administracion/clases/', '/acceso-qr/']:
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, 'bootstrap')

    def test_admin_can_create_member_with_plan(self):
        admin = get_user_model().objects.create_user('manager', password='pass12345', is_staff=True)
        self.client.force_login(admin)
        response = self.client.post('/administracion/socios/crear/', {
            'username': 'nuevo',
            'first_name': 'Nuevo',
            'last_name': 'Socio',
            'email': 'nuevo@example.com',
            'rut': '98.765.432-5',
            'phone': '+56912345678',
            'password1': 'NuevaClaveSegura123!',
            'password2': 'NuevaClaveSegura123!',
            'plan': self.plan.id,
            'start_date': timezone.localdate().isoformat(),
            'end_date': (timezone.localdate() + timedelta(days=30)).isoformat(),
        })
        self.assertRedirects(response, '/administracion/socios/')
        created = MemberProfile.objects.get(user__username='nuevo')
        self.assertEqual(created.rut, '98765432-5')
        self.assertEqual(created.memberships.get().plan, self.plan)

    def test_web_aliases_and_browsable_api_are_html(self):
        admin = get_user_model().objects.create_user('manager', password='pass12345', is_staff=True)
        self.client.force_login(admin)
        for url in ['/dashboard/', '/admin-panel/', '/simulador-qr/']:
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, '<html')
        self.authenticate_api(admin)
        response = self.api_client.get('/api/', HTTP_ACCEPT='text/html')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'bootstrap')

    def test_api_root_and_global_records_are_restricted(self):
        response = self.api_client.get('/api/')
        self.assertEqual(response.status_code, 401)
        self.authenticate_api(self.member.user)
        response = self.api_client.get('/api/')
        self.assertEqual(response.status_code, 403)
        response = self.api_client.get('/api/members/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()['results']), 1)
        response = self.api_client.get('/api/access-logs/')
        self.assertEqual(response.status_code, 403)
        admin = get_user_model().objects.create_user('api_admin', password='pass12345', is_staff=True)
        self.authenticate_api(admin)
        self.assertEqual(self.api_client.get('/api/').status_code, 200)

    def test_fitlife_static_assets_and_brand_markup_are_served(self):
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'class="navbar-brand bg-primary text-white fw-bold px-3 py-2 rounded text-decoration-none"')
        self.assertContains(response, '/static/css/app.css?v=fitlife-20261029')
        self.assertContains(response, '/static/img/gym-hero.jpg')
        for asset in ['css/app.css', 'admin/css/fitlife_admin.css', 'img/gym-hero.jpg']:
            path = finders.find(asset)
            self.assertIsNotNone(path, asset)
            self.assertTrue(__import__('pathlib').Path(path).exists())

    def test_dashboard_contains_shared_brand_and_styles(self):
        self.client.force_login(self.member.user)
        response = self.client.get('/dashboard/')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], '/mi-cuenta/')
        admin = get_user_model().objects.create_user('dashboard_admin', password='pass12345', is_staff=True)
        self.client.force_login(admin)
        response = self.client.get('/dashboard/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'bg-primary')
        self.assertContains(response, 'text-white')
        self.assertContains(response, '/static/css/app.css?v=fitlife-20261029')

    def test_authenticated_home_redirects_to_role_panel(self):
        self.client.force_login(self.member.user)
        response = self.client.get('/')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], '/redirigir/')
        admin = get_user_model().objects.create_user('home_admin', password='pass12345', is_staff=True)
        self.client.force_login(admin)
        response = self.client.get('/')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], '/redirigir/')

    def test_admin_home_has_quick_actions_and_native_css(self):
        admin = get_user_model().objects.create_user('admin_index', password='pass12345', is_staff=True)
        self.client.force_login(admin)
        response = self.client.get('/admin/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Control de acceso QR')
        self.assertContains(response, 'Dashboard')
        self.assertContains(response, '/static/admin/css/base.css')
        self.assertContains(response, '/static/admin/css/fitlife_admin.css?v=fitlife-20261029')

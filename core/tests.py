from datetime import timedelta
from django.contrib.auth import get_user_model
from django.contrib.admin.models import LogEntry
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.contrib.staticfiles import finders
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone
from .forms import MemberCreateForm
from .models import Booking, GroupClass, MemberProfile, Membership, Payment, Plan
from .services import book_class, cancel_booking, validate_qr_access
from .serializers import MembershipSerializer
from .exceptions import fitlife_exception_handler


class FitLifeRulesTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        call_command('collectstatic', interactive=False, verbosity=0)

    def setUp(self):
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
        response = self.client.patch(
            f'/api/memberships/{self.member.memberships.first().id}/',
            {'estado': Membership.Status.FROZEN},
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 403)
        response = self.client.patch(
            f'/api/payments/{payment.id}/',
            {'amount': 1},
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 403)
        response = self.client.delete(f'/api/payments/{payment.id}/')
        self.assertEqual(response.status_code, 403)

    def test_admin_can_manage_memberships_and_payments_through_api(self):
        admin = get_user_model().objects.create_user('payment_admin', password='pass12345', is_staff=True)
        self.client.force_login(admin)
        membership = self.member.memberships.first()
        response = self.client.patch(
            f'/api/memberships/{membership.id}/',
            {'estado': Membership.Status.FROZEN},
            content_type='application/json',
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
        response = self.client.patch(
            f'/api/payments/{payment.id}/',
            {'status': Payment.Status.REFUNDED},
            content_type='application/json',
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
        response = self.client.get('/api/', HTTP_ACCEPT='text/html')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'bootstrap')

    def test_api_root_and_global_records_are_restricted(self):
        response = self.client.get('/api/')
        self.assertEqual(response.status_code, 401)
        self.client.force_login(self.member.user)
        response = self.client.get('/api/')
        self.assertEqual(response.status_code, 403)
        response = self.client.get('/api/members/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()['results']), 1)
        response = self.client.get('/api/access-logs/')
        self.assertEqual(response.status_code, 403)
        admin = get_user_model().objects.create_user('api_admin', password='pass12345', is_staff=True)
        self.client.force_login(admin)
        self.assertEqual(self.client.get('/api/').status_code, 200)

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

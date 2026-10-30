from datetime import timedelta
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone
from core.models import GroupClass, MemberProfile, Membership, Plan


class Command(BaseCommand):
    help = 'Crea datos locales de demostración sin duplicar registros.'

    @transaction.atomic
    def handle(self, *args, **options):
        user_model = get_user_model()
        admin, created = user_model.objects.get_or_create(username='fitlife_admin', defaults={
            'email': 'admin@fitlife.local', 'is_staff': True, 'is_superuser': True,
        })
        if created:
            admin.set_password('FitLife.Admin.2026!')
            admin.save(update_fields=['password'])

        plan, _ = Plan.objects.get_or_create(name='Plan Mensual', defaults={
            'description': 'Acceso general y reservas de clases.', 'monthly_price': 25000,
        })
        member_user, created = user_model.objects.get_or_create(username='socio_demo', defaults={
            'first_name': 'Socio', 'last_name': 'Demo', 'email': 'socio@fitlife.local',
        })
        if not created:
            member_user.first_name = 'Socio'
            member_user.last_name = 'Demo'
            member_user.email = 'socio@fitlife.local'
            member_user.save(update_fields=['first_name', 'last_name', 'email'])
        if created:
            member_user.set_password('FitLife.Socio.2026!')
            member_user.save(update_fields=['password'])
        member, _ = MemberProfile.objects.get_or_create(user=member_user, defaults={'phone': '+56911111111'})
        today = timezone.localdate()
        demo_memberships = list(member.memberships.order_by('-end_date', '-id'))
        membership = next((item for item in demo_memberships if item.plan_id == plan.id), None)
        if membership is None:
            membership = Membership(member=member, plan=plan)
        for extra in demo_memberships:
            if extra.pk != membership.pk:
                extra.payments.all().delete()
                extra.delete()
        membership.start_date = today
        membership.end_date = today + timedelta(days=30)
        membership.estado = Membership.Status.ACTIVE
        membership.save()
        start = timezone.now() + timedelta(days=1)
        GroupClass.objects.get_or_create(name='HIIT Demo', starts_at=start, defaults={
            'instructor': 'Coach FitLife', 'ends_at': start + timedelta(minutes=45), 'capacity': 12,
        })
        self.stdout.write(self.style.SUCCESS(
            'Datos demo listos. Admin: fitlife_admin / FitLife.Admin.2026! | Socio demo: socio_demo / FitLife.Socio.2026!'
        ))

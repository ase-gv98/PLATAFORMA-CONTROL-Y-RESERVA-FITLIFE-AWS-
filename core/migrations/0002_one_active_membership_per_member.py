from django.db import migrations, models
from django.db.models import Q


def keep_latest_active_membership(apps, schema_editor):
    Membership = apps.get_model('core', 'Membership')
    Payment = apps.get_model('core', 'Payment')
    duplicate_members = (
        Membership.objects
        .filter(status='ACTIVE')
        .values('member_id')
        .annotate(active_count=models.Count('id'))
        .filter(active_count__gt=1)
    )
    for duplicate in duplicate_members:
        memberships = list(
            Membership.objects
            .filter(member_id=duplicate['member_id'], status='ACTIVE')
            .order_by('-end_date', '-id')
        )
        for extra in memberships[1:]:
            Payment.objects.filter(membership_id=extra.id).delete()
            extra.status = 'EXPIRED'
            extra.save(update_fields=['status'])


class Migration(migrations.Migration):
    dependencies = [
        ('core', '0002_memberprofile_rut'),
    ]

    operations = [
        migrations.RunPython(keep_latest_active_membership, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name='membership',
            constraint=models.UniqueConstraint(
                condition=Q(status='ACTIVE'),
                fields=('member',),
                name='unique_active_membership_per_member',
            ),
        ),
    ]

from django.db import migrations, models
from django.db.models import Q


def migrate_membership_status(apps, schema_editor):
    Membership = apps.get_model('core', 'Membership')
    Membership.objects.filter(estado__in=['ACTIVE', 'TRUE', '1']).update(estado='ACTIVE')
    Membership.objects.filter(estado__in=['EXPIRED', 'FALSE', '0']).update(estado='EXPIRED')
    Membership.objects.filter(estado='FROZEN').update(estado='FROZEN')


class Migration(migrations.Migration):
    dependencies = [
        ('core', '0002_one_active_membership_per_member'),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name='membership',
            name='unique_active_membership_per_member',
        ),
        migrations.RenameField(
            model_name='membership',
            old_name='status',
            new_name='estado',
        ),
        migrations.AddField(
            model_name='payment',
            name='payment_method',
            field=models.CharField(
                choices=[
                    ('CASH', 'Efectivo'),
                    ('CARD', 'Tarjeta'),
                    ('TRANSFER', 'Transferencia'),
                ],
                default='TRANSFER',
                max_length=10,
            ),
        ),
        migrations.AddField(
            model_name='payment',
            name='status',
            field=models.CharField(
                choices=[
                    ('PENDING', 'Pendiente'),
                    ('PAID', 'Pagado'),
                    ('REFUNDED', 'Reembolsado'),
                ],
                default='PAID',
                max_length=10,
            ),
        ),
        migrations.RunPython(migrate_membership_status, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name='membership',
            constraint=models.UniqueConstraint(
                condition=Q(estado='ACTIVE'),
                fields=('member',),
                name='unique_active_membership_per_member',
            ),
        ),
    ]

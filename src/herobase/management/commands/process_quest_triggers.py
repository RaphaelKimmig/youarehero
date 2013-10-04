from django.core.management.base import BaseCommand
from herobase.models import Quest


class Command(BaseCommand):
    help = 'Update quests with timer based start/end triggers'

    def handle(self, *args, **options):
        Quest.objects.update_start_timer_set_but_not_started()
        Quest.objects.update_expired_but_not_done()
# -"- coding:utf-8 -"-
import textwrap
from django.core.files.storage import FileSystemStorage
from django.core.mail import send_mail
from django.db.models.query import QuerySet
from easy_thumbnails.files import get_thumbnailer
import os
from django.conf import settings
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError, PermissionDenied
from django.core.urlresolvers import reverse
from django.core.validators import MinValueValidator
from django.db import models, transaction
from django.utils.datastructures import SortedDict
from django.utils.translation import ugettext_lazy as _

# Create your models here.
from django.db.models.signals import post_save, post_syncdb
from django.utils.decorators import method_decorator
from herobase.fields import LocationField
from heromessage.models import Message

from south.modelsinspector import add_introspection_rules

CLASS_CHOICES =  (
    (5, "Scientist"),
    (1, 'Gadgeteer'),
    (2, 'Diplomat'),
    (3, 'Action'),
    (4, 'Protective'))

def negate(f):
    def decorated(*args, **kwargs):
        return not f(*args, **kwargs)
    return decorated

class ActionMixin(object):
    def get_actions(self):
        return []

    def valid_actions_for(self, request):
        actions = self.get_actions()
        valid_actions = SortedDict()
        for name, action in actions.items():
            valid = True
            for condition in action['conditions']:
                if not condition(request):
                    valid = False
            if valid:
                valid_actions[name] = action
        return valid_actions

    def process_action(self, request, action_name):
        actions = self.get_actions()
        if not action_name in actions:
            raise ValueError("not a valid action")

        action = actions[action_name]

        valid = True
        for condition in action['conditions']:
            if not condition(request):
                valid = False

        if not valid:
            raise PermissionDenied("Can't execute action %s" % action)

        for f in action['actions']:
            f(request)


class AdventureQuerySet(QuerySet):
   def active(self):
       return self.exclude(state=Adventure.STATE_HERO_CANCELED)

class AdventureManager(models.Manager):
    def get_query_set(self):
        return AdventureQuerySet(model=self.model, using=self._db)
    def active(self):
        return self.get_query_set().active()

class Adventure(models.Model, ActionMixin):
    objects = AdventureManager()

    """Model the relationship between a User and a Quest she is engaged in."""
    user = models.ForeignKey(User, related_name='adventures')
    quest = models.ForeignKey('Quest')
    created = models.DateTimeField(auto_now_add=True)
    modified = models.DateTimeField(auto_now=True)

    STATE_NOT_SET = 0
    STATE_HERO_APPLIED = 1
    STATE_OWNER_REFUSED = 2
    STATE_HERO_CANCELED = 3
    STATE_OWNER_ACCEPTED = 4
    STATE_HERO_DONE = 5
    STATE_OWNER_DONE = 6

    state = models.IntegerField(default=STATE_NOT_SET, choices=(
        (STATE_NOT_SET, "kein Status"),
        (STATE_HERO_APPLIED, u'beworben'),
        (STATE_OWNER_REFUSED, u'zurückgewiesen'),
        (STATE_HERO_CANCELED, u'abgebrochen'),
        (STATE_OWNER_ACCEPTED, u'akzeptiert'),
        (STATE_HERO_DONE, u'fordert Bestätigung an'),
        (STATE_OWNER_DONE, u'Teilnahme bestätigt'),
        ))

    def get_actions(self):
        actions = {
            'accept': {
                'conditions': (self.quest.is_owner, self.quest.is_open,
                               lambda r: self.quest.is_open,
                               lambda r: self.state == self.STATE_HERO_APPLIED),
                'actions': (self.accept,),
                'verbose_name': _(u"Akzeptieren"),
            },
            'refuse': {
                'conditions': (self.quest.is_owner,
                               lambda r: self.state == self.STATE_HERO_APPLIED),
                'actions': (self.refuse,),
                'verbose_name': _(u"Zurückweisen"),
            },
            'done': {
                'conditions': (self.quest.is_owner,
                               lambda r: self.state == self.STATE_OWNER_ACCEPTED,
                                lambda r: self.quest.state == Quest.STATE_OWNER_DONE),
                'actions': (self.done,),
                'verbose_name': _(u"Teilnahme bestätigen"),
            },
        }
        return actions

    def __unicode__(self):
        return '%s - %s' % (self.quest.title, self.user.username)

    #### A C T I O N S ####
    def accept(self, request=None):
        self.state = self.STATE_OWNER_ACCEPTED
        if not self.quest.auto_accept:
            Message.send(get_system_user(), self.user,
                'Du wurdest als Held Akzeptiert',
                textwrap.dedent('''\
                Du wurdest als Held akzeptiert. Es kann losgehen!
                Verabredet dich jetzt mit dem Questgeber um die Quest zu erledigen.

                Quest: https://youarehero.net%s''' % self.quest.get_absolute_url()))
        self.save()
        self.quest.check_full()
        self.quest.save()

    def refuse(self, request=None):
        self.state = self.STATE_OWNER_REFUSED
        self.save()

    def done(self, request=None):
        profile = self.user.get_profile()
        profile.experience += self.quest.experience
        profile.save()

        self.state = self.STATE_OWNER_DONE
        self.save()


class QuestQuerySet(QuerySet):
    def active(self):
        return self.exclude(state__in=(Quest.STATE_OWNER_DONE, Quest.STATE_OWNER_CANCELED))
    def inactive(self):
        return self.filter(state__in=(Quest.STATE_OWNER_DONE, Quest.STATE_OWNER_CANCELED))


class QuestManager(models.Manager):
    def get_query_set(self):
        return QuestQuerySet(model=self.model, using=self._db)
    def active(self):
        return self.get_query_set().active()
    def inactive(self):
        return self.get_query_set().inactive()

class Quest(models.Model, ActionMixin):
    objects = QuestManager()

    """A quest, owned by a user"""
    owner = models.ForeignKey(User, related_name='created_quests')
    title = models.CharField(max_length=255)
    description = models.TextField()

    location = models.CharField(max_length=255) # TODO : placeholder
    due_date = models.DateTimeField()

    hero_class = models.IntegerField(choices=CLASS_CHOICES)
    heroes = models.ManyToManyField(User, through=Adventure, related_name='quests')

    created = models.DateTimeField(auto_now_add=True)
    modified = models.DateTimeField(auto_now=True)

    max_heroes = models.PositiveIntegerField(default=1, validators=[MinValueValidator(1)])
    auto_accept = models.BooleanField(default=False, verbose_name="automatisch akzeptieren",
        help_text=_(u"Wenn aktiviert, akzeptierst Du Helden automatisch. Du kannst dann allerdings niemanden zurückweisen."))

    QUEST_LEVELS = (
        (1, '1 (Easy)'), (2, '2 (Okay)'), (3, '3 (Experienced)'), (4, '4 (Challenging)'), (5, 'Heroic')
    )

    level = models.PositiveIntegerField(choices=QUEST_LEVELS)
    experience = models.PositiveIntegerField()

    STATE_NOT_SET = 0
    STATE_OPEN = 1
    STATE_FULL = 2
    STATE_OWNER_DONE = 3
    STATE_OWNER_CANCELED = 4

    QUEST_STATES = (
            (STATE_OPEN , 'offen'),
            (STATE_FULL , 'voll'),
            (STATE_OWNER_DONE , 'abgeschlossen'),
            (STATE_OWNER_CANCELED , 'abgebrochen'),
        )

    state = models.IntegerField(default=STATE_OPEN, choices=QUEST_STATES)

    def active_heroes(self):
        """Return all accepted heroes and heros who claim to be done"""
        return self.heroes.filter(adventures__quest=self.pk,
            adventures__state__in=(Adventure.STATE_OWNER_ACCEPTED,
                                   Adventure.STATE_HERO_DONE))

    def accepted_heroes(self):
        """Return all accepted heroes and heros who are done or claim so"""
        return self.heroes.filter(adventures__quest=self.pk,
            adventures__state__in=(Adventure.STATE_OWNER_ACCEPTED,
                                   Adventure.STATE_OWNER_DONE,
                                   Adventure.STATE_HERO_DONE))

    def applying_heroes(self):
        return self.heroes.filter(adventures__quest=self.pk,
            adventures__state=Adventure.STATE_HERO_APPLIED)

    def done_heroes(self):
        return self.heroes.filter(adventures__quest=self.pk,
            adventures__state=Adventure.STATE_OWNER_DONE)

    def remaining_slots(self):
        return self.max_heroes - self.accepted_heroes().count()

    def clean(self):
        if self.experience and self.level and self.experience > self.level * 100: # TODO experience formula
            raise ValidationError('Maximum experience for quest with level {0} is {1}'.format(self.level, self.level * 100))


    def get_actions(self):
        actions = SortedDict((
            ('done', {
                'conditions': (self.is_owner, self.is_active,
                               lambda r: self.accepted_heroes()),
                'actions': (self.done, ),
                'verbose_name': _(u"Quest abschließen"),

                }),
            ('cancel', {
                'conditions': (self.is_owner, self.is_active),
                'actions': (self.cancel,),
                'verbose_name': _("Quest abbrechen"),
                }),
            ('hero_apply', {
                'conditions': (self.is_open, self.can_apply,
                                self.is_open),
                'actions': (self.hero_apply, ),
                'verbose_name': _("Bewerben"),
                }),
            ('hero_cancel', {
                'conditions': (self.is_active,
                               lambda r: r.user in (list(self.active_heroes()) + list(self.applying_heroes()))),
                'actions': (self.hero_cancel, ),
                'verbose_name': _("Abbrechen"),
                }),
            ))
        return actions

    ####  A C T I O N S ####

    def hero_cancel(self, request):
        adventure = self.adventure_set.get(user=request.user)
        adventure.state = Adventure.STATE_HERO_CANCELED
        adventure.save()
        self.check_full()
        self.save()

    def hero_apply(self, request):
        adventure, created = self.adventure_set.get_or_create(user=request.user)
        if adventure.state != Adventure.STATE_HERO_CANCELED:
            if self.auto_accept:
                Message.send(get_system_user(), self.owner, 'Ein Held hat sich beworben',
                textwrap.dedent('''\
                Auf eine deiner Quests hat sich ein Held beworben.
                Verabredet euch jetzt um die Quest zu erledigen.

                Quest: https://youarehero.net%s''' % self.get_absolute_url()))
            else:
                Message.send(get_system_user(), self.owner, 'Ein Held hat sich beworben',
                    textwrap.dedent('''\
                    Auf eine deiner Quests hat sich ein Held beworben.
                    Damit er auch mitmachen kann solltest du seine Teilnahme erlauben.
                    Verabredet euch dann um die Quest zu erledigen.

                    Quest: https://youarehero.net%s''' % self.get_absolute_url()))
        if self.auto_accept:
            adventure.state = Adventure.STATE_OWNER_ACCEPTED
        else:
            adventure.state = Adventure.STATE_HERO_APPLIED
        adventure.save()
#        Message.objects.create(
#            sender=get_system_user(),
#            recipient=adventure.quest.owner,
#            text="%s applied to your Quest %s. Have a look at https://youarehero.net%s" % (
#                request.user, adventure.quest.title,
#                adventure.quest.get_absolute_url()),
#            title="New Hero applied")

    def cancel(self, request=None):
        self.state = self.STATE_OWNER_CANCELED
        self.save()

    def done(self, request=None):
        # todo: xp und so

        self.state = self.STATE_OWNER_DONE
        self.save()

    #### C O N D I T I O N S ####
    def needs_attention(self):
        return self.adventure_set.filter(state__in=(Adventure.STATE_HERO_APPLIED, Adventure.STATE_HERO_DONE)).exists()

    def is_owner(self, request):
        return self.owner == request.user

    def is_canceled(self, request=None):
        return self.state == Quest.STATE_OWNER_CANCELED

    def is_open(self, request=None):
        return self.state == Quest.STATE_OPEN

    def is_done(self, request=None):
        return self.state == Quest.STATE_OWNER_DONE

    def is_full(self, request=None):
        return self.state == Quest.STATE_FULL

    def is_active(self, request=None):
        return self.state in (Quest.STATE_OPEN, Quest.STATE_FULL)

    def is_closed(self, request=None):
        return self.state in (Quest.STATE_OWNER_CANCELED, Quest.STATE_OWNER_DONE)

    def check_full(self, request=None):
        """Calculates if quest is full or not"""
        if self.is_closed():
            return
        if not self.max_heroes:
            return
        if self.accepted_heroes().count() < self.max_heroes:
            self.state = Quest.STATE_OPEN
        else:
            self.state = Quest.STATE_FULL

    def can_apply(self, request):
        if self.is_owner(request):
            return False
        try:
            adventure = self.adventure_set.get(user=request.user)
        except Adventure.DoesNotExist:
            return True
        return adventure.state in (Adventure.STATE_HERO_CANCELED, )

    #### M I S C ####

    def get_absolute_url(self):
        """Get the url for this quests detail page."""
        return reverse("quest-detail", args=(self.pk,))

    def __unicode__(self):
        return self.title


class UserProfile(models.Model):

    add_introspection_rules([], ["^herobase\.fields\.LocationField"])

    """Hold extended user information."""
    user = models.OneToOneField(User)
    experience = models.PositiveIntegerField(default=0)
    location = models.CharField(max_length=255) # TODO : placeholder
    hero_class = models.IntegerField(choices=CLASS_CHOICES, blank=True, null=True)

    geolocation = LocationField(_(u'geolocation'), max_length=100, default='48,8') # todo : fix default :-)

    created = models.DateTimeField(auto_now_add=True)
    modified = models.DateTimeField(auto_now=True)

    public_location = models.BooleanField(default=False, verbose_name=_("Location is public"),
        help_text=_("Enable this if you want to share your location with other Heroes."))

    about = models.TextField(blank=True, default='', help_text='Some text about you.')

    receive_system_email = models.BooleanField(default=False, verbose_name="Bei Questaenderungen per Mail benachrichtigen.", help_text="Setze diesen Hacken wenn du bei Aenderungen an deinen Quests per Mail benachrichtigt werden willst")
    receive_private_email = models.BooleanField(default=False, verbose_name="Bei privaten Nachrichten per Mail benachrichtigen.", help_text="Setze diesen Hacken wenn du bei Nachrichten von anderen NUtzern benachrichtigt werden willst")

    CLASS_AVATARS =  {
        5: "scientist.jpg",
        1: 'gadgeteer.jpg',
        2: 'diplomat.jpg',
        3: 'action.jpg',
        4: 'protective.jpg'}
    avatar_storage = FileSystemStorage(location=os.path.join(settings.PROJECT_ROOT, 'assets/'))

    def avatar_thumbnails(self):
        return self._avatar_thumbnails((50, 50))

    def avatar_thumbnails_tiny(self):
        return self._avatar_thumbnails((15, 15))

    def _avatar_thumbnails(self, size):
        thumbs = []
        for id, image_name in self.CLASS_AVATARS.items():
            image = os.path.join('avatar/', image_name)
            thumbnailer = get_thumbnailer(self.avatar_storage, image)
            thumbnail = thumbnailer.get_thumbnail({'size': size, 'quality':90})
            thumbs.append((id, os.path.join(settings.MEDIA_URL, thumbnail.url )))
        return thumbs

    def avatar(self):
        file_name = "default.png"
        if self.hero_class  is not None:
            file_name = self.CLASS_AVATARS[self.hero_class]
        image = os.path.join('avatar/', file_name)
        thumbnailer = get_thumbnailer(self.avatar_storage, image)
        thumbnail = thumbnailer.get_thumbnail({'size': (270, 270), 'quality':90})
        return os.path.join(settings.MEDIA_URL, thumbnail.url )

    @property
    def get_geolocation(self):
        if self.geolocation:
            return self.geolocation.split(',')

    @property
    def level(self):
        """Calculate the user's level based on her experience"""
        return int(self.experience / 1000) + 1 # TODO: correct formula

    def relative_level_experience(self):
        return (self.experience % 1000) / 10 # TODO: correct formula

    @property
    def unread_messages_count(self):
        return Message.objects.filter(recipient=self.user,read__isnull=True,recipient_deleted__isnull=True).count()

    def __unicode__(self):
        return self.user.username

def create_user_profile(sender, instance, created, **kwargs):
    """Create a user profile on user account creation."""
    if created:
        try:
            UserProfile.objects.get_or_create(user=instance)
        except:
            pass
post_save.connect(create_user_profile, sender=User)


class Location(models.Model):
    latitude = models.FloatField()
    longitude = models.FloatField()

    zip_code = models.CharField(max_length=255)
    country = models.CharField(max_length=255)
    state = models.CharField(max_length=255)
    city = models.CharField(max_length=255)
    street = models.CharField(max_length=255)
    class Meta:
        abstract = True
        # plz, hausnummer, strasse, stadt, bundesland


# wohin damit?

from registration.signals import user_activated
from django.contrib.auth import login, authenticate

def login_on_activation(sender, user, request, **kwargs):
    """Logs in the user after activation"""
    user.backend = 'django.contrib.auth.backends.ModelBackend'
    login(request, user)

# Registers the function with the django-registration user_activated signal
user_activated.connect(login_on_activation)

SYSTEM_USER_NAME = "YouAreHero"

def get_system_user():
    user, created = User.objects.get_or_create(username=SYSTEM_USER_NAME)
    return user

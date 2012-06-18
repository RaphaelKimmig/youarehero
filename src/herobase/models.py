# -"- coding:utf-8 -"-
"""
This module provides all the basic Quest related models for You are HERO.
The most important are Quest, Userprofile (represents a hero) and Adventure.
This module also contains the ActionMixin, which provides basic logic for model actions.
The model actions connect state logic to the models.
"""
from functools import wraps
from operator import attrgetter

import os
import textwrap

from django.core.files.storage import FileSystemStorage
from django.core.mail import send_mail
from django.db.models.query import QuerySet
from django.conf import settings
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError, PermissionDenied
from django.core.urlresolvers import reverse
from django.core.validators import MinValueValidator
from django.db import models, transaction
from django.utils.datastructures import SortedDict
from django.utils.translation import ugettext_lazy as _
from django.db.models.signals import post_save, post_syncdb
from django.utils.decorators import method_decorator

from easy_thumbnails.files import get_thumbnailer
from south.modelsinspector import add_introspection_rules

from herobase.fields import LocationField
from heromessage.models import Message

# The classes a User can choose from. (Heroclasses)
CLASS_CHOICES =  (
    (5, "Scientist"),
    (1, 'Gadgeteer'),
    (2, 'Diplomat'),
    (3, 'Action'),
    (4, 'Protective'))



def action(verbose_name=None, condition=None):
    index = getattr(action, 'index', 1)
    def decorator(f):
        f.action = index
        f.verbose_name = verbose_name or f.__name__
        return f
    setattr(action, 'index', index + 1)
    return method_decorator(decorator)

class ActionMixin(object):
    """Provides a mixin to use in Models that want to implement actions."""

    def get_actions(self):
        """
        Return a list of actions representing available actions for a model
        instance.
        """
        if not hasattr(self.__class__, '_cached_actions'):
            actions = []
            for key, element in self.__class__.__dict__.items():
                if getattr(element, 'action', None):
                    actions.append((key, element))
            actions.sort(key=lambda (key, element): element.action)
            setattr(self.__class__, '_cached_actions', zip(*actions)[0])
        return getattr(self.__class__, '_cached_actions')

    def valid_actions_for(self, request):
        """
        Return a dict containing all actions that may be executed given a 
        request.
        """
        actions = self.get_actions()
        valid_actions = SortedDict()
        for name in actions:
            action = getattr(self, name)
            if action(request, validate_only=True):
                valid_actions[name] = {'verbose_name': action.verbose_name}
        return valid_actions

    def process_action(self, request, action_name):
        """Execute an action if all its preconditions are satisfied."""
        actions = self.get_actions()
        if not action_name in actions:
            raise ValueError("not a valid action")
        action = getattr(self, action_name)
        if not action(request, validate_only=True):
            raise PermissionDenied(action_name)
        return action(request)


class AdventureQuerySet(QuerySet):
    def active(self):
        """Show only adventures that have not been canceled."""
        return self.exclude(state=Adventure.STATE_HERO_CANCELED)
    def in_progress(self):
        return self.filter(state__in=(Adventure.STATE_HERO_APPLIED,
                                      Adventure.STATE_OWNER_ACCEPTED,
                                      Adventure.STATE_HERO_DONE))
class AdventureManager(models.Manager):
    """Custom Object Manager for Adventures, excluding canceled ones."""
    def get_query_set(self):
        return AdventureQuerySet(model=self.model, using=self._db)
    def active(self):
        """Show only adventures that have not been canceled."""
        return self.get_query_set().active()
    def in_progress(self):
        return self.get_query_set().in_progress()

class Adventure(models.Model, ActionMixin):
    """Model the relationship between a User and a Quest she is engaged in."""

    objects = AdventureManager()

    user = models.ForeignKey(User, related_name='adventures')
    quest = models.ForeignKey('Quest')
    created = models.DateTimeField(auto_now_add=True)
    modified = models.DateTimeField(auto_now=True)

    # The Adventure States are related to a Quest and a User
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

    def __unicode__(self):
        return '%s - %s' % (self.quest.title, self.user.username)

    @action(verbose_name=_("Accept"))
    def accept(self, request, validate_only=False):
        """Accept adventure.user as a participant and send a notification."""
        valid = self.quest.is_open() and request.user == self.quest.owner and self.state == Adventure.STATE_HERO_APPLIED
        if validate_only or not valid:
            return valid
        self.state = self.STATE_OWNER_ACCEPTED
        # send a message if acceptance is not instantaneous
        if not self.quest.auto_accept:
            Message.send(get_system_user(), self.user,
                'Du wurdest als Held Akzeptiert',
                textwrap.dedent('''\
                Du wurdest als Held akzeptiert. Es kann losgehen!
                Verabredet dich jetzt mit dem Questgeber um die Quest zu erledigen.

                Quest: https://youarehero.net%s''' % self.quest.get_absolute_url()))
        self.save()
        # recalculate denormalized quest state (quest might be full now)
        self.quest.check_full()
        self.quest.save()


    @action(verbose_name=_("Refuse"))
    def refuse(self, request=None, validate_only=False):
        """Deny adventure.user participation in the quest."""
        # TODO : this should maybe generate a message?
        valid = self.quest.owner == request.user and self.state == self.STATE_HERO_APPLIED
        if validate_only or not valid:
            return valid
        self.state = self.STATE_OWNER_REFUSED
        self.save()

    @action(verbose_name=_("Done"))
    def done(self, request=None, validate_only=False):
        """Confirm a users participation in a quest."""
        valid = (self.quest.owner == request.user and
                 self.state == self.STATE_OWNER_ACCEPTED and
                 self.quest.state == Quest.STATE_OWNER_DONE)
        if validate_only or not valid:
            return valid
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
    """Custom Quest Object Manager, for active and inactive `Quest` objects"""
    def get_query_set(self):
        return QuestQuerySet(model=self.model, using=self._db)
    def active(self):
        return self.get_query_set().active()
    def inactive(self):
        return self.get_query_set().inactive()


class Quest(models.Model, ActionMixin):
    """A quest, owned by a user."""
    objects = QuestManager()

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
        help_text=_(u"Wenn aktiviert, akzeptierst Du Helden automatisch."
                    u" Du kannst dann allerdings niemanden zurückweisen."))

    QUEST_LEVELS = (
        (1, '1 (Easy)'),
        (2, '2 (Okay)'),
        (3, '3 (Experienced)'),
        (4, '4 (Challenging)'),
        (5, 'Heroic')
    )

    level = models.PositiveIntegerField(choices=QUEST_LEVELS)
    experience = models.PositiveIntegerField()

    # States for the Quest. OPEN + FULL = ACTIVE, DONE + CANCELED = INACTIVE
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
        """Return all heroes active on a quest. These are accepted heroes
         and heroes who claim to be done."""
        return self.heroes.filter(adventures__quest=self.pk,
            adventures__state__in=(Adventure.STATE_OWNER_ACCEPTED,
                                   Adventure.STATE_HERO_DONE))

    def accepted_heroes(self):
        """Return all accepted heroes and following states (heros who
        are done or claim so)"""
        return self.heroes.filter(adventures__quest=self.pk,
            adventures__state__in=(Adventure.STATE_OWNER_ACCEPTED,
                                   Adventure.STATE_OWNER_DONE,
                                   Adventure.STATE_HERO_DONE))

    def applying_heroes(self):
        """Return all heroes, applying for the quest."""
        return self.heroes.filter(adventures__quest=self.pk,
            adventures__state=Adventure.STATE_HERO_APPLIED)

    def remaining_slots(self):
        """Number of heroes who may participate in the quest until maximum
         number of heroes is achieved"""
        return self.max_heroes - self.accepted_heroes().count()

    def clean(self):
        """Clean function for form validation: Max XPs are associated to quest level"""
        if self.experience and self.level and self.experience > self.level * 100: # TODO experience formula
            raise ValidationError('Maximum experience for quest with level {0} is {1}'.format(self.level, self.level * 100))


    @action(verbose_name=_("Cancel"))
    def hero_cancel(self, request, validate_only=False):
        """Cancels an adventure on this quest."""
        valid = self.is_active() and Adventure.objects.in_progress().filter(quest=self, user=request.user).exists()
        if validate_only or not valid:
            return valid
        adventure = self.adventure_set.get(user=request.user)
        adventure.state = Adventure.STATE_HERO_CANCELED
        adventure.save()
        self.check_full()
        self.save()

    @action(verbose_name=_("Apply"))
    def hero_apply(self, request, validate_only=False):
        """Applies a hero to the quest and create an adventure for her."""
        if not self.is_open():
            valid = False
        else:
            try:
                adventure = self.adventure_set.get(user=request.user)
            except Adventure.DoesNotExist:
                valid = True
                    # iff we already have an adventure object the only reason for applying
                # again is a previous cancellation
            else:
                valid = adventure.state in (Adventure.STATE_HERO_CANCELED, )
        if validate_only or not valid:
            return valid

        adventure, created = self.adventure_set.get_or_create(user=request.user)
        # send a message when a hero applies for the first time
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

    @action(verbose_name=_("Cancel"))
    def cancel(self, request, validate_only=False):
        """Cancels the whole quest."""
        valid = self.owner == request.user and self.is_active()
        if validate_only or not valid:
            return valid
        self.state = self.STATE_OWNER_CANCELED
        self.save()

    @action(verbose_name=_("Mark as done"))
    def done(self, request, validate_only=False):
        """Mark the quest as done. The quest is complete and inactive."""
        valid = self.owner == request.user and self.is_active() and self.accepted_heroes().exists()
        if validate_only or not valid:
            return valid

        self.state = self.STATE_OWNER_DONE
        self.save()

    #### C O N D I T I O N S ####
    def needs_attention(self):
        """Only for playtest. Later there should be Notifications for this."""
        return self.adventure_set.filter(state__in=(Adventure.STATE_HERO_APPLIED,
                                                    Adventure.STATE_HERO_DONE)).exists()

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
        """Calculates if quest is full or not. Needs to be called when
        a hero is accepted or cancels his adventure."""
        if self.is_closed():
            return
        if not self.max_heroes:
            return
        if self.accepted_heroes().count() < self.max_heroes:
            self.state = Quest.STATE_OPEN
        else:
            self.state = Quest.STATE_FULL
        self.save()

    #### M I S C ####

    def get_absolute_url(self):
        """Get the url for this quests detail page."""
        return reverse("quest-detail", args=(self.pk,))

    def __unicode__(self):
        """String representation"""
        return self.title


class UserProfile(models.Model):
    """This model extends a django user with additional hero information."""
    add_introspection_rules([], ["^herobase\.fields\.LocationField"])

    user = models.OneToOneField(User)
    experience = models.PositiveIntegerField(default=0)
    location = models.CharField(max_length=255) # TODO : placeholder
    hero_class = models.IntegerField(choices=CLASS_CHOICES, blank=True, null=True)

    # google geolocation
    geolocation = LocationField(_(u'geolocation'), max_length=100, default='48,8') # todo : fix default :-)

    created = models.DateTimeField(auto_now_add=True)
    modified = models.DateTimeField(auto_now=True)

    public_location = models.BooleanField(default=False, verbose_name=_("Location is public"),
        help_text=_("Enable this if you want to share your location with other Heroes."))

    about = models.TextField(blank=True, default='', help_text='Some text about you.')

    receive_system_email = models.BooleanField(default=False,
        verbose_name="Bei Questaenderungen per Mail benachrichtigen.",
        help_text="Setze diesen Hacken wenn du bei Aenderungen an deinen"
                  " Quests per Mail benachrichtigt werden willst")
    receive_private_email = models.BooleanField(default=False,
        verbose_name="Bei privaten Nachrichten per Mail benachrichtigen.",
        help_text="Setze diesen Hacken wenn du bei Nachrichten von anderen "
                  "Nutzern benachrichtigt werden willst")

    CLASS_AVATARS =  {
        5: "scientist.jpg",
        1: 'gadgeteer.jpg',
        2: 'diplomat.jpg',
        3: 'action.jpg',
        4: 'protective.jpg'}
    avatar_storage = FileSystemStorage(location=os.path.join(settings.PROJECT_ROOT, 'assets/'))

    def avatar_thumbnails(self):
        """Return a list of avatar thumbnails 50x50"""
        return self._avatar_thumbnails((50, 50))

    def avatar_thumbnails_tiny(self):
        """Return a list of avatar thumbnails 15x15"""
        return self._avatar_thumbnails((15, 15))

    def _avatar_thumbnails(self, size):
        """Return a list of tuples (id,img_url) of avatar thumbnails."""
        thumbs = []
        for id, image_name in self.CLASS_AVATARS.items():
            image = os.path.join('avatar/', image_name)
            thumbnailer = get_thumbnailer(self.avatar_storage, image)
            thumbnail = thumbnailer.get_thumbnail({'size': size, 'quality':90})
            thumbs.append((id, os.path.join(settings.MEDIA_URL, thumbnail.url )))
        return thumbs

    def avatar(self):
        """Return a String, containing a path to a thumbnail-image 270x270."""
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
        """Calculates percentage of XP for current level."""
        return (self.experience % 1000) / 10 # TODO: correct formula

    @property
    def unread_messages_count(self):
        """Return number of unread messages."""
        return Message.objects.filter(recipient=self.user,read__isnull=True,
            recipient_deleted__isnull=True).count()

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
    """Return an unique system-user. Creates one if not existing."""
    user, created = User.objects.get_or_create(username=SYSTEM_USER_NAME)
    return user

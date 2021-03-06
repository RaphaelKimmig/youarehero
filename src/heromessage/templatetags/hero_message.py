# -*- coding: utf-8 -*-
from django import template
from django.core.urlresolvers import reverse
from django.utils.html import escape
from django.utils.http import urlquote
from django.utils.safestring import mark_safe
from django.utils.translation import ugettext

register = template.Library()

@register.simple_tag
def message_user(user):
    mail_link = '<a class="box-button" href="%(url)s" data-toggle="tooltip" data-title="%(tooltip)s"><i class="icon-envelope-alt"></i></a>' % {
        'url': reverse('message_to', args=(user.pk, )),
        'tooltip': ugettext(u'Send message to %(username)s') % {'username': escape(user.username)},
    }
    return mark_safe(mail_link)

@register.simple_tag
def message_team(team, size=20):
    mail_link = '<a href="%(url)s" data-toggle="tooltip" data-title="%(tooltip)s"><i class="icon-yah-mail icon-%(size)s"></i></a>' % {
        'url': reverse('message_team_to', args=(urlquote(team), )),
        'tooltip': ugettext(u'Send message to team %(team)s') % {'team': escape(team)},
        'size': size,
    }
    return mark_safe(mail_link)

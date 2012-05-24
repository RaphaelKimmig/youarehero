# -*- coding: utf-8 -*-
from crispy_forms.bootstrap import FormActions
from django.conf.urls import url
from django.core.exceptions import ValidationError
from django.forms.models import ModelForm
from django.forms.util import ErrorList
from herobase.models import Quest, UserProfile

from crispy_forms.helper import FormHelper
from crispy_forms.layout import Submit, Layout, Fieldset, Div
from django.utils.translation import ugettext_lazy as _

class QuestCreateForm(ModelForm):
    def __init__(self, *args, **kwargs):
        self.helper = FormHelper()
        self.helper.form_method = 'post'
        self.helper.form_action = 'quest-create'
        self.helper.form_class = 'well form-horizontal'
        self.request = kwargs.pop('request')

        #self.helper.add_input(Submit('submit', 'Submit'))
        self.helper.layout = Layout(
            Fieldset(
                'Create a Quest',
                Div(
                    Div(
                        'title',
                        'description',
                        css_class="span6",
                    ),
                    Div(
                        'hero_class',
                        'max_heroes',
                        'auto_accept',
                        'level',
                        'experience',
                        'location',
                        'due_date',
                        css_class="span6",
                    ),
                    css_class="row",
                ),
            ),
            FormActions(
                Submit('save', 'Save', css_class='btn-primary btn-large')
            ),
        )
        super(QuestCreateForm, self).__init__(*args, **kwargs)


    def clean_level(self):
        data = self.cleaned_data['level']
        if self.request.user.get_profile().level < int(data):
            raise ValidationError("Your level is not high enough for this quest level!")
        return data

    def clean(self):
        data = super(QuestCreateForm, self).clean()
        if ('experience' in data and 'level' in data and
            int(data['experience']) > int(data['level']) * 100): # TODO experience formula
            self._errors['experience'] = self._errors.get('experience', ErrorList())
            self._errors['experience'].append(_(u'Experience to high for level.'))
            del data['experience']
        return data

    class Meta:
        model = Quest
        fields = ('title', 'description', 'max_heroes', 'location', 'due_date', 'hero_class', 'level' ,'experience', 'auto_accept')


class UserProfileEdit(ModelForm):
    def __init__(self, *args, **kwargs):
        self.helper = FormHelper()
        self.helper.form_method = 'post'
        self.helper.form_action = 'user-edit'
        self.helper.form_class = 'well form-horizontal'

        #self.helper.add_input(Submit('submit', 'Submit'))
        self.helper.layout = Layout(
            Fieldset(
                _('Edit your Profile'),
                Div(
                    'location',
                    'hero_class'
                )
            ),
            FormActions(
                Submit('save', 'Save', css_class='btn-primary btn-large')
            ),
        )
        super(UserProfileEdit, self).__init__(*args, **kwargs)

    class Meta:
        model = UserProfile
        fields = ('location', 'hero_class')
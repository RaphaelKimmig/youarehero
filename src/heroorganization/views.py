# Create your views here.
from django.contrib.auth.decorators import login_required
from django.core.urlresolvers import reverse
from django.http import HttpResponseRedirect, HttpResponseForbidden
from django.shortcuts import get_object_or_404
from django.utils.decorators import method_decorator
from django.views.generic.base import TemplateView
from django.views.generic.detail import DetailView
from django.views.generic.edit import UpdateView, CreateView
from django.views.generic.list import ListView
from herobase.forms import QuestCreateForm
from herobase.models import Quest
from heroorganization.forms import OrganizationForm
from heroorganization.models import Organization


class OrganizationListView(ListView):
    slug_url_kwarg = 'name'
    slug_field = 'user__username'
    model = Organization
    template_name = "heroorganization/organization_list.html"


class OrganizationDetailView(DetailView):
    slug_url_kwarg = 'name'
    slug_field = 'user__username'
    model = Organization
    template_name = "heroorganization/organization_detail.html"


class OrgAdminMixin(object):
    @method_decorator(login_required)
    def dispatch(self, request, *args, **kwargs):
        try:
            self.organization = Organization.objects.get(user=request.user)
            print "gotit"
            self.kwargs['pk'] = kwargs['pk'] = self.organization.pk
        except Organization.DoesNotExist:
            return HttpResponseForbidden()

        return super(OrgAdminMixin, self).dispatch(request, *args, **kwargs)


class OrgAdminIndexView(OrgAdminMixin, TemplateView):
    template_name = "heroorganization/admin/index.html"

    def get_context_data(self, **kwargs):
        context = super(OrgAdminIndexView, self).get_context_data(**kwargs)
        context["organization"] = self.organization
        return context


class OrgAdminUpdateView(OrgAdminMixin, UpdateView):
    template_name = "heroorganization/admin/update.html"
    model = Organization
    form_class = OrganizationForm

    def get_success_url(self):
        return reverse("organization_admin_index")
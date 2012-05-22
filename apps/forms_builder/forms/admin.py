
from csv import writer
from datetime import datetime
from mimetypes import guess_type
from os.path import join

from django.conf import settings
from django.utils.encoding import iri_to_uri
from django.conf.urls.defaults import patterns, url
from django.contrib import admin
from django.core.files.storage import FileSystemStorage
from django.core.urlresolvers import reverse
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.template import loader, Context
from django.template.defaultfilters import slugify
from django.utils.translation import ugettext_lazy as _

from event_logs.models import EventLog
from perms.utils import is_admin, update_perms_and_save

from forms_builder.forms.models import Form, Field, FormEntry, FieldEntry
from forms_builder.forms.settings import UPLOAD_ROOT
from forms_builder.forms.forms import FormAdminForm, FormForField

fs = FileSystemStorage(location=UPLOAD_ROOT)

class FieldAdminForm(FormForField):
    class Meta:
        model = Field

    
class FieldAdmin(admin.TabularInline):
    model = Field    
    form = FieldAdminForm
    extra = 0
    ordering = ("position",)

class FormAdmin(admin.ModelAdmin):

    inlines = (FieldAdmin,)
    list_display = ("title", "id", "intro", "email_from", "email_copies", 
        "admin_link_export", "admin_link_view")
    list_display_links = ("title",)
#    list_filter = ("status",)
    search_fields = ("title", "intro", "response", "email_from", 
        "email_copies")
#    radio_fields = {"status": admin.HORIZONTAL}
    fieldsets = (
        (None, {"fields": ("title", "slug", "intro", "response", "completion_url")}),
        (_("Email"), {"fields": ('subject_template', "email_from", "email_copies", "send_email", "email_text")}),
        ('Permissions', {'fields': ('allow_anonymous_view',)}),
        ('Advanced Permissions', {'classes': ('collapse',),'fields': (
            'user_perms',
            'member_perms',
            'group_perms',
        )}),
        ('Publishing Status', {'fields': (
            'status',
            'status_detail'
        )}),
    )
    
    form = FormAdminForm
    
    class Media:
        js = (
            '%sjs/jquery-1.6.2.min.js' % settings.STATIC_URL,
            #'%sjs/jquery_ui_all_custom/jquery-ui-1.8.5.custom.min.js' % settings.STATIC_URL,
            #'https://ajax.googleapis.com/ajax/libs/jquery/1.7.2/jquery.min.js',
            'https://ajax.googleapis.com/ajax/libs/jqueryui/1.8.18/jquery-ui.min.js',
            '%sjs/global/tinymce.event_handlers.js' % settings.STATIC_URL,
            '%sjs/admin/form-fields-inline-ordering.js' % settings.STATIC_URL,
        )
        css = {'all': ['%scss/admin/dynamic-inlines-with-sort.css' % settings.STATIC_URL], }

    def get_urls(self):
        """
        Add the export view to urls.
        """
        urls = super(FormAdmin, self).get_urls()
        extra_urls = patterns("", 
            url("^export/(?P<form_id>\d+)/$", 
                self.admin_site.admin_view(self.export_view), 
                name="forms_form_export"),
            url("^file/(?P<field_entry_id>\d+)/$", 
                self.admin_site.admin_view(self.file_view), 
                name="forms_form_file"),
        )
        return extra_urls + urls

    def export_view(self, request, form_id):
        """
        Output a CSV file to the browser containing the entries for the form. 
        """
        form = get_object_or_404(Form, id=form_id)
        response = HttpResponse(mimetype="text/csv")
        csvname = "%s-%s.csv" % (form.slug, slugify(datetime.now().ctime()))
        response["Content-Disposition"] = "attachment; filename=%s" % csvname
        csv = writer(response)
        # Write out the column names and store the index of each field 
        # against its ID for building each entry row. Also store the IDs of 
        # fields with a type of FileField for converting their field values 
        # into download URLs.
        columns = []
        field_indexes = {}
        file_field_ids = []
        for field in form.fields.all():
            columns.append(field.label.encode("utf-8"))
            field_indexes[field.id] = len(field_indexes)
            if field.field_type == "FileField":
                file_field_ids.append(field.id)
        entry_time_name = FormEntry._meta.get_field("entry_time").verbose_name
        columns.append(unicode(entry_time_name))
        columns.append(unicode("Pricing"))
        columns.append(unicode("Price"))
        columns.append(unicode("Payment Method"))
        csv.writerow(columns)
        # Loop through each field value order by entry, building up each  
        # entry as a row.
        entries = FormEntry.objects.filter(form=form).order_by('pk')
        for entry in entries:
            values = FieldEntry.objects.filter(entry=entry)
            row = [""] * len(columns)
            row[-4] = entry.entry_time
            if entry.pricing:
                row[-3] = entry.pricing.label
                row[-2] = entry.pricing.price
            row[-1] = entry.payment_method
            for field_entry in values:
                value = field_entry.value.encode("utf-8")
                # Create download URL for file fields.
                if field_entry.field_id in file_field_ids:
                    url = reverse("admin:forms_form_file", args=(field_entry.id,))
                    value = request.build_absolute_uri(url)
                # Only use values for fields that currently exist for the form.
                try:
                    row[field_indexes[field_entry.field_id]] = value
                except KeyError:
                    pass
            # Write out the row.
            csv.writerow(row)
        return response

    def file_view(self, request, field_entry_id):
        """
        Output the file for the requested field entry.
        """
        field_entry = get_object_or_404(FieldEntry, id=field_entry_id)
        path = join(fs.location, field_entry.value)
        response = HttpResponse(mimetype=guess_type(path)[0])
        f = open(path, "r+b")
        response["Content-Disposition"] = "attachment; filename=%s" % f.name
        response.write(f.read())
        f.close()
        return response
    
    def log_deletion(self, request, object, object_repr):
        super(FormAdmin, self).log_deletion(request, object, object_repr)
        log_defaults = {
            'event_id' : 587300,
            'event_data': '%s (%d) deleted by %s' % (object._meta.object_name, 
                                                    object.pk, request.user),
            'description': '%s deleted' % object._meta.object_name,
            'user': request.user,
            'request': request,
            'instance': object,
        }
        EventLog.objects.log(**log_defaults)
    
    def log_change(self, request, object, message):
        super(FormAdmin, self).log_change(request, object, message)
        log_defaults = {
            'event_id' : 587200,
            'event_data': '%s (%d) edited by %s' % (object._meta.object_name, 
                                                    object.pk, request.user),
            'description': '%s edited' % object._meta.object_name,
            'user': request.user,
            'request': request,
            'instance': object,
        }
        EventLog.objects.log(**log_defaults)
    
    def log_addition(self, request, object):
        super(FormAdmin, self).log_addition(request, object)
        log_defaults = {
            'event_id' : 587100,
            'event_data': '%s (%d) added by %s' % (object._meta.object_name, 
                                                   object.pk, request.user),
            'description': '%s added' % object._meta.object_name,
            'user': request.user,
            'request': request,
            'instance': object,
        }
        EventLog.objects.log(**log_defaults)
    
    def save_model(self, request, object, form, change):
        instance = form.save(commit=False)
        
        instance = update_perms_and_save(request, form, instance)
        
        return instance
    
    def change_view(self, request, object_id, extra_context=None):
        result = super(FormAdmin, self).change_view(request, object_id, extra_context)

        if not request.POST.has_key('_addanother') and not request.POST.has_key('_continue') and request.GET.has_key('next'):
            result['Location'] = iri_to_uri("%s") % request.GET.get('next')
        return result

admin.site.register(Form, FormAdmin)

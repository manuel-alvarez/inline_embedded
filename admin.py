# embedded/admin.py
from django.contrib import admin
from django.contrib.admin.util import unquote, flatten_fieldsets
from django.db import transaction
from django.forms.formsets import all_valid
from django.forms.models import (ModelForm, BaseModelFormSet,
	modelformset_factory)
from django.utils.decorators import method_decorator
from django.utils.encoding import force_unicode
from django.utils.functional import curry
from django.utils.safestring import mark_safe
from django.utils.translation import gettext_lazy as _
from django.views.decorators.csrf import csrf_protect

from embedded.helpers import EmbeddedAdminFormSet

csrf_protect_m = method_decorator(csrf_protect)

class BaseEmbeddedFormSet(BaseModelFormSet):
	def __init__(self, data=None, files=None, auto_id='id_%s', prefix=None,
				 queryset=None, instance=None, **kwargs):
		defaults = {'data': data, 'files': files, 'auto_id': auto_id, 'prefix': prefix, 'queryset': queryset}
		defaults.update(kwargs)
		
		super(BaseEmbeddedFormSet, self).__init__(**defaults)

	def _construct_form(self, i, **kwargs):
		if i < self.initial_form_count() and not kwargs.get('instance'):
			kwargs['instance'] = self.get_queryset()[i]
		defaults = {'auto_id': self.auto_id, 'prefix': self.add_prefix(i)}
		if self.is_bound:
			defaults['data'] = self.data
			defaults['files'] = self.files
		if self.initial:
			try:
				defaults['initial'] = self.initial[i]
			except IndexError:
				pass
		# Allow extra forms to be empty.
		if i >= self.initial_form_count():
			defaults['empty_permitted'] = True
		defaults.update(kwargs)
		form = self.form(**defaults)
		self.add_fields(form, i)
		return form

	def get_queryset(self):
		if not hasattr(self, '_queryset'):
			if self.queryset is not None:
				qs = self.queryset
			else:
				qs = self.model._default_manager.get_query_set()

			# Removed ordering here
			# If the queryset isn't already ordered we need to add an
			# artificial ordering here to make sure that all formsets
			# constructed from this queryset have the same form order.
			# if not qs.ordered:
				# qs = qs.order_by(self.model._meta.pk.name)

			# Removed queryset limiting here. As per discussion re: #13023
			# on django-dev, max_num should not prevent existing
			# related objects/inlines from being displayed.
			self._queryset = qs
		return self._queryset

	def save_embedded(self):
		"""
			Replace BaseModelFormSet.save_new_objects for embedded formsets
			Don't save anything in the db, but create a new list of objects to insert in the new object
			It is assumed that the data have been validated. Elsewhere the method returns a void list
		"""			
		self.changed_objects = []
		self.deleted_objects = []
		self.new_objects = []
		
		output_objects = []
		
		if not self.is_valid():
			return []
			
		for form in self.initial_forms:
			embedded_instance = form.save(commit=False)
			if self.can_delete == True and form._raw_value("DELETE"):
				self.deleted_objects.append(embedded_instance)
			else:
				output_objects.append(embedded_instance)
				if form.has_changed():
					self.changed_objects.append((embedded_instance, form.changed_data))
			
		for form in self.extra_forms:
			if form.has_changed() and not (self.can_delete and form._raw_value("DELETE")): # it has new data and has not been marked for deletion
				embedded_instance = form.save(commit=False)
				self.new_objects.append(embedded_instance)
				output_objects.append(embedded_instance)
		
		return output_objects
		
def embeddedformset_factory(parent_model, model, form=ModelForm,
						  formset=BaseModelFormSet, fk_name=None,
						  fields=None, exclude=None,
						  extra=3, can_order=False, can_delete=True, max_num=None,
						  formfield_callback=None):
	"""
	Returns an ``InlineFormSet`` for the given kwargs.
	"""
	kwargs = {
		'form': form,
		'formfield_callback': formfield_callback,
		'formset': formset,
		'extra': extra,
		'can_delete': can_delete,
		'can_order': can_order,
		'fields': fields,
		'exclude': exclude,
		'max_num': max_num,
	}
	FormSet = modelformset_factory(model, **kwargs)
	return FormSet

class EmbeddedModelAdmin(admin.TabularInline):
	def queryset(self, request, parent_instance=None):
		"""
		Returns a QuerySet of all model instances that can be edited by the
		admin site. This is used by changelist_view.
		"""
		if parent_instance:
			qs = getattr(parent_instance, self.model._meta.object_name.lower())
		else:
			qs = self.model._default_manager.get_query_set()

		return qs

	def get_formset(self, request, obj=None, **kwargs):
		if self.declared_fieldsets:
			fields = flatten_fieldsets(self.declared_fieldsets)
		else:
			fields = None
		if self.exclude is None:
			exclude = []
		else:
			exclude = list(self.exclude)
		exclude.extend(kwargs.get("exclude", []))
		exclude.extend(self.get_readonly_fields(request, obj))
		# if exclude is an empty list we use None, since that's the actual
		# default
		exclude = exclude or None
		defaults = {
			"form": self.form,
			"formset": self.formset,
			"fields": fields,
			"exclude": exclude,
			"formfield_callback": curry(self.formfield_for_dbfield, request=request),
			"extra": self.extra,
			"max_num": self.max_num,
			"can_delete": self.can_delete,
		}
		defaults.update(kwargs)
		return embeddedformset_factory(self.parent_model, self.model, **defaults)

class TabularEmbedded(EmbeddedModelAdmin):
	template = 'admin/edit_inline/tabular_embedded.html'
	
class StackedEmbedded(EmbeddedModelAdmin):
	template = 'admin/edit_inline/stacked_embedded.html'
	
class EmbeddedAdmin(admin.ModelAdmin):
	embedded = []
	
	def __init__(self, model, admin_site):
		super(EmbeddedAdmin, self).__init__(model, admin_site)
		self.embedded_instances = []
		for embedded_class in self.embedded:
			embedded_instance = embedded_class(self.model, self.admin_site)
			self.embedded_instances.append(embedded_instance)		
		
	def get_formsets(self, request, obj=None):
		for inline in self.inline_instances:
			yield inline.get_formset(request, obj)
			
	def get_embedded_formsets(self, request, obj=None):
		for embedded in self.embedded_instances:
			yield embedded.get_formset(request, obj)

	@csrf_protect_m
	@transaction.commit_on_success
	def add_view(self, request, form_url='', extra_context=None):
		"The 'add' admin view for this model."
		model = self.model
		opts = model._meta
		
		if not self.has_add_permission(request):
			raise PermissionDenied

		ModelForm = self.get_form(request)
		formsets = []
		if request.method == 'POST':
			form = ModelForm(request.POST, request.FILES)
			if form.is_valid():
				new_object = self.save_form(request, form, change=False)
				form_validated = True
			else:
				form_validated = False
				new_object = self.model()
			prefixes = {}
			for FormSet, inline in zip(self.get_formsets(request), self.inline_instances):
				prefix = FormSet.get_default_prefix()
				prefixes[prefix] = prefixes.get(prefix, 0) + 1
				if prefixes[prefix] != 1:
					prefix = "%s-%s" % (prefix, prefixes[prefix])
				formset = FormSet(data=request.POST, files=request.FILES,
								  instance=new_object,
								  save_as_new="_saveasnew" in request.POST,
								  prefix=prefix, queryset=inline.queryset(request))
				formsets.append(formset)

			embedded_formsets = []
			for FormSet, embedded in zip(self.get_embedded_formsets(request, new_object),
									   self.embedded_instances):
				prefix = FormSet.model._meta.object_name.lower()
				prefixes[prefix] = prefixes.get(prefix, 0) + 1
				if prefixes[prefix] != 1:
					prefix = "%s-%s" % (prefix, prefixes[prefix])
				try:
					formset = FormSet(request.POST, request.FILES, prefix=prefix,
									  instance=embedded.model,
									  queryset=embedded.queryset(request))
					embedded_formsets.append(formset) # this is only to get a complete log messaging
				except:
					pass

			if all_valid(formsets) and all_valid(embedded_formsets) and form_validated:
				# we have to update embedded formsets first because we modify the new_object
				for formset in embedded_formsets:
					embedded_data = self.save_embedded_formset(formset, change=False)
					setattr(new_object, formset.model._meta.object_name.lower(), embedded_data)
				self.save_model(request, new_object, form, change=False)
				form.save_m2m()
				for formset in formsets:
					self.save_formset(request, form, formset, change=False)

				self.log_addition(request, new_object)
				return self.response_add(request, new_object)
		else:
			# Prepare the dict of initial data from the request.
			# We have to special-case M2Ms as a list of comma-separated PKs.
			initial = dict(request.GET.items())
			for k in initial:
				try:
					f = opts.get_field(k)
				except models.FieldDoesNotExist:
					continue
				if isinstance(f, models.ManyToManyField):
					initial[k] = initial[k].split(",")
			form = ModelForm(initial=initial)
			prefixes = {}
			for FormSet, inline in zip(self.get_formsets(request),
									   self.inline_instances):
				prefix = FormSet.get_default_prefix()
				prefixes[prefix] = prefixes.get(prefix, 0) + 1
				if prefixes[prefix] != 1:
					prefix = "%s-%s" % (prefix, prefixes[prefix])
				formset = FormSet(instance=self.model(), prefix=prefix,
								  queryset=inline.queryset(request))
				formsets.append(formset)

			for FormSet, embedded in zip(self.get_embedded_formsets(request, ), self.embedded_instances):
				prefix = FormSet.model._meta.object_name.lower()
				prefixes[prefix] = prefixes.get(prefix, 0) + 1
				if prefixes[prefix] != 1:
					prefix = "%s-%s" % (prefix, prefixes[prefix])
				formset = FormSet(instance=embedded.model, prefix=prefix,
								  queryset=embedded.queryset(request))
				formsets.append(formset)
				
		adminForm = admin.helpers.AdminForm(form, list(self.get_fieldsets(request)),
			self.prepopulated_fields, self.get_readonly_fields(request),
			model_admin=self)
		media = self.media + adminForm.media

		inline_admin_formsets = []
		for inline, formset in zip(self.inline_instances, formsets):
			fieldsets = list(inline.get_fieldsets(request))
			readonly = list(inline.get_readonly_fields(request))
			inline_admin_formset = admin.helpers.InlineAdminFormSet(inline, formset,
				fieldsets, readonly, model_admin=self)
			inline_admin_formsets.append(inline_admin_formset)
			media = media + inline_admin_formset.media

		embedded_admin_formsets=[]
		for embedded, formset in zip(self.embedded_instances, formsets):
			fieldsets = list(embedded.get_fieldsets(request))
			readonly = list(embedded.get_readonly_fields(request))
			embedded_admin_formset = EmbeddedAdminFormSet(embedded, formset,
				fieldsets, readonly, model_admin=self)
			embedded_admin_formsets.append(embedded_admin_formset)
			media = media + embedded_admin_formset.media

		context = {
			'title': _('Add %s') % force_unicode(opts.verbose_name),
			'adminform': adminForm,
			'is_popup': "_popup" in request.REQUEST,
			'show_delete': False,
			'media': mark_safe(media),
			'inline_admin_formsets': inline_admin_formsets,
			'embedded_admin_formsets': embedded_admin_formsets,
			'errors': admin.helpers.AdminErrorList(form, formsets),
			'root_path': self.admin_site.root_path,
			'app_label': opts.app_label,
		}
		context.update(extra_context or {})
		return self.render_change_form(request, context, form_url=form_url, add=True)

	@csrf_protect_m
	@transaction.commit_on_success
	def change_view(self, request, object_id, extra_context=None):
		"""Add a hidden field for the object's primary key."""
		model = self.model
		opts = model._meta

		obj = self.get_object(request, unquote(object_id))

		if not self.has_change_permission(request, obj):
			raise PermissionDenied

		if obj is None:
			raise Http404(_('%(name)s object with primary key %(key)r does not exist.') % {'name': force_unicode(opts.verbose_name), 'key': escape(object_id)})

		if request.method == 'POST' and "_saveasnew" in request.POST:
			return self.add_view(request, form_url='../add/')

		ModelForm = self.get_form(request, obj)
		formsets = []
		if request.method == 'POST':
			form = ModelForm(request.POST, request.FILES, instance=obj)
			if form.is_valid():
				form_validated = True
				new_object = self.save_form(request, form, change=True)
			else:
				form_validated = False
				new_object = obj
			prefixes = {}
			for FormSet, inline in zip(self.get_formsets(request, new_object),
									   self.inline_instances):
				prefix = FormSet.get_default_prefix()
				prefixes[prefix] = prefixes.get(prefix, 0) + 1
				if prefixes[prefix] != 1:
					prefix = "%s-%s" % (prefix, prefixes[prefix])
				formset = FormSet(request.POST, request.FILES,
								  instance=new_object, prefix=prefix,
								  queryset=inline.queryset(request))

				formsets.append(formset)

			embedded_formsets=[]
			for FormSet, embedded in zip(self.get_embedded_formsets(request, new_object),
									   self.embedded_instances):
				prefix = FormSet.model._meta.object_name.lower()
				prefixes[prefix] = prefixes.get(prefix, 0) + 1
				if prefixes[prefix] != 1:
					prefix = "%s-%s" % (prefix, prefixes[prefix])
				try:
					formset = FormSet(request.POST, request.FILES, prefix=prefix,
									  queryset=embedded.queryset(request, obj))

					embedded_formsets.append(formset) # this is only to get a complete history messaging
				except:
					pass
				
			if all_valid(formsets) and all_valid(embedded_formsets) and form_validated:
				# we have to update embedded formsets first because we modify the new_object
				for formset in embedded_formsets:
					embedded_data = self.save_embedded_formset(formset, change=True)
					setattr(new_object, formset.model._meta.object_name.lower(), embedded_data)				
				self.save_model(request, new_object, form, change=True)
				form.save_m2m()
				for formset in formsets:
					self.save_formset(request, form, formset, change=True)
					
				change_message = self.construct_change_message(request, form, formsets+embedded_formsets)
				self.log_change(request, new_object, change_message)
				return self.response_change(request, new_object)

		else:
			form = ModelForm(instance=obj)
			prefixes = {}
			for FormSet, inline in zip(self.get_formsets(request, obj), self.inline_instances):
				prefix = FormSet.get_default_prefix()
				prefixes[prefix] = prefixes.get(prefix, 0) + 1
				if prefixes[prefix] != 1:
					prefix = "%s-%s" % (prefix, prefixes[prefix])
				formset = FormSet(instance=obj, prefix=prefix,
								  queryset=inline.queryset(request))
				formsets.append(formset)
			
			for FormSet, embedded in zip(self.get_embedded_formsets(request, obj), self.embedded_instances):
				prefix = FormSet.model._meta.object_name.lower()
				prefixes[prefix] = prefixes.get(prefix, 0) + 1
				if prefixes[prefix] != 1:
					prefix = "%s-%s" % (prefix, prefixes[prefix])
				formset = FormSet(instance=embedded.model, prefix=prefix,
								  queryset=embedded.queryset(request, obj))
				formsets.append(formset)
				
		adminForm = admin.helpers.AdminForm(form, self.get_fieldsets(request, obj),
			self.prepopulated_fields, self.get_readonly_fields(request, obj),
			model_admin=self)
		media = self.media + adminForm.media

		inline_admin_formsets = []
		for inline, formset in zip(self.inline_instances, formsets):
			fieldsets = list(inline.get_fieldsets(request, obj))
			readonly = list(inline.get_readonly_fields(request, obj))
			inline_admin_formset = admin.helpers.InlineAdminFormSet(inline, formset,
				fieldsets, readonly, model_admin=self)
			inline_admin_formsets.append(inline_admin_formset)
			media = media + inline_admin_formset.media
			
		embedded_admin_formsets=[]
		for embedded, formset in zip(self.embedded_instances, formsets):
			fieldsets = list(embedded.get_fieldsets(request, obj))
			readonly = list(embedded.get_readonly_fields(request, obj))
			embedded_admin_formset = EmbeddedAdminFormSet(embedded, formset,
				fieldsets, readonly, model_admin=self)
			embedded_admin_formsets.append(embedded_admin_formset)
			media = media + embedded_admin_formset.media

		context = {
			'title': _('Change %s') % force_unicode(opts.verbose_name),
			'adminform': adminForm,
			'object_id': object_id,
			'original': obj,
			'is_popup': "_popup" in request.REQUEST,
			'media': mark_safe(media),
			'inline_admin_formsets': inline_admin_formsets,
			'embedded_admin_formsets': embedded_admin_formsets,
			'errors': admin.helpers.AdminErrorList(form, formsets),
			'root_path': self.admin_site.root_path,
			'app_label': opts.app_label,
		}
		
		context.update(extra_context or {})
		return self.render_change_form(request, context, change=True, obj=obj)

	def save_embedded_formset(self, formset, change):
		"""
			Replace BaseModelFormSet.save_new_objects for embedded formsets
			Don't save anything in the db, but create a new list of objects to insert in the new object
		"""
		from data.models import Address, Phone, Mail, Web
			
		formset.changed_objects = []
		formset.deleted_objects = []
		formset.new_objects = []
		
		output_objects = []
		
		for form in formset.initial_forms:
			embedded_instance = form.save(commit=False)
			if formset.can_delete == True and form._raw_value("DELETE"):
				formset.deleted_objects.append(embedded_instance)
			else:
				output_objects.append(embedded_instance)
				if form.has_changed():
					formset.changed_objects.append((embedded_instance, form.changed_data))
			
		for form in formset.extra_forms:
			if form.has_changed() and not (formset.can_delete and form._raw_value("DELETE")): # it has new data and has not been marked for deletion
				embedded_instance = form.save(commit=False)
				formset.new_objects.append(embedded_instance)
				output_objects.append(embedded_instance)
		
		return output_objects
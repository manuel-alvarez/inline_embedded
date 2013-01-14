# embedded/helpers.py
from django.contrib import admin
from django.contrib.admin.util import (flatten_fieldsets, label_for_field)

class EmbeddedAdminFormSet(admin.helpers.InlineAdminFormSet):
	"""
	A wrapper around an embedded formset for use in the admin system.
	"""
	def fields(self):
		for i, field in enumerate(flatten_fieldsets(self.fieldsets)):
			if field in self.readonly_fields:
				yield {
					'label': label_for_field(field, self.opts.model, self.opts),
					'widget': {
						'is_hidden': False
					},
					'required': False
				}
			else:
				yield self.formset.form.base_fields[field]

class EmbeddedAdminForm(admin.helpers.InlineAdminForm):
	"""
	A wrapper around an embedded form for use in the admin system.
	"""
	def __init__(self, formset, form, fieldsets, prepopulated_fields, original,
	  readonly_fields=None, model_admin=None):
		self.formset = formset
		self.model_admin = model_admin
		self.original = original
		if original is not None:
			self.original_content_type_id = ContentType.objects.get_for_model(original).pk
		self.show_url = original and hasattr(original, 'get_absolute_url')
		super(EmbeddedAdminForm, self).__init__(form, fieldsets, prepopulated_fields,
			readonly_fields, model_admin)

	def __iter__(self):
		for name, options in self.fieldsets:
			yield EmbeddedFieldset(self.formset, self.form, name,
				self.readonly_fields, model_admin=self.model_admin, **options)

	def has_auto_field(self):
		if self.form._meta.model._meta.has_auto_field:
			return True
		# Also search any parents for an auto field.
		for parent in self.form._meta.model._meta.get_parent_list():
			if parent._meta.has_auto_field:
				return True
		return False

	def pk_field(self):
		return AdminField(self.form, self.formset._pk_field.name, False)

	def deletion_field(self):
		from django.forms.formsets import DELETION_FIELD_NAME
		return AdminField(self.form, DELETION_FIELD_NAME, False)

	def ordering_field(self):
		from django.forms.formsets import ORDERING_FIELD_NAME
		return AdminField(self.form, ORDERING_FIELD_NAME, False)

class EmbeddedFieldset(admin.helpers.InlineFieldset):
	def __init__(self, formset, *args, **kwargs):
		self.formset = formset
		super(EmbeddedFieldset, self).__init__(*args, **kwargs)

	def __iter__(self):
		for field in self.fields:
			yield Fieldline(self.form, field, self.readonly_fields,
				model_admin=self.model_admin)


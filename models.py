from django.db import models
from django.forms.models import modelformset_factory, ModelForm

from embedded.admin import BaseEmbeddedFormSet

class EmbeddedField():
	field = ''
	model = None
	form = ModelForm
	max_num = 12
	extra = 1
	can_delete = True
	verbose_name = ''
	
	def get_formset(self):
		FormSet = modelformset_factory(self.model, formset=BaseEmbeddedFormSet, form=self.form, max_num=self.max_num, extra=self.extra, can_delete=self.can_delete)
		FormSet.verbose_name = self.verbose_name
		return FormSet
		
	def get_queryset(self, object, field=None):
		if field is not None:
			self.field = field
		return getattr(object, self.field)
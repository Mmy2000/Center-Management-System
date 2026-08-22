from django import forms


class DefaultsOptionalModelForm(forms.ModelForm):
    """ModelForm where any field carrying a model-level default is optional.

    JSON clients (and PATCH in particular) send only what they mean to set;
    without this, ``status`` or ``capacity`` would be "required" purely because
    the model declares ``blank=False`` while also declaring a default.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            model_field = self._model_field(name)
            if model_field is not None and model_field.has_default():
                field.required = False

    def _model_field(self, name):
        try:
            return self._meta.model._meta.get_field(name)
        except Exception:
            return None

    def clean(self):
        cleaned = super().clean()
        for name in self.fields:
            model_field = self._model_field(name)
            if model_field is None or not model_field.has_default():
                continue
            if cleaned.get(name) in (None, ""):
                cleaned[name] = model_field.get_default()
        return cleaned

from django import forms
from .models import Chapter


class MultipleFileInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class MultipleFileField(forms.FileField):
    widget = MultipleFileInput

    def clean(self, data, initial=None):
        clean_single = super().clean
        if isinstance(data, (list, tuple)):
            result = [clean_single(item, initial) for item in data]
        else:
            result = clean_single(data, initial)
        return result


class MultiFileUploadForm(forms.Form):
    chapter = forms.ModelChoiceField(
        queryset=Chapter.objects.none(),
        widget=forms.Select(attrs={'class': 'form-select form-select-lg'}),
        label='Select Chapter',
    )
    files = MultipleFileField(
        widget=MultipleFileInput(attrs={
            'class': 'form-control form-control-lg',
            'accept': '.pdf,.docx',
            'multiple': True,
        }),
        label='Upload PDF or Word Files',
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['chapter'].queryset = Chapter.objects.all()

    def clean_files(self):
        files = self.files.getlist('files')
        if not files:
            raise forms.ValidationError('Please upload at least one PDF or DOCX file.')

        for file in files:
            name = file.name.lower()
            if not (name.endswith('.pdf') or name.endswith('.docx')):
                raise forms.ValidationError('Only PDF (.pdf) and Word (.docx) files are supported.')
            if file.size > 10 * 1024 * 1024:
                raise forms.ValidationError('Each file must be under 10 MB.')
        return files

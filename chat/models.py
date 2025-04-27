from django.db import models

# Create your models here.

class SuggestionButton(models.Model):
    text = models.CharField(max_length=100)
    context = models.CharField(max_length=100, help_text="Контекст, в котором показывается кнопка")
    order = models.IntegerField(default=0)
    
    class Meta:
        ordering = ['order']
        
    def __str__(self):
        return self.text

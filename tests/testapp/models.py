from uuid import uuid4

from django.db import models


def synthetic_slug():
    return uuid4().hex


class Parent(models.Model):
    name = models.CharField(max_length=80)


class Widget(models.Model):
    slug = models.SlugField(unique=True, default=synthetic_slug)
    code = models.CharField(max_length=80, unique=True)
    name = models.CharField(max_length=80)
    value = models.IntegerField(default=0)
    visible = models.BooleanField(default=True)
    parent = models.ForeignKey(
        Parent, null=True, blank=True, on_delete=models.CASCADE, related_name="widgets"
    )

    class Meta:
        ordering = ("pk",)


class AuditEntry(models.Model):
    message = models.CharField(max_length=120)

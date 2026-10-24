from collections.abc import Iterable

from django import template

register = template.Library()


@register.filter(is_safe=True)
def is_list(value):
    return isinstance(value, Iterable)

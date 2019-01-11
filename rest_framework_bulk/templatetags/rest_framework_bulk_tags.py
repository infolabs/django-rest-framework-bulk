# -*- coding: utf-8 -*-
from __future__ import unicode_literals


import collections
from django import template


register = template.Library()


def is_list(value):
    return isinstance(value, collections.Iterable)

register.filter(is_list, is_safe=True)
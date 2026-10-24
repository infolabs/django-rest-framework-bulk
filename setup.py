"""Setuptools entry point; metadata must not import Django or package code."""
import re
from pathlib import Path

from setuptools import find_packages, setup

ROOT = Path(__file__).resolve().parent
version = re.search(r"^__version__ = '([^']+)'", (ROOT / 'rest_framework_bulk/_version.py').read_text(), re.M).group(1)

setup(
    name='djangorestframework-bulk',
    version=version,
    author='Miroslav Shubernetskiy',
    author_email='miroslav@miki725.com',
    description='Django REST Framework bulk CRUD view mixins (InfLabs)',
    long_description=(ROOT / 'README.rst').read_text(encoding='utf-8'),
    long_description_content_type='text/x-rst',
    url='https://github.com/infolabs/django-rest-framework-bulk',
    license='MIT',
    python_requires='>=3.12',
    packages=find_packages(include=['rest_framework_bulk', 'rest_framework_bulk.*'],
        exclude=['rest_framework_bulk.tests', 'rest_framework_bulk.tests.*', 'rest_framework_bulk.drf2', 'rest_framework_bulk.drf2.*']),
    package_data={'rest_framework_bulk': ['templates/rest_framework/horizontal/*.html']},
    install_requires=['Django>=5.2,<5.3', 'djangorestframework>=3.18,<3.19'],
    classifiers=[
        'Development Status :: 3 - Alpha',
        'Framework :: Django :: 5.2',
        'Intended Audience :: Developers',
        'Programming Language :: Python :: 3',
        'Programming Language :: Python :: 3.12',
    ],
)

#!/usr/bin/env python

from setuptools import setup

setup(
    name='qbatch',
    version='0.1',
    description='Execute shell command lines in parallel on SGE/PBS clusters',
    author="Jon Pipitone, Gabriel A. Devenyi",
    author_email="jon@pipitone.ca, gdevenyi@gmail.com",
    license='Unlicense',
    url="https://github.com/pipitone/qbatch",
    scripts=["bin/qbatch"],
    long_description=open('README.md').read(),
    setup_requires=['nose>=1.0'],
)

#!/usr/bin/env python

from setuptools import setup

setup(
    name='qbatch',
    version='0.1',
    description='A tool for submitting commands to SGE/PBS clusters',
    author="Jon Pipitone, Gabriel A. Devenyi",
    author_email="jon@pipitone.ca, gdevenyi@gmail.com",
    license='Unlicense',
    url="https://github.com/pipitone/qbatch",
    scripts=["bin/qbatch", "bin/pbs_jobnames"],
    long_description=open('README.md').read(),
    setup_requires=['nose>=1.0'],
)

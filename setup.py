#!/usr/bin/env python

from setuptools import setup

# pypi doesn't like markdown
# https://github.com/pypa/packaging-problems/issues/46
try:
    import pypandoc
    description = pypandoc.convert('README.md', 'rst')
except (IOError, ImportError):
    description = ''

setup(
    name='qbatch',
    version='1.0rc2',
    description='Execute shell command lines in parallel on SGE/PBS clusters',
    author="Jon Pipitone, Gabriel A. Devenyi",
    author_email="jon@pipitone.ca, gdevenyi@gmail.com",
    license='Unlicense',
    url="https://github.com/pipitone/qbatch",
    long_description=description,
    scripts=["bin/qbatch"],
    setup_requires=['nose>=1.0'],
    classifiers=[
       'Development Status :: 4 - Beta',
       'Environment :: Console',
       'Intended Audience :: Science/Research',
       'License :: Public Domain',
       'Natural Language :: English',
       'Operating System :: POSIX :: Linux',
       'Programming Language :: Python :: 2.6',
       'Programming Language :: Python :: 2.7',
       'Programming Language :: Python :: 2 :: Only',
       'Topic :: System :: Clustering',
       'Topic :: System :: Distributed Computing',
       'Topic :: Utilities',
    ],
)

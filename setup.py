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
    version='2.1.5',
    description='Execute shell command lines in parallel on Slurm, S(sun|on of) Grid Engine (SGE) and PBS/Torque clusters',
    author="Jon Pipitone, Gabriel A. Devenyi",
    author_email="jon@pipitone.ca, gdevenyi@gmail.com",
    license='Unlicense',
    url="https://github.com/pipitone/qbatch",
    long_description=description,
    entry_points = {
        "console_scripts": [
            "qbatch=qbatch:qbatchParser",
        ]
    },
    packages=["qbatch"],
    classifiers=[
       'Development Status :: 5 - Production/Stable',
       'Environment :: Console',
       'Intended Audience :: Science/Research',
       'License :: Public Domain',
       'Natural Language :: English',
       'Operating System :: POSIX :: Linux',
       'Programming Language :: Python :: 2',
       'Programming Language :: Python :: 3',
       'Topic :: System :: Clustering',
       'Topic :: System :: Distributed Computing',
       'Topic :: Utilities',
    ],
    install_requires=[
        "six",
        "future",
    ],
)

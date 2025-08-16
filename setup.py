#!/usr/bin/env python

from setuptools import setup
from io import open

# read the contents of your README file
from os import path
this_directory = path.abspath(path.dirname(__file__))
with open(path.join(this_directory, 'README.md'), encoding='utf-8') as f:
    long_description = f.read()

setup(
    name='qbatch',
    version='2.3.1',
    description='Execute shell command lines in parallel on Slurm, '
    'S(un|on of) Grid Engine (SGE) and PBS/Torque clusters',
    author="Jon Pipitone, Gabriel A. Devenyi",
    author_email="jon@pipitone.ca, gdevenyi@gmail.com",
    license='Unlicense',
    url="https://github.com/pipitone/qbatch",
    long_description=long_description,
    long_description_content_type='text/markdown',
    entry_points={
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
        "future",
    ],
)

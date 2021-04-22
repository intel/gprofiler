#!/bin/bash
set -e

flake8 --config .flake8 .
black . --line-length 120 --check
mypy .

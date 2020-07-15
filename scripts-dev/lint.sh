#!/bin/sh
#
# Runs linting scripts over the local Sygnal checkout
# isort - sorts import statements
# black - opinionated code formatter
# flake8 - lints and finds mistakes
# mypy - checks types

set -e

if [ $# -ge 1 ]
then
  files=$*
else
  files="sygnal tests"
fi

echo "Linting these locations: $files"
echo " ===== Running isort ===== "
isort $files
echo " ===== Running black ===== "
black $files
echo " ===== Running flake8 ===== "
flake8 $files
echo " ===== Running mypy ===== "
mypy $files

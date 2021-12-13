#!/usr/local/bin/python
#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
import os
from threading import Thread

import yaml  # type: ignore


def lister():
    os.listdir("/")  # have some kernel stacks


def burner():
    while True:  # have some Python stacks
        pass


def parser():
    while True:
        # Have some package stacks.
        # Notice the name of the package (PyYAML) is different from the name of the module (yaml)
        yaml.parse("")


if __name__ == "__main__":
    Thread(target=burner).start()
    Thread(target=parser).start()
    while True:
        lister()
